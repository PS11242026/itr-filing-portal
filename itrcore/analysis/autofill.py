"""Assemble a draft computation from imported feeds (the "Genius" step).

Reads a taxpayer's imported Form 16, Form 26AS and AIS for an AY, plus last
year's final computation, and produces the computation ``inputs_dict`` — the
same shape the Computation page saves and ``tax.serde.inputs_from_dict``
consumes — together with provenance for every figure and follow-ups the
imports cannot settle by themselves.

Design notes:
- Pure read/compute: this never writes. The page persists the result through
  ``comp_repo.save_inputs`` so the existing audit trail applies.
- Form 26AS is authoritative for TDS. Form 16's section-192 TDS also appears
  in 26AS, so it is only used when no 26AS has been imported — otherwise
  salary TDS would be double counted.
- Salary fills even without a Form 16: 26AS section-192 rows carry the
  employer-wise 'amount paid' (salary/pension credited); failing that, the
  AIS aggregate seeds one row. Both raise refine-with-Form-16 follow-ups.
- AIS carries totals, not the cost/date detail capital gains and house
  property need, so those raise follow-ups instead of fabricating rows.
- Amounts arrive in paise (ingestion) and leave in whole rupees (tax engine),
  floored the same way the reconciler floors them.
"""

import json
import re
from dataclasses import dataclass

from itrcore.analysis.classify import load_thresholds
from itrcore.authz import TaxpayerScope
from itrcore.db.repos import computations as comp_repo
from itrcore.db.repos import importers as importers_repo
from itrcore.ingest import ais as ais_mod
from itrcore.ingest import cg_excel

_CATEGORY_LABELS = {
    "interest_savings": "Savings interest",
    "interest_deposits": "Deposit interest",
    "interest_others": "Interest — others (incl. I-T refund)",
    "dividend": "Dividend",
    "rent": "Rent received",
    "salary": "Salary",
    "family_pension": "Family pension",
    "other_income": "Other income (AIS)",
    "sale_securities": "Securities sale",
    "sale_immovable": "Immovable-property sale",
}
_INTEREST_CATEGORIES = ("interest_savings", "interest_deposits", "dividend")
# AIS "other income" tab categories that land in the OS 'other' field.
_OTHER_FIELD_CATEGORIES = ("interest_others", "other_income")

# Adjustment target prefix → head label for the classified summary.
_ADJ_HEADS = (("other_sources.", "Other sources"), ("salary", "Salary"),
              ("house_property", "House property"), ("business", "Business"),
              ("tds", "TDS"), ("tcs", "TDS"))


@dataclass(frozen=True)
class SourceNote:
    """One filled figure and where it came from (shown to staff)."""

    head: str
    detail: str
    amount_inr: int
    origin: str


@dataclass(frozen=True)
class AssembledInputs:
    inputs: dict                        # JSON-safe, ready for save_inputs
    sources: tuple[SourceNote, ...]
    follow_ups: tuple[str, ...]
    filled_heads: tuple[str, ...]       # sections this overwrote


def _r(paise) -> int:
    """Paise → whole rupees, floored (matches recon)."""
    return int(paise or 0) // 100


def _employer_key(tan, name) -> str:
    """Merge key for salary rows: TAN when present, else the name."""
    return (str(tan or "").strip().upper()
            or str(name or "").strip().casefold())


def _tds_key(entry: dict) -> tuple:
    """Merge key for TDS rows: TAN when present, else deductor+section."""
    tan = str(entry.get("tan") or "").strip().upper()
    if tan:
        return ("tan", tan)
    return ("name", str(entry.get("deductor") or "").strip().casefold(),
            str(entry.get("section") or "").strip())


_TAN_IN_SOURCE = re.compile(r"\(([A-Z]{4}\d{5}[A-Z])")


def _ais_salary_payers(scope: TaxpayerScope, ay: str) -> list[dict]:
    """Payer-wise salary from the saved AIS document: per payer the MAX
    across its B1 (TDS-192 'amount paid') and B7 (Annexure-II gross)
    blocks. Annexure-II is what catches retirement-year payouts —
    commuted pension / gratuity carry no TDS, so the 26AS 'amount paid'
    can understate gross salary by lakhs (Kishori: 26AS ₹4.7L vs
    Annexure-II ₹45.4L from the pension cell)."""
    saved = importers_repo.get_ais_document(scope, ay)
    if not saved:
        return []
    doc = ais_mod.document_from_dict(saved["doc"])
    best: dict[str, dict] = {}
    for r in doc.rows:
        if r.bucket != "salary" or r.amount_paise <= 0:
            continue
        key = ais_mod.norm_source(r.source)
        m = _TAN_IN_SOURCE.search(str(r.source or "").upper())
        cur = best.setdefault(key, {
            "name": str(r.source or "").split("(")[0].strip() or key,
            "tan": "", "gross": 0})
        cur["gross"] = max(cur["gross"], _r(r.amount_paise))
        if m and not cur["tan"]:
            cur["tan"] = m.group(1)
    return sorted(best.values(), key=lambda p: -p["gross"])


def _prior_ay(ay: str) -> str | None:
    try:
        start = int(str(ay).split("-")[0])
    except (ValueError, AttributeError, IndexError):
        return None
    return f"{start - 1}-{start % 100:02d}"


def _prior_carryforward(scope: TaxpayerScope,
                        prior_ay: str) -> dict | None:
    """Losses carried forward by last year's final (else latest) snapshot."""
    snaps = comp_repo.list_snapshots(scope, prior_ay)
    if not snaps:
        return None
    chosen = next((s for s in snaps if s["status"] == "final"), snaps[0])
    row = comp_repo.get_snapshot(scope, prior_ay, chosen["version_no"])
    try:
        result = json.loads(row["result_json"])
    except (TypeError, KeyError, json.JSONDecodeError):
        return None
    reg = result.get(result.get("recommended") or "new") \
        or result.get("new") or {}
    cg = (reg.get("heads") or {}).get("capital_gains") or {}
    return {"stcl": int(cg.get("cf_stcl", 0) or 0),
            "ltcl": int(cg.get("cf_ltcl", 0) or 0),
            "hp_loss": int(reg.get("hp_loss_carried_forward", 0) or 0)}


def assemble(scope: TaxpayerScope, ay: str, *,
             base: dict | None = None) -> AssembledInputs:
    """Build draft inputs from imports, merged onto ``base`` (existing saved
    inputs). Import-derived heads (salary, TDS, interest/dividend,
    brought-forward losses) overwrite; everything the imports can't derive
    (house property, capital gains, deductions, challans, other other-source
    lines) is preserved from ``base``."""
    base = base or {}
    sources: list[SourceNote] = []
    follow_ups: list[str] = []
    filled: list[str] = []

    # -- imported feeds fetched up-front (salary derivation needs them) --
    as26 = importers_repo.list_tds(scope, ay, source="26as")
    ais_totals: dict[str, int] = {}
    for r in importers_repo.list_ais(scope, ay):
        ais_totals[r["category"]] = (ais_totals.get(r["category"], 0)
                                     + r["amount_paise"])
    tis_totals: dict[str, int] = {}
    for r in importers_repo.list_tis(scope, ay):
        tis_totals[r["category"]] = (tis_totals.get(r["category"], 0)
                                     + r["derived_value_paise"])
    tol = load_thresholds()["recon"]["ais_tolerance_inr"]

    def _best(category: str):
        """(rupees, origin) preferring the ITD-reconciled TIS derived value,
        else the AIS aggregate; None when neither reports the category."""
        if category in tis_totals:
            return _r(tis_totals[category]), "TIS (derived)"
        if category in ais_totals:
            return _r(ais_totals[category]), "AIS"
        return None

    # -- Salary: Form 16 authoritative; else DERIVED from 26AS section-192
    # rows (amount paid = salary/pension credited, employer-wise), else the
    # AIS/TIS aggregate — the head fills even before Form 16 arrives.
    # Manually entered rows for other employers are always kept.
    f16 = importers_repo.list_form16(scope, ay)
    salaries = []
    f16_tds = []
    derived_keys = set()
    for f in f16:
        name = f["employer_name"] or "employer"
        salaries.append({
            "employer_name": f["employer_name"] or "",
            "employer_tan": f["employer_tan"] or "",
            "gross_salary": _r(f["gross_salary_paise"]),
            "exempt_allowances": _r(f["exempt_allowances_paise"]),
            "prof_tax": _r(f["prof_tax_paise"])})
        sources.append(SourceNote("Salary", name,
                                  _r(f["gross_salary_paise"]), "Form 16"))
        derived_keys.add(_employer_key(f["employer_tan"],
                                       f["employer_name"]))
        if f["tds_paise"]:
            f16_tds.append({"tan": f["employer_tan"] or "", "deductor": name,
                            "section": "192", "amount": _r(f["tds_paise"])})
    if not f16:
        rows_192 = [r for r in as26
                    if (r["section"] or "").strip() == "192"
                    and _r(r["amount_paid_paise"]) > 0]
        ais_payers = _ais_salary_payers(scope, ay)
        if ais_payers:
            # Payer-wise from the AIS salary statements (Annexure-II +
            # TDS-192), reconciled against the 26AS 'amount paid'.
            paid_by_key = {_employer_key(r["tan"], r["deductor_name"]): r
                           for r in rows_192}
            for p in ais_payers:
                r26 = paid_by_key.pop(_employer_key(p["tan"], p["name"]),
                                      None)
                paid26 = _r(r26["amount_paid_paise"]) if r26 else 0
                tan = p["tan"] or (r26["tan"] if r26 else "") or ""
                gross = max(p["gross"], paid26)
                salaries.append({
                    "employer_name": p["name"], "employer_tan": tan,
                    "gross_salary": gross, "exempt_allowances": 0,
                    "prof_tax": 0})
                sources.append(SourceNote(
                    "Salary", p["name"], gross,
                    "AIS salary statement (payer-wise)"))
                derived_keys.add(_employer_key(tan, p["name"]))
                derived_keys.add(_employer_key(p["tan"], p["name"]))
                if gross > paid26 + tol and paid26:
                    follow_ups.append(
                        f"{p['name']}: the AIS reports salary "
                        f"₹{gross:,} but 26AS shows TDS-linked payments "
                        f"of only ₹{paid26:,}. The ₹{gross - paid26:,} "
                        "balance is typically retirement benefits — "
                        "gratuity / commuted pension / leave encashment. "
                        "Enter the EXEMPT portion u/s 10(10)/10(10A)/"
                        "10(10AA) into the row's 'Exempt allowances' from "
                        "the PPO / Form 16 before filing.")
            for r in paid_by_key.values():     # 26AS-only employers
                name = r["deductor_name"] or r["tan"] or "employer"
                gross = _r(r["amount_paid_paise"])
                salaries.append({
                    "employer_name": name, "employer_tan": r["tan"] or "",
                    "gross_salary": gross, "exempt_allowances": 0,
                    "prof_tax": 0})
                sources.append(SourceNote(
                    "Salary", name, gross, "Form 26AS (192 amount paid)"))
                derived_keys.add(_employer_key(r["tan"], name))
            follow_ups.append(
                "Salary derived payer-wise from the AIS salary statements "
                "(Annexure-II / TDS-192) reconciled with 26AS — no "
                "exempt-allowance / professional-tax breakup yet. Import "
                "Form 16 to refine before filing.")
        elif rows_192:
            for r in rows_192:
                name = r["deductor_name"] or r["tan"] or "employer"
                gross = _r(r["amount_paid_paise"])
                salaries.append({
                    "employer_name": name, "employer_tan": r["tan"] or "",
                    "gross_salary": gross, "exempt_allowances": 0,
                    "prof_tax": 0})
                sources.append(SourceNote(
                    "Salary", name, gross, "Form 26AS (192 amount paid)"))
                derived_keys.add(_employer_key(r["tan"], name))
            follow_ups.append(
                "Salary rows derived from 26AS section-192 'amount paid' — "
                "26AS has no exempt-allowance / professional-tax breakup. "
                "Import Form 16 to refine before filing.")
            best_sal = _best("salary")
            total_192 = sum(s["gross_salary"] for s in salaries)
            if best_sal and abs(best_sal[0] - total_192) > tol:
                follow_ups.append(
                    f"AIS/TIS report salary ₹{best_sal[0]:,} but 26AS "
                    f"section-192 payments total ₹{total_192:,} — verify "
                    "against Form 16 / salary slips.")
        else:
            best_sal = _best("salary")
            if best_sal and best_sal[0] > 0:
                amount, origin = best_sal
                salaries.append({
                    "employer_name": "As per AIS", "employer_tan": "",
                    "gross_salary": amount, "exempt_allowances": 0,
                    "prof_tax": 0})
                sources.append(SourceNote("Salary", "As per AIS (aggregate)",
                                          amount, origin))
                derived_keys.add(_employer_key("", "As per AIS"))
                follow_ups.append(
                    "Salary taken from the AIS aggregate — import Form 16 "
                    "(or 26AS) for the employer-wise breakup with "
                    "exempt allowances.")
    base_salaries = [dict(s) for s in base.get("salaries", [])]
    if salaries:
        kept = [s for s in base_salaries
                if _employer_key(s.get("employer_tan"),
                                 s.get("employer_name"))
                not in derived_keys]
        for s in kept:
            sources.append(SourceNote(
                "Salary", s.get("employer_name") or "manual row",
                int(s.get("gross_salary", 0) or 0), "Manual entry (kept)"))
        salaries = salaries + kept
        filled.append("Salary")
    else:
        salaries = base_salaries
        for s in salaries:
            sources.append(SourceNote(
                "Salary", s.get("employer_name") or "manual row",
                int(s.get("gross_salary", 0) or 0), "Manual entry (kept)"))

    # -- TDS: 26AS authoritative, else Form 16 (192 only), MERGED with
    # manual rows whose deductor the imports do not cover ----------------
    tds = []
    if as26:
        for r in as26:
            who = r["deductor_name"] or r["tan"] or "deductor"
            tds.append({"tan": r["tan"] or "", "deductor": r["deductor_name"]
                        or "", "section": r["section"] or "",
                        "amount": _r(r["tds_paise"])})
            sources.append(SourceNote(
                "TDS", f"{who} ({r['section'] or '—'})",
                _r(r["tds_paise"]), "Form 26AS"))
        if f16_tds:
            follow_ups.append(
                "Form 16 salary TDS is already included in Form 26AS — "
                "using 26AS to avoid double counting.")
        filled.append("TDS")
    elif f16_tds:
        tds = f16_tds
        for t in f16_tds:
            sources.append(SourceNote("TDS", f"{t['deductor']} (192)",
                                      t["amount"], "Form 16"))
        follow_ups.append(
            "No Form 26AS imported — TDS taken from Form 16 (section 192) "
            "only. Import 26AS to capture non-salary TDS.")
        filled.append("TDS")
    if tds:                       # imported rows lead; merge unmatched manual
        covered = {_tds_key(t) for t in tds}
        for t in base.get("tds", []):
            if _tds_key(t) not in covered:
                tds.append(dict(t))
                sources.append(SourceNote(
                    "TDS", f"{t.get('deductor') or t.get('tan') or 'manual'} "
                           f"({t.get('section') or '—'})",
                    int(t.get("amount", 0) or 0), "Manual entry (kept)"))
    else:
        tds = [dict(t) for t in base.get("tds", [])]

    # -- Other sources from AIS / TIS -----------------------------------
    os_base = dict(base.get("other_sources", {}))

    def _pick(category: str, base_val: int) -> int:
        best = _best(category)
        if best is None:
            if base_val:
                sources.append(SourceNote(
                    "Other sources", _CATEGORY_LABELS[category], base_val,
                    "Manual entry (kept)"))
            return base_val
        amount, origin = best
        sources.append(SourceNote("Other sources",
                                  _CATEGORY_LABELS[category], amount,
                                  origin))
        if base_val and amount != base_val:
            follow_ups.append(
                f"{_CATEGORY_LABELS[category]}: the imported figure "
                f"₹{amount:,} replaced the earlier entry ₹{base_val:,} — "
                "edit the Other sources tab if the earlier figure was "
                "intentional.")
        return amount

    # The AIS "other income" side: interest-from-others (I-T refund etc.)
    # and the recognised income categories under Part B7 land in the OS
    # 'other' field; family pension gets its own field (1/3rd deduction).
    other_parts = []
    for category in _OTHER_FIELD_CATEGORIES:
        best = _best(category)
        if best is not None:
            other_parts.append((category, *best))
    if other_parts:
        other_val = sum(amount for _, amount, _ in other_parts)
        for category, amount, origin in other_parts:
            sources.append(SourceNote("Other sources",
                                      _CATEGORY_LABELS[category], amount,
                                      origin))
        base_other = os_base.get("other", 0)
        if base_other and other_val != base_other:
            follow_ups.append(
                f"Other income: the imported AIS figure ₹{other_val:,} "
                f"replaced the earlier entry ₹{base_other:,} — edit the "
                "Other sources tab if the earlier figure was intentional.")
        if any(c == "other_income" for c, _, _ in other_parts):
            follow_ups.append(
                "AIS reports other-income items (see the AIS Part B1/B7 "
                "detail) — verify their nature: winnings u/s 115BB/115BBJ "
                "are taxed at special rates the engine does not apply "
                "under 'Other income'.")
    else:
        other_val = os_base.get("other", 0)
        if other_val:
            sources.append(SourceNote("Other sources", "Other income",
                                      other_val, "Manual entry (kept)"))

    other_sources = {
        "savings_interest": _pick("interest_savings",
                                  os_base.get("savings_interest", 0)),
        "fd_interest": _pick("interest_deposits",
                             os_base.get("fd_interest", 0)),
        "dividend": _pick("dividend", os_base.get("dividend", 0)),
        "family_pension": _pick("family_pension",
                                os_base.get("family_pension", 0)),
        "taxable_gifts": os_base.get("taxable_gifts", 0),
        "other": other_val}
    if any(c in tis_totals or c in ais_totals for c in _INTEREST_CATEGORIES):
        filled.append("Interest & dividend")
    if other_parts or _best("family_pension") is not None:
        filled.append("Other income")

    # Where AIS and TIS disagree, use the derived (TIS) figure but flag it.
    for category in sorted(set(ais_totals) & set(tis_totals)):
        if category == "other":
            continue
        if abs(_r(ais_totals[category]) - _r(tis_totals[category])) > tol:
            follow_ups.append(
                f"AIS and TIS disagree on "
                f"{_CATEGORY_LABELS.get(category, category)}: AIS "
                f"₹{_r(ais_totals[category]):,} vs TIS "
                f"₹{_r(tis_totals[category]):,} — used the TIS derived "
                "figure.")

    def _known(category: str) -> int:
        best = _best(category)
        return best[0] if best else 0

    # AIS/TIS items that need cost/date detail the summary can't supply.
    if _known("rent"):
        follow_ups.append(
            f"AIS/TIS report rent received ₹{_known('rent'):,} — add a House "
            "Property row (rent, municipal taxes, 24(b) interest).")
    # -- Capital gains from the AIS sale-of-securities detail ------------
    # Auto-created ONLY into an empty CG table: once any row exists
    # (auto-created earlier, imported from the worksheet, or manual), the
    # table is the practitioner's working copy and is never rebuilt over.
    capital_gains = base.get("capital_gains", [])
    if not capital_gains:
        saved_doc = importers_repo.get_ais_document(scope, ay)
        if saved_doc:
            derived = cg_excel.computation_rows_from_ais(
                ais_mod.document_from_dict(saved_doc["doc"]))
            if derived["rows"]:
                capital_gains = derived["rows"]
                follow_ups.extend(derived["notes"])
                filled.append("Capital gains")
                for row in capital_gains:
                    sources.append(SourceNote(
                        "Capital gains", row["description"],
                        int(row["full_value_consideration"]),
                        "AIS SFT detail"))
    if not capital_gains and _known("sale_securities"):
        follow_ups.append(
            f"AIS/TIS report securities sales of "
            f"₹{_known('sale_securities'):,} — download the capital-gains "
            "template PRE-FILLED from the AIS on the Capital gains tab, "
            "complete the purchase dates, and import it.")
    if _known("sale_immovable"):
        follow_ups.append(
            f"AIS/TIS report an immovable-property sale of "
            f"₹{_known('sale_immovable'):,} — add a Capital Gains row "
            "(property) with cost and dates.")

    # -- Brought-forward losses from last year's return -----------------
    bf_base = dict(base.get("brought_forward", {}))
    brought_forward = {"hp_loss": bf_base.get("hp_loss", 0),
                       "stcl": bf_base.get("stcl", 0),
                       "ltcl": bf_base.get("ltcl", 0)}
    prior = _prior_ay(ay)
    if prior:
        rolled = _prior_carryforward(scope, prior)
        if rolled and any(rolled.values()):
            brought_forward = rolled
            for field_name, label in (("stcl", "STCL"), ("ltcl", "LTCL"),
                                      ("hp_loss", "House-property loss")):
                if rolled[field_name]:
                    sources.append(SourceNote(
                        "Brought-forward loss", label, rolled[field_name],
                        f"Prior year {prior}"))
            filled.append("Brought-forward losses")

    if not (f16 or as26 or ais_totals):
        follow_ups.append(
            "No Form 16, Form 26AS or AIS imported yet — import them on the "
            "Data import page first.")

    # Provenance for manually-entered heads the imports never touch, so the
    # classified summary shows the WHOLE computation, not just imports.
    for hp in base.get("house_properties", []):
        sources.append(SourceNote(
            "House property", hp.get("address") or hp.get("kind", "property"),
            int(hp.get("annual_rent_received", 0) or 0),
            "Manual entry (kept)"))
    for cgr in base.get("capital_gains", []):
        sources.append(SourceNote(
            "Capital gains",
            cgr.get("description") or cgr.get("asset_class", "asset"),
            int(cgr.get("full_value_consideration", 0) or 0),
            "Manual entry (kept)"))
    for b in base.get("businesses", []):
        sources.append(SourceNote(
            "Business", b.get("name") or f"presumptive {b.get('section')}",
            int(b.get("declared_income", 0) or 0)
            or int(b.get("turnover_digital", 0) or 0)
            + int(b.get("turnover_cash", 0) or 0)
            + int(b.get("gross_receipts", 0) or 0),
            "Manual entry (kept)"))
    for b in base.get("regular_businesses", []):
        sources.append(SourceNote(
            "Business", b.get("name") or "regular books",
            int(b.get("net_profit_before_tax", 0) or 0),
            "Manual entry (kept)"))
    for c in base.get("clubbing", []):
        sources.append(SourceNote(
            "Clubbing", c.get("description") or f"u/s {c.get('section')}",
            int(c.get("amount", 0) or 0), "Manual entry (kept)"))
    for e in base.get("exempt_incomes", []):
        sources.append(SourceNote(
            "Exempt income", f"{e.get('section')} "
                             f"{e.get('description') or ''}".strip(),
            int(e.get("amount", 0) or 0), "Manual entry (kept)"))
    for pair in base.get("deductions", []):
        try:
            sec, amt = pair[0], int(pair[1] or 0)
        except (TypeError, ValueError, IndexError):
            continue
        sources.append(SourceNote("Deductions", f"u/s {sec}", amt,
                                  "Manual entry (kept)"))
    if other_sources.get("taxable_gifts"):
        sources.append(SourceNote("Other sources", "Taxable gifts 56(2)(x)",
                                  int(other_sources["taxable_gifts"]),
                                  "Manual entry (kept)"))
    # Manual head/subhead adjustments ride through untouched — imports
    # never overwrite a practitioner's add/less lines.
    adjustments = [dict(a) for a in base.get("adjustments", [])]
    for a in adjustments:
        target = str(a.get("target") or "")
        head = next((label for prefix, label in _ADJ_HEADS
                     if target.startswith(prefix)), "Other sources")
        sources.append(SourceNote(
            head, f"Adjustment: {a.get('label') or target}",
            int(a.get("amount", 0) or 0), "Manual adjustment (kept)"))

    inputs = {
        "ay": ay,
        "personal": dict(base.get("personal", {}))
        or {"resident_status": "Resident"},
        "salaries": salaries,
        "house_properties": base.get("house_properties", []),
        "capital_gains": capital_gains,
        "businesses": base.get("businesses", []),
        "regular_businesses": base.get("regular_businesses", []),
        "clubbing": base.get("clubbing", []),
        "exempt_incomes": base.get("exempt_incomes", []),
        "other_sources": other_sources,
        "deductions": base.get("deductions", []),
        "adjustments": adjustments,
        "tds": tds,
        "challans": base.get("challans", []),
        "brought_forward": brought_forward,
        "regime_election": base.get("regime_election", "auto"),
        "filing_date": base.get("filing_date"),
        "books": base.get("books", {})}

    return AssembledInputs(inputs=inputs, sources=tuple(sources),
                           follow_ups=tuple(follow_ups),
                           filled_heads=tuple(filled))
