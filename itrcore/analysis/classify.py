"""Analysis orchestrator: pure function over transaction rows.

Order: counterparty/mode → self-transfers → reversals → noise → keyword
rules → recurrence (salary/rent) → flags. Returns per-transaction updates
plus the ExplanationQueries to raise. Never touches the DB itself.
"""

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from itrcore.analysis import narration as narration_mod
from itrcore.analysis.recurrence import find_series
from itrcore.analysis.self_transfer import pair_reversals, pair_self_transfers

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@lru_cache(maxsize=1)
def load_thresholds() -> dict:
    return yaml.safe_load(
        (_CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_rules() -> tuple:
    raw = yaml.safe_load((_CONFIG_DIR / "classification_rules.yaml")
                         .read_text(encoding="utf-8"))
    return tuple(raw["rules"])


@dataclass
class TxnUpdate:
    txn_id: int
    mode: str | None = None
    counterparty_key: str = ""
    counterparty_display: str = ""
    auto_category: str | None = None
    flag_reason: str | None = None


@dataclass
class QueryDraft:
    topic: str
    question: str
    transaction_id: int | None = None


@dataclass
class AnalysisOutcome:
    updates: list[TxnUpdate] = field(default_factory=list)
    queries: list[QueryDraft] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def analyze(txns: list[dict], *, employer_names: list[str],
            fy_end: date) -> AnalysisOutcome:
    """txns: dicts with id, account_id, txn_date (date), narration,
    amount_paise."""
    thresholds = load_thresholds()
    rules = load_rules()
    out = AnalysisOutcome()

    enriched = []
    for t in txns:
        mode, key, display = narration_mod.parse(t["narration"])
        enriched.append({**t, "mode": mode, "counterparty_key": key,
                         "counterparty_display": display})

    self_ids = pair_self_transfers(
        enriched, thresholds["self_transfer"]["date_window_days"])
    reversal_ids = pair_reversals(
        [t for t in enriched if t["id"] not in self_ids],
        thresholds["reversal"]["date_window_days"])

    noise_kws = [k.upper() for k in thresholds["noise_keywords"]]
    flags = thresholds["flags"]
    categorized: dict[int, TxnUpdate] = {}

    for t in enriched:
        upd = TxnUpdate(txn_id=t["id"], mode=t["mode"],
                        counterparty_key=t["counterparty_key"],
                        counterparty_display=t["counterparty_display"])
        categorized[t["id"]] = upd
        narr = t["narration"].upper()
        amount = t["amount_paise"]

        if t["id"] in self_ids:
            upd.auto_category = "non_income:self_transfer"
            continue
        if t["id"] in reversal_ids:
            upd.auto_category = "noise"
            upd.flag_reason = "reversal pair"
            continue
        if any(kw in narr for kw in noise_kws) and \
                abs(amount) <= flags["small_noise_inr"] * 100:
            upd.auto_category = "noise"
            continue

        rule_hit = None
        for rule in rules:
            direction = rule.get("direction", "any")
            if direction == "credit" and amount <= 0:
                continue
            if direction == "debit" and amount >= 0:
                continue
            if any(kw.upper() in narr for kw in rule["match"]):
                rule_hit = rule
                break
        if rule_hit:
            category = rule_hit["category"]
            if category.startswith("flag:"):
                kind = category.split(":", 1)[1]
                if (kind == "cash_deposit"
                        and amount >= flags["cash_deposit_inr"] * 100):
                    upd.flag_reason = "cash deposit"
                    out.queries.append(QueryDraft(
                        topic=f"txn:{t['id']}",
                        transaction_id=t["id"],
                        question=(f"A cash deposit of ₹{amount / 100:,.0f} "
                                  f"was made on {t['txn_date']:%d-%m-%Y}. "
                                  "Please explain the source of this "
                                  "cash.")))
                else:
                    upd.auto_category = "non_income:personal"
                continue
            upd.auto_category = category
            continue

    # Recurrence also runs over keyword-tagged salary/rent credits so the
    # series-level questions (missing month, tenant vacated) still fire.
    series_input = [t for t in enriched
                    if categorized[t["id"]].auto_category
                    in (None, "income:salary", "income:house_property")
                    and t["id"] not in self_ids]
    for series in find_series(series_input, thresholds, employer_names,
                              fy_end):
        member_cats = {categorized[tid].auto_category
                       for tid in series.txn_ids}
        if series.kind == "salary":
            for tid in series.txn_ids:
                categorized[tid].auto_category = "income:salary"
            if series.missing_months:
                out.queries.append(QueryDraft(
                    topic=f"series:{series.key}:gap",
                    question=(f"Salary credits from {series.display} skip "
                              f"{', '.join(series.missing_months)} — was "
                              "salary received in cash/another account "
                              "for those months?")))
        elif series.kind == "rent_candidate":
            if "income:salary" in member_cats:
                continue                    # keyword already says salary
            known_rent = member_cats == {"income:house_property"}
            for tid in series.txn_ids:
                if categorized[tid].auto_category is None:
                    categorized[tid].auto_category = "income:house_property"
                    categorized[tid].flag_reason = ("recurring credit — "
                                                    "confirm")
            if not known_rent:
                out.queries.append(QueryDraft(
                    topic=f"series:{series.key}",
                    question=(f"We see ~monthly credits of about "
                              f"₹{series.median_amount / 100:,.0f} from "
                              f"{series.display} "
                              f"({len(series.txn_ids)} times). Is this "
                              "rent? If yes, what is the agreed monthly "
                              "rent and the property address? If not, "
                              "what is it?")))
            if series.missing_months:
                out.queries.append(QueryDraft(
                    topic=f"series:{series.key}:gap",
                    question=(f"The credits from {series.display} skip "
                              f"{', '.join(series.missing_months)} — was "
                              "any amount received in cash for those "
                              "months?")))
            if series.ended_early:
                out.queries.append(QueryDraft(
                    topic=f"series:{series.key}:end",
                    question=(f"Credits from {series.display} stop after "
                              f"{series.dates[-1]:%b %Y}. Did the tenant "
                              "vacate / arrangement end? Please confirm "
                              "the date.")))

    for t in enriched:
        upd = categorized[t["id"]]
        if upd.auto_category is not None or upd.flag_reason:
            continue
        amount = t["amount_paise"]
        if amount >= flags["large_credit_inr"] * 100:
            upd.flag_reason = "large unexplained credit"
            gift_floor = flags["gift_like_min_inr"] * 100
            hint = (" If this is a gift, please give the donor's name and "
                    "your relationship (Section 56(2)(x))."
                    if amount >= gift_floor else "")
            out.queries.append(QueryDraft(
                topic=f"txn:{t['id']}", transaction_id=t["id"],
                question=(f"Please identify the credit of "
                          f"₹{amount / 100:,.0f} on "
                          f"{t['txn_date']:%d-%m-%Y} from "
                          f"{upd.counterparty_display or 'unknown'} "
                          f"({t['narration'][:80]}). Was this a sale of "
                          "an asset, a gift, a loan, or something else?"
                          + hint)))
        elif amount < 0:
            upd.auto_category = "non_income:personal"

    out.updates = list(categorized.values())
    auto = sum(1 for u in out.updates if u.auto_category)
    out.stats = {"total": len(out.updates), "auto_categorized": auto,
                 "flagged": sum(1 for u in out.updates if u.flag_reason),
                 "queries": len(out.queries)}
    return out
