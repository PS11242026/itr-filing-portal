"""Seeded ColumnMaps for common Indian banks + the adapter registry.

Maps are data: tweak header spellings here (or via the saved column_maps
table, which the manual mapper feeds) — no code changes per bank vintage.
"""

import hashlib
import json

from itrcore.ingest.base import ColumnMap, FileProbe, ParseResult
from itrcore.ingest.tabular import (load_frame, parse_with_map,
                                    sniff_score)

SEEDED_MAPS: tuple[ColumnMap, ...] = (
    ColumnMap(
        id="sbi_v1", bank_hint="SBI",
        header_synonyms={
            "date": ["Txn Date", "Transaction Date", "Date"],
            "value_date": ["Value Date"],
            "narration": ["Description", "Narration", "Particulars"],
            "ref": ["Ref No./Cheque No.", "Ref No", "Cheque No"],
            "debit": ["Debit", "Withdrawal Amt", "Debit Amount"],
            "credit": ["Credit", "Deposit Amt", "Credit Amount"],
            "balance": ["Balance", "Closing Balance"],
        },
        date_formats=("%d %b %Y", "%d-%m-%Y", "%d/%m/%Y")),
    ColumnMap(
        id="hdfc_v1", bank_hint="HDFC Bank",
        header_synonyms={
            "date": ["Date"],
            "narration": ["Narration"],
            "ref": ["Chq./Ref.No.", "Chq/Ref No"],
            "value_date": ["Value Dt", "Value Date"],
            "debit": ["Withdrawal Amt.", "Withdrawal Amt"],
            "credit": ["Deposit Amt.", "Deposit Amt"],
            "balance": ["Closing Balance"],
        },
        date_formats=("%d/%m/%y", "%d/%m/%Y")),
    ColumnMap(
        id="icici_v1", bank_hint="ICICI Bank",
        header_synonyms={
            "value_date": ["Value Date"],
            "date": ["Transaction Date"],
            "ref": ["Cheque Number"],
            "narration": ["Transaction Remarks"],
            "debit": ["Withdrawal Amount (INR )", "Withdrawal Amount"],
            "credit": ["Deposit Amount (INR )", "Deposit Amount"],
            "balance": ["Balance (INR )", "Balance"],
        },
        date_formats=("%d/%m/%Y", "%d-%b-%y")),
    ColumnMap(
        id="axis_v1", bank_hint="Axis Bank",
        header_synonyms={
            "date": ["Tran Date"],
            "ref": ["CHQNO", "Chq No"],
            "narration": ["PARTICULARS", "Transaction Particulars"],
            "debit": ["DR", "Debit Amount"],
            "credit": ["CR", "Credit Amount"],
            "balance": ["BAL", "Balance"],
        },
        date_formats=("%d-%m-%Y", "%d/%m/%Y")),
    ColumnMap(
        id="kotak_v1", bank_hint="Kotak Mahindra Bank",
        header_synonyms={
            "date": ["Date", "Transaction Date"],
            "narration": ["Narration", "Description"],
            "ref": ["Chq/Ref No", "Chq / Ref number"],
            "amount": ["Amount", "Amount (INR)"],
            "drcr": ["Dr / Cr", "Dr/Cr"],
            "balance": ["Balance", "Balance (INR)"],
        },
        amount_style="single_drcr",
        date_formats=("%d-%m-%Y", "%d/%m/%Y", "%d %b %Y")),
)


def header_signature(frame, header_row: int) -> str:
    """Stable hash of a header row (keys saved custom maps)."""
    if header_row < 0 or header_row >= len(frame):
        return ""
    row = [str(v).strip().lower() for v in frame.iloc[header_row].tolist()]
    return hashlib.sha256(json.dumps(row).encode()).hexdigest()[:32]


def map_from_saved(map_json: str) -> ColumnMap:
    d = json.loads(map_json)
    return ColumnMap(id=d["id"], bank_hint=d.get("bank_hint", "custom"),
                     header_synonyms=d["header_synonyms"],
                     date_formats=tuple(d.get("date_formats", ())),
                     amount_style=d.get("amount_style", "separate"),
                     credit_marker=d.get("credit_marker", "CR"),
                     debit_marker=d.get("debit_marker", "DR"))


def map_to_json(cmap: ColumnMap) -> str:
    return json.dumps({
        "id": cmap.id, "bank_hint": cmap.bank_hint,
        "header_synonyms": cmap.header_synonyms,
        "date_formats": list(cmap.date_formats),
        "amount_style": cmap.amount_style,
        "credit_marker": cmap.credit_marker,
        "debit_marker": cmap.debit_marker})


def best_parse(probe: FileProbe,
               extra_maps: tuple[ColumnMap, ...] = ()) -> ParseResult:
    """Try saved custom maps first, then seeded bank maps; best wins."""
    frame = load_frame(probe)
    best: ParseResult | None = None
    for cmap in tuple(extra_maps) + SEEDED_MAPS:
        if sniff_score(frame, cmap) <= 0:
            continue
        result = parse_with_map(frame, cmap)
        rank = (result.confidence, len(result.columns_used),
                len(result.txns))
        if best is None or rank > (best.confidence,
                                   len(best.columns_used),
                                   len(best.txns)):
            best = result
    if best is None:
        return ParseResult(adapter_id="none", confidence=0.0,
                           issues=("no known bank layout matched — use "
                                   "the manual column mapper",))
    return best
