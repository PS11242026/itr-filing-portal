"""AIS parser — the encrypted v15 export and older flat JSON, one taxonomy.

Two input shapes are handled:

1. The real portal download (``*_AIS_*.json``): an ENCRYPTED envelope
   (:mod:`itrcore.ingest.ais_crypto`) wrapping an AIS **v15** document with
   ``partA`` (general info) and ``partB.sections`` — Part B1 (TDS/TCS),
   B2 (SFT), B3 (payment of taxes), B4 (demand & refund), B7 (other info).
   Each information block carries an ``l2`` summary row (category, code,
   source, count, amount, derived amount) and an ``l1`` transaction-detail
   table — exactly the bifurcation a Genius AIS pull shows.

2. Older/plain flat JSON — walked tolerantly for recognisable info lines.

:func:`parse_ais_document` returns the rich structure (all parts, with L1+L2
detail) used for the Genius-style display and the Excel export.
:func:`parse_ais_json` keeps the original contract — a flat, de-duplicated
``[{category, description, amount_paise}]`` list the importer/auto-fill
already consume — so nothing downstream changes.
"""

import json
from dataclasses import dataclass

from itrcore.ingest import ais_crypto

# ---------------------------------------------------------------- taxonomy --
# Internal computation buckets. The first four feed auto-fill directly; the
# rest are carried for the AIS/TIS display and reconciliation.
_CATEGORY_KEYWORDS = {
    "interest_savings": ("interest from savings", "sb interest",
                         "sft-016(sb)", "savings bank"),
    "interest_deposits": ("interest from deposit", "interest from term",
                          "fd interest", "interest on deposit",
                          "sft-016(td)", "term deposit"),
    "interest_others": ("interest from others", "interest from income tax "
                        "refund", "interest income (others)"),
    "dividend": ("dividend",),
    "rent": ("rent received", "rent from", "rent payment"),
    # Before "salary": family pension is other-sources income, but its name
    # would otherwise hit the salary bucket's "pension" needle.
    "family_pension": ("family pension",),
    "salary": ("salary", "pension"),
    # SFT-005/006 spending reports. These are EXPENDITURE the assessee
    # incurred — a credit-card bill settled, cash paid for a draft — and the
    # AIS calls several of them "Miscellaneous payment". Bucketed before
    # other_income so that needle can never pull ₹37 lakh of card repayments
    # into Section 56.
    "expenditure": ("payment to credit card", "credit card payment",
                    "payment made to credit card", "purchase of bank drafts",
                    "cash payment", "expenditure in foreign"),
    # Genuinely-income B1/B7 categories with no bucket of their own — these
    # feed the computation's Other sources "other income" field. Deliberately
    # NOT a catch-all: cash deposits, card spends, travel etc. stay in
    # "other" (display-only) so they never masquerade as income.
    "other_income": ("winnings", "lottery", "crossword", "online game",
                     "betting", "gambling", "annuity", "retirement benefit",
                     "national savings scheme", "accumulated balance",
                     "income distributed", "commission", "royalty", "prize"),
    "sale_securities": ("sale of securities", "off market", "sale of units",
                        "sale of equity", "sale of mutual fund"),
    "purchase_securities": ("purchase of securities", "purchase of units",
                            "purchase of mutual fund"),
    "purchase_time_deposit": ("purchase of time deposit",
                              "purchase of term deposit"),
    "sale_immovable": ("sale of immovable", "immovable property"),
    "business_receipts": ("receipts from business", "business receipts",
                          "gross receipts", "turnover"),
    "gst_turnover": ("gst turnover",),
    "foreign_remittance": ("foreign remittance", "outward foreign"),
}

# ITD information-category codes → internal bucket (authoritative when present;
# the keyword map is the fallback).
_CODE_TO_BUCKET = {
    "SAL": "salary", "INS": "interest_savings", "IND": "interest_deposits",
    "INO": "interest_others", "DIV": "dividend", "RNT": "rent",
    "SS": "sale_securities", "PS": "purchase_securities",
    "PTD": "purchase_time_deposit", "SIP": "sale_immovable",
    "BR": "business_receipts", "GST": "gst_turnover",
}


def classify(desc: str) -> str:
    """Map a free-text information description/category to an internal bucket.
    Shared with the TIS parser so AIS and TIS land in the same taxonomy."""
    low = (desc or "").lower()
    for category, needles in _CATEGORY_KEYWORDS.items():
        if any(n in low for n in needles):
            return category
    return "other"


# Payers whose "savings bank" interest is actually POST OFFICE savings-bank
# interest. It needs its own bucket because Sec 10(15)(i) exempts the first
# ₹3,500 (₹7,000 joint) of it and nothing else — the AIS reports it under the
# same information category as any bank's, so only the payer can tell them
# apart. CPRC Chennai is the post office's centralised reporting centre.
_POST_OFFICE_PAYERS = ("CPRC", "CENTRALIZED PROCESSING AND RECONCILIATION",
                       "POST OFFICE", "POSTAL", "DEPARTMENT OF POSTS",
                       "INDIA POST")


def _bucket(category: str, category_code: str, source: str = "") -> str:
    code = (category_code or "").strip().upper()
    bucket = (_CODE_TO_BUCKET[code] if code in _CODE_TO_BUCKET
              else classify(category))
    if bucket == "interest_savings" and _is_post_office(source):
        return "interest_post_office"
    return bucket


def _is_post_office(source: str) -> bool:
    up = (source or "").upper()
    return any(p in up for p in _POST_OFFICE_PAYERS)


def _sft_only() -> bool:
    """Read lazily (not at import) so a test can flip the config file."""
    from itrcore.analysis.classify import load_thresholds
    return bool(load_thresholds().get("ais", {}).get(
        "sft_only_for_interest_and_dividend", True))


# ------------------------------------------------------------- value helpers
def money_to_paise(raw) -> int | None:
    """'15,84,000.00' / 158400 / '-' → integer paise (None when not a
    number)."""
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if s in ("", "-", "NA", "N/A"):
        return None
    try:
        return int(round(float(s) * 100))
    except (TypeError, ValueError):
        return None


def _label_names(column_label) -> list[str]:
    """A columnLabel list may hold plain strings or field dicts."""
    out = []
    for c in column_label or []:
        if isinstance(c, dict):
            out.append(str(c.get("name") or c.get("field") or ""))
        else:
            out.append(str(c))
    return out


def _norm_source(source: str) -> str:
    """'STATE BANK OF INDIA (MUMS86175F)' → 'STATE BANK OF INDIA' so the same
    payer reported via different ids (TAN vs PAN) de-duplicates."""
    s = (source or "").split("(")[0]
    return " ".join(s.upper().split())


# Public alias — auto-fill groups AIS salary blocks by payer with the same
# normalization the de-duplication uses.
norm_source = _norm_source


def _part_code(title: str) -> str:
    """'Part B1-Information relating to...' → 'B1'."""
    t = (title or "").strip()
    if t.lower().startswith("part "):
        token = t[5:].split("-", 1)[0].split()[0] if len(t) > 5 else ""
        return token.strip().upper()
    return ""


# --------------------------------------------------------------- data model
@dataclass(frozen=True)
class DetailTable:
    """The ``l1`` transaction-level rows under one information block."""

    labels: tuple[str, ...] = ()
    rows: tuple[tuple, ...] = ()


@dataclass(frozen=True)
class AisRow:
    """One AIS information block (an ``l2`` summary + its ``l1`` detail)."""

    part: str                       # 'B1'..'B7'
    part_title: str
    category: str                   # ITD category, e.g. 'Salary'
    category_code: str              # 'SAL'
    info_code: str                  # 'TDS-192'
    description: str
    source: str                     # 'STATE BANK OF INDIA (MUMS89582D)'
    count: int
    amount_paise: int
    derived_paise: int | None
    bucket: str                     # internal computation bucket
    detail: DetailTable = DetailTable()


@dataclass(frozen=True)
class TaxPayment:
    """One Part B3 challan — tax the assessee paid himself.

    Since the AY 2023-24 redesign the TRACES 26AS carries only TDS/TCS and
    refunds (its Part VII says in terms "for other details refer AIS"), so
    the AIS is now the ONLY pulled source for advance tax and
    self-assessment tax.

    ``fy`` is the year the payment RELATES to, not the year it was made:
    a Self Assessment challan paid 12-09-2025 is stamped FY 2024-25 because
    it discharges AY 2025-26. Crediting on the deposit date instead would
    put a prior year's tax against this year's liability, so ``fy`` is what
    decides the assessment year."""

    fy: str = ""                   # "2024-25" — the FY the tax relates to
    major_head: str = ""           # "Income Tax (Other than Companies)"
    minor_head: str = ""           # "Advance Tax" / "Self Assessment" / …
    kind: str = "advance"          # advance | self_assessment | regular_…
    amount_paise: int = 0          # Total (A+B+C+D)
    bsr_code: str = ""
    deposit_date: str = ""         # dd/mm/yyyy as printed
    challan_serial: str = ""
    cin: str = ""

    @property
    def ay(self) -> str:
        """FY 2024-25 → AY 2025-26."""
        try:
            start = int(str(self.fy).split("-")[0])
        except (ValueError, IndexError):
            return ""
        return f"{start + 1}-{str(start + 2)[2:]}"


@dataclass(frozen=True)
class AisDocument:
    pan: str = ""
    name: str = ""
    dob: str = ""
    general_info: tuple[tuple[str, str], ...] = ()
    rows: tuple[AisRow, ...] = ()
    json_version: str = ""
    tax_payments: tuple[TaxPayment, ...] = ()

    # Income buckets reported BOTH as B1 TDS blocks and B2 SFT blocks.
    _SFT_PREFERRED = ("dividend", "interest_savings", "interest_post_office",
                      "interest_deposits", "interest_others")

    def _rows_for_totals(self) -> tuple["AisRow", ...]:
        """The rows that participate in income totals.

        Dividend and interest are reported TWICE — under Part B1 as the TDS
        blocks (TDS-194/194A) and under Part B2 as SFT-015/016. Payer names
        differ between the two feeds ("ITC LIMITED" vs "I T C LIMITED",
        "CENTRALIZED PROCESSING AND RECONCILIATION CENTRE" vs "CPRC
        CHENNAI"), and so do the figures when a bank reports interest
        gross-of-TDS in one feed and net in the other. Matching them up
        pairwise therefore leaks: a TDS re-report whose payer normalises
        differently AND whose rupee figure differs survives, and the bucket
        is silently inflated by that whole amount.

        Under ``ais.sft_only_for_interest_and_dividend`` (the default) the
        rule is categorical: **once a bucket has any SFT row, the SFT feed
        alone decides its total.** That is the feed the ITD itself derives
        the TIS processed value from, and it is the only rule a stray TDS
        re-report cannot slip past. A bucket with no SFT row at all still
        falls back to its TDS rows.

        With the flag off, the older pairwise rule applies: an SFT row
        consumes a TDS row of the same bucket carrying the same normalized
        payer OR the same rupee figure (each consuming at most one).

        Nothing is deleted either way: dropped rows stay in the viewer and
        the exports, and :meth:`dropped_tds_rows` lists them so a payer that
        reports only through TDS is added back deliberately, not silently
        lost."""
        b2_buckets = {r.bucket for r in self.rows
                      if r.part == "B2" and r.bucket in self._SFT_PREFERRED}
        if not b2_buckets:
            return self.rows
        if _sft_only():
            return tuple(r for r in self.rows
                         if r.part == "B2" or r.bucket not in b2_buckets)

        b2_names: dict[str, set] = {}
        b2_amounts: dict[str, dict[int, int]] = {}
        for r in self.rows:
            if r.part == "B2" and r.bucket in self._SFT_PREFERRED:
                b2_names.setdefault(r.bucket, set()).add(
                    _norm_source(r.source))
                if r.amount_paise > 0:
                    bucket_amounts = b2_amounts.setdefault(r.bucket, {})
                    bucket_amounts[r.amount_paise] = \
                        bucket_amounts.get(r.amount_paise, 0) + 1
        out = []
        for r in self.rows:
            if r.part != "B2" and r.bucket in b2_names:
                if _norm_source(r.source) in b2_names[r.bucket]:
                    continue          # same payer — the SFT figure wins
                remaining = b2_amounts.get(r.bucket, {})
                if r.amount_paise > 0 and remaining.get(r.amount_paise, 0):
                    remaining[r.amount_paise] -= 1
                    continue          # same figure re-reported via TDS
            out.append(r)
        return tuple(out)

    def dropped_tds_rows(self) -> tuple["AisRow", ...]:
        """B1 TDS rows suppressed by the SFT-only rule that have NO twin in
        the SFT feed — the only ones actually at risk of being lost.

        A suppressed row whose payer or figure also appears under Part B2 is
        the same income reported twice, which is the whole reason the rule
        exists; saying so would bury the real cases in noise."""
        kept = {id(r) for r in self._rows_for_totals()}
        b2_names: dict[str, set] = {}
        b2_amounts: dict[str, set] = {}
        for r in self.rows:
            if r.part == "B2" and r.bucket in self._SFT_PREFERRED:
                b2_names.setdefault(r.bucket, set()).add(
                    _norm_source(r.source))
                b2_amounts.setdefault(r.bucket, set()).add(r.amount_paise)
        out = []
        for r in self.rows:
            if id(r) in kept or r.amount_paise <= 0:
                continue
            if _norm_source(r.source) in b2_names.get(r.bucket, ()):
                continue                       # same payer, reported twice
            if r.amount_paise in b2_amounts.get(r.bucket, ()):
                continue                       # same figure, reported twice
            out.append(r)
        return tuple(out)

    def _dedup_totals(self, key_of) -> dict[str, int]:
        """Rupee total per group, de-duplicating the same income reported
        through several mechanisms.

        The same figure is reported more than once — salary under Part B1
        (TDS-192) *and* Part B7 (Annexure-II); deposit interest under B1
        (TDS-194A) *and* B2 (SFT-016). Summing every row double counts. So per
        (group, payer) we keep the LARGEST reported figure, then sum across
        payers — closely tracking the TIS processed value. Dividend and
        interest are special-cased to the SFT rows alone (see
        _rows_for_totals)."""
        best: dict[tuple[str, str], int] = {}
        for r in self._rows_for_totals():
            grp = key_of(r)
            if grp is None or r.amount_paise <= 0:
                continue
            k = (grp, _norm_source(r.source))
            best[k] = max(best.get(k, 0), r.amount_paise)
        totals: dict[str, int] = {}
        for (grp, _src), amt in best.items():
            totals[grp] = totals.get(grp, 0) + amt
        return totals

    def category_totals(self) -> dict[str, int]:
        """De-duplicated rupee total per internal computation bucket (drives
        auto-fill). Import the TIS JSON for the ITD's own reconciled figure
        when it matters."""
        return self._dedup_totals(
            lambda r: None if r.bucket == "other" else r.bucket)

    def tis_summary(self) -> list[dict]:
        """TIS-style summary grouped by the ITD information category name
        (matches the Genius 'Taxpayer Information Summary' sheet). Processed =
        de-duplicated reported value; derived mirrors it unless the AIS block
        carried a distinct derived amount."""
        processed = self._dedup_totals(lambda r: r.category or None)
        derived_seen: dict[str, int] = {}
        for r in self._rows_for_totals():
            if r.derived_paise:
                derived_seen[r.category] = (derived_seen.get(r.category, 0)
                                            + r.derived_paise)
        out = []
        for i, cat in enumerate(sorted(processed), start=1):
            out.append({"sr": i, "category": cat,
                        "processed_paise": processed[cat],
                        "derived_paise": derived_seen.get(cat,
                                                          processed[cat])})
        return out

    def rows_for_part(self, part: str) -> list["AisRow"]:
        return [r for r in self.rows if r.part == part]


# --------------------------------------------------------- (de)serialization
def document_to_dict(doc: AisDocument) -> dict:
    """JSON-safe dict for persistence (``details_json`` / doc store)."""
    return {
        "pan": doc.pan, "name": doc.name, "dob": doc.dob,
        "json_version": doc.json_version,
        "general_info": [list(p) for p in doc.general_info],
        "rows": [{
            "part": r.part, "part_title": r.part_title,
            "category": r.category, "category_code": r.category_code,
            "info_code": r.info_code, "description": r.description,
            "source": r.source, "count": r.count,
            "amount_paise": r.amount_paise, "derived_paise": r.derived_paise,
            "bucket": r.bucket,
            "detail_labels": list(r.detail.labels),
            "detail_rows": [list(row) for row in r.detail.rows],
        } for r in doc.rows],
        "tax_payments": [{
            "fy": p.fy, "major_head": p.major_head,
            "minor_head": p.minor_head, "kind": p.kind,
            "amount_paise": p.amount_paise, "bsr_code": p.bsr_code,
            "deposit_date": p.deposit_date,
            "challan_serial": p.challan_serial, "cin": p.cin,
        } for p in doc.tax_payments]}


def document_from_dict(data: dict) -> AisDocument:
    # bucket is DERIVED data — recomputed from category/code on every load
    # so documents saved under an older taxonomy self-heal (family pension
    # out of salary, the SFT-only dividend rule, new income buckets) without
    # any migration or re-import.
    rows = tuple(AisRow(
        part=d["part"], part_title=d.get("part_title", ""),
        category=d["category"], category_code=d.get("category_code", ""),
        info_code=d.get("info_code", ""), description=d.get("description", ""),
        source=d.get("source", ""), count=d.get("count", 0),
        amount_paise=d.get("amount_paise", 0),
        derived_paise=d.get("derived_paise"),
        bucket=_bucket(d["category"], d.get("category_code", ""),
                       d.get("source", "")),
        detail=DetailTable(
            labels=tuple(d.get("detail_labels", ())),
            rows=tuple(tuple(x) for x in d.get("detail_rows", ()))))
        for d in data.get("rows", []))
    return AisDocument(
        pan=data.get("pan", ""), name=data.get("name", ""),
        dob=data.get("dob", ""), json_version=data.get("json_version", ""),
        general_info=tuple(tuple(p) for p in data.get("general_info", ())),
        rows=rows,
        tax_payments=tuple(TaxPayment(**p)
                           for p in data.get("tax_payments", ())))


def challans_for_ay(doc: AisDocument, ay: str) -> tuple[list[dict], list]:
    """Split the AIS Part B3 challans into (creditable here, other years).

    A challan belongs to the assessment year its own ``Financial Year``
    column names — never to the year it happened to be deposited in. Only
    the matching ones are shaped for the computation; the rest are handed
    back so the practitioner is told they exist rather than left wondering
    where the money went."""
    mine: list[dict] = []
    others: list[TaxPayment] = []
    for p in doc.tax_payments:
        if not p.amount_paise or p.kind == "regular_assessment":
            continue                # 400 is post-assessment, not a credit here
        if p.ay != ay:
            others.append(p)
            continue
        day, _, rest = p.deposit_date.partition("/")
        month, _, year = rest.partition("/")
        mine.append({
            "kind": p.kind,
            "paid_on": (f"{year}-{month}-{day}"
                        if len(year) == 4 and month and day else None),
            "amount": round(p.amount_paise / 100),
            "reference": f"{p.bsr_code} - {p.challan_serial}".strip(" -")})
    return mine, others


# ------------------------------------------------------------- v15 parsing
def _general_info(part_a: dict) -> tuple[list[tuple[str, str]], dict]:
    names = _label_names(part_a.get("columnLabel"))
    data = part_a.get("columnData") or []
    pairs = [(n, "" if v is None else str(v))
             for n, v in zip(names, data)]
    fields = {n.lower(): v for n, v in pairs}
    return pairs, fields


def _detail_table(l1: dict) -> DetailTable:
    labels = tuple(_label_names(l1.get("columnLabel")))
    if not labels:
        return DetailTable()
    rows = []
    for raw in l1.get("columnData") or []:
        if isinstance(raw, list):
            rows.append(tuple("" if c is None else c
                              for c in raw[:len(labels)]))
    return DetailTable(labels=labels, rows=tuple(rows))


def _summary_row(l2: dict) -> dict | None:
    """The single ``l2`` row → {category, info_code, description, source,
    count, amount, category_code, derived}."""
    names = _label_names(l2.get("columnLabel"))
    data_rows = l2.get("columnData") or []
    if not names or not data_rows or not isinstance(data_rows[0], list):
        return None
    row = dict(zip((n.strip().lower() for n in names), data_rows[0]))
    return {
        "category": str(row.get("information category") or "").strip(),
        "info_code": str(row.get("information code") or "").strip(),
        "description": str(row.get("information description") or "").strip(),
        "source": str(row.get("information source") or "").strip(),
        "count": row.get("count"),
        "amount": row.get("amount"),
        "category_code": str(row.get("information category code")
                             or "").strip(),
        "derived": row.get("derived amount"),
    }


def _elements(section: dict) -> list[dict]:
    els = list(section.get("elements") or [])
    for sub in section.get("subSections") or []:
        els.extend(sub.get("elements") or [])
    return els


# Minor head on the challan → what the payment was. The AIS prints the words,
# the challan itself carries the code, so both are accepted.
_MINOR_HEAD_KIND = {
    "advance tax": "advance", "100": "advance",
    "self assessment": "self_assessment",
    "self assessment tax": "self_assessment", "300": "self_assessment",
    "tax on regular assessment": "regular_assessment", "400":
        "regular_assessment",
}


def _tax_payments(section: dict) -> list[TaxPayment]:
    """Part B3 rows → challans.

    B3 is shaped unlike every other AIS block: a flat ``columnLabel`` /
    ``columnData`` table with NO ``l2`` summary and no ``l1`` detail. The
    main row walk requires ``l2``, so every challan the AIS reported was
    being skipped in silence — which is why computations showed a payable
    the client had already settled."""
    out: list[TaxPayment] = []
    for el in _elements(section):
        names = [n.strip().lower()
                 for n in _label_names(el.get("columnLabel"))]
        if "bsr code" not in names:
            continue
        for data in el.get("columnData") or []:
            if not isinstance(data, list):
                continue
            row = dict(zip(names, data))
            total = money_to_paise(row.get("total (a+b+c+d)"))
            if total is None:                 # fall back to the tax column
                total = money_to_paise(row.get("tax (a)")) or 0
            minor = str(row.get("minor head") or "").strip()
            out.append(TaxPayment(
                fy=str(row.get("financial year") or "").strip(),
                major_head=str(row.get("major head") or "").strip(),
                minor_head=minor,
                kind=_MINOR_HEAD_KIND.get(minor.lower(), "advance"),
                amount_paise=total,
                bsr_code=str(row.get("bsr code") or "").strip(),
                deposit_date=str(row.get("date of deposit") or "").strip(),
                challan_serial=str(row.get("challan serial number")
                                   or "").strip(),
                cin=str(row.get("challan identification number")
                        or "").strip()))
    return out


def _parse_v15(tree: dict) -> AisDocument:
    pairs, fields = _general_info(tree.get("partA") or {})
    rows: list[AisRow] = []
    payments: list[TaxPayment] = []
    for section in (tree.get("partB") or {}).get("sections") or []:
        part = _part_code(section.get("title", ""))
        part_title = str(section.get("title") or "")
        if section.get("sectionKey") == "paymentOfTaxes" or part == "B3":
            payments.extend(_tax_payments(section))
            continue
        for el in _elements(section):
            summ = _summary_row(el.get("l2") or {})
            if not summ or not summ["category"]:
                continue
            amount = money_to_paise(summ["amount"]) or 0
            try:
                count = int(float(str(summ["count"])))
            except (TypeError, ValueError):
                count = 0
            rows.append(AisRow(
                part=part, part_title=part_title,
                category=summ["category"],
                category_code=summ["category_code"],
                info_code=summ["info_code"],
                description=summ["description"] or summ["category"],
                source=summ["source"] or str(el.get("infoSrcId") or ""),
                count=count, amount_paise=amount,
                derived_paise=money_to_paise(summ["derived"]),
                bucket=_bucket(summ["category"], summ["category_code"],
                       summ["source"] or ""),
                detail=_detail_table(el.get("l1") or {})))
    return AisDocument(
        pan=fields.get("permanent account number (pan)", "")
        or (tree.get("metadata") or {}).get("loggedInPan", ""),
        name=fields.get("name of assessee", ""),
        dob=fields.get("date of birth", ""),
        general_info=tuple(pairs), rows=tuple(rows),
        json_version=str((tree.get("metadata") or {}).get("jsonVersion", "")),
        tax_payments=tuple(payments))


def _is_v15(tree) -> bool:
    return isinstance(tree, dict) and "partB" in tree and isinstance(
        tree.get("partB"), dict)


# --------------------------------------------------- older flat JSON walk
def _walk(node, hits: list[dict]) -> None:
    if isinstance(node, dict):
        desc = (node.get("infoDesc") or node.get("informationDescription")
                or node.get("desc") or "")
        amount = (node.get("processedValue") or node.get("amount")
                  or node.get("infoValue") or node.get("reportedValue"))
        if desc and amount is not None:
            paise = money_to_paise(amount)
            if paise is not None:
                hits.append({"category": classify(str(desc)),
                             "description": str(desc)[:200],
                             "amount_paise": paise})
        for value in node.values():
            _walk(value, hits)
    elif isinstance(node, list):
        for item in node:
            _walk(item, hits)


# --------------------------------------------------------------- public API
def _load(data: bytes, pan: str | None, dob: str | None) -> object:
    """Decrypt if the file is an ITD envelope, then JSON-parse."""
    if ais_crypto.looks_encrypted(data):
        if not (pan and dob):
            missing = "PAN and date of birth" if not pan else "date of birth"
            raise ValueError(
                "this AIS file is encrypted by the ITD portal and is keyed "
                f"to the client's PAN + date of birth — the client's "
                f"{missing} is missing. Add it to the client record (or "
                "type the DOB alongside the upload) and retry; a "
                "successful decrypt stores it on the record automatically.")
        data = ais_crypto.decrypt(data, pan=pan, dob=dob)
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a valid AIS JSON export: {exc}") from exc


def parse_ais_document(data: bytes, *, pan: str | None = None,
                       dob: str | None = None) -> AisDocument:
    """Full AIS v15 document (all parts, L1+L2 detail). Raises ``ValueError``
    for a non-v15 payload — callers wanting the flat list use
    :func:`parse_ais_json`."""
    tree = _load(data, pan, dob)
    if not _is_v15(tree):
        raise ValueError("not an AIS v15 document (no partB) — use "
                         "parse_ais_json for the older flat format")
    return _parse_v15(tree)


def flat_entries(doc: AisDocument) -> list[dict]:
    """The de-duplicated flat list ``import_ais`` stores — ONE code path
    shared by fresh parses and re-derivation from a saved document."""
    labels = {r.bucket: r.category for r in doc.rows}
    return [{"category": bucket,
             "description": labels.get(bucket, bucket),
             "amount_paise": paise}
            for bucket, paise in sorted(doc.category_totals().items())]


def tis_entries(doc: AisDocument) -> list[dict]:
    """The TIS rows ``import_tis`` stores, derived from the document.

    Grouped by our BUCKET, not by re-classifying the ITD's category text.
    The two differ wherever the bucket depends on something the category
    name does not carry: post-office savings interest is filed by the ITD
    under plain "Interest from savings bank", so classifying the text would
    fold it back into bank interest — and since auto-fill prefers the TIS
    figure over the AIS one, that lands the same money in BOTH the savings
    and post-office fields. ``tis_summary`` keeps the ITD's own grouping for
    the Genius-style display; this is the internal taxonomy."""
    processed = doc.category_totals()
    labels: dict[str, str] = {}
    derived: dict[str, int] = {}
    for r in doc._rows_for_totals():
        if r.bucket == "other":
            continue
        labels.setdefault(r.bucket, r.category)
        if r.derived_paise:
            derived[r.bucket] = derived.get(r.bucket, 0) + r.derived_paise
    return [{"category": bucket,
             "description": labels.get(bucket, bucket),
             "processed_value_paise": amount,
             "derived_value_paise": derived.get(bucket, amount)}
            for bucket, amount in sorted(processed.items())]


def parse_ais_json(data: bytes, *, pan: str | None = None,
                   dob: str | None = None) -> list[dict]:
    """Flat, de-duplicated ``[{category, description, amount_paise}]`` for the
    importer / auto-fill. Handles the encrypted v15 export and the older flat
    JSON alike."""
    tree = _load(data, pan, dob)
    if _is_v15(tree):
        entries = flat_entries(_parse_v15(tree))
        if not entries:
            raise ValueError("AIS decrypted but carried no recognised income "
                             "categories")
        return entries
    hits: list[dict] = []
    _walk(tree, hits)
    if not hits:
        raise ValueError("no information entries recognised in this AIS "
                         "JSON — enter figures manually and flag the file "
                         "format to the developer")
    return hits
