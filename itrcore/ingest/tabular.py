"""Generic tabular statement parser driven by a ColumnMap.

Pipeline: bytes → DataFrame (no header) → locate header row (fuzzy) →
map columns to roles → parse rows → RawTxns + issues. Never raises on a
bad row; it degrades to an issue entry so staff can fix it in staging.
"""

import csv
import io
from decimal import Decimal

import pandas as pd
from rapidfuzz import fuzz

from itrcore.ingest.base import ColumnMap, FileProbe, ParseResult, RawTxn
from itrcore.ingest.normalize import (norm_narration, parse_amount,
                                      parse_date, to_paise)

_ROLES = ("date", "value_date", "narration", "ref", "debit", "credit",
          "amount", "drcr", "balance")


def load_frame(probe: FileProbe) -> pd.DataFrame:
    """Raw grid with NO header row applied."""
    if probe.kind == "csv":
        # csv.reader instead of read_csv: bank files open with ragged
        # title lines and stray spaces before quoted fields, which make
        # pandas drop the real data rows as "bad lines".
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = probe.data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("could not decode the CSV file")
        rows = [r for r in csv.reader(io.StringIO(text),
                                      skipinitialspace=True)
                if any((c or "").strip() for c in r)]
        if not rows:
            raise ValueError("the CSV file is empty")
        width = max(len(r) for r in rows)
        rows = [r + [None] * (width - len(r)) for r in rows]
        return pd.DataFrame(rows)
    if probe.kind == "xlsx":
        return pd.read_excel(io.BytesIO(probe.data), header=None, dtype=str)
    if probe.kind == "xls_html":
        tables = pd.read_html(io.BytesIO(probe.data), header=None,
                              flavor="lxml")
        if not tables:
            raise ValueError("no tables found in the HTML .xls file")
        frame = max(tables, key=len)
        return frame.astype(str).where(frame.notna(), None)
    if probe.kind == "xls_legacy":
        raise ValueError("legacy .xls needs 'xlrd' — ask the client for "
                         "CSV/XLSX, or convert in Excel")
    raise ValueError(f"unsupported file kind: {probe.kind}")


def _norm_header(text) -> str:
    return " ".join(str(text or "").strip().lower()
                    .replace(".", " ").replace("/", " ").split())


def find_header(frame: pd.DataFrame, cmap: ColumnMap) -> tuple[int, dict]:
    """Returns (header_row_index, {role: column_index}) for the best row."""
    best = (-1, {}, 0.0)
    limit = min(cmap.header_search_rows, len(frame))
    for r in range(limit):
        row = [_norm_header(v) for v in frame.iloc[r].tolist()]
        mapping: dict[str, int] = {}
        score = 0.0
        for role, synonyms in cmap.header_synonyms.items():
            for c, cell in enumerate(row):
                if not cell or c in mapping.values():
                    continue
                s = max(fuzz.token_sort_ratio(cell, _norm_header(syn))
                        for syn in synonyms)
                if s >= 85:
                    mapping[role] = c
                    score += s / 100
                    break
        if score > best[2]:
            best = (r, mapping, score)
    return best[0], best[1]


def sniff_score(frame: pd.DataFrame, cmap: ColumnMap) -> float:
    _, mapping = find_header(frame, cmap)
    needed = {"date", "narration"}
    has_amount = ({"debit", "credit"} <= mapping.keys()
                  or "amount" in mapping)
    if not needed <= mapping.keys() or not has_amount:
        return 0.0
    return min(1.0, len(mapping) / max(3, len(cmap.header_synonyms)))


def parse_with_map(frame: pd.DataFrame, cmap: ColumnMap,
                   *, header_row: int | None = None,
                   mapping: dict | None = None) -> ParseResult:
    if header_row is None or mapping is None:
        header_row, mapping = find_header(frame, cmap)
    if header_row < 0 or "date" not in mapping:
        return ParseResult(adapter_id=cmap.id, confidence=0.0,
                           issues=("could not locate a header row",))

    txns: list[RawTxn] = []
    issues: list[str] = []
    parsed = 0
    candidates = 0
    for r in range(header_row + 1, len(frame)):
        row = frame.iloc[r]

        def cell(role):
            idx = mapping.get(role)
            if idx is None or idx >= len(row):
                return None
            v = row.iloc[idx]
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            s = str(v).strip()
            return s if s and s.lower() != "nan" else None

        raw_date = cell("date")
        raw_narr = cell("narration") or ""
        if raw_date is None and not raw_narr:
            continue
        candidates += 1
        txn_date = parse_date(raw_date, cmap.date_formats)
        if txn_date is None:
            continue                      # summary/footer rows

        if cmap.amount_style == "separate":
            debit = parse_amount(cell("debit"))
            credit = parse_amount(cell("credit"))
            if debit is None and credit is None:
                issues.append(f"row {r + 1}: no amount")
                continue
            amount = (credit or 0) - (debit or 0)
        elif cmap.amount_style == "single_drcr":
            amount = parse_amount(cell("amount"))
            if amount is None:
                issues.append(f"row {r + 1}: no amount")
                continue
            flag = (cell("drcr") or "").strip().upper().replace(".", "")
            if flag.startswith(cmap.debit_marker):
                amount = -abs(amount)
            else:
                amount = abs(amount)
        else:                             # single_signed
            amount = parse_amount(cell("amount"))
            if amount is None:
                issues.append(f"row {r + 1}: no amount")
                continue

        txns.append(RawTxn(
            txn_date=txn_date,
            value_date=parse_date(cell("value_date"), cmap.date_formats),
            narration=norm_narration(raw_narr),
            ref=cell("ref"),
            amount_paise=to_paise(Decimal(amount)),
            balance_paise=to_paise(parse_amount(cell("balance"))),
            row_no=r + 1))
        parsed += 1

    confidence = (parsed / candidates) if candidates else 0.0
    return ParseResult(adapter_id=cmap.id, confidence=confidence,
                       txns=tuple(txns), issues=tuple(issues),
                       header_row=header_row, columns_used=mapping)
