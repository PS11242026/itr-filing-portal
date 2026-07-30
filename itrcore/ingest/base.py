"""Ingestion canon: FileProbe, RawTxn, ColumnMap, ParseResult."""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class FileProbe:
    filename: str
    data: bytes
    kind: str            # csv | xlsx | xls_html | pdf | unknown
    text_head: str = ""  # first ~4KB decoded, for sniffing


@dataclass(frozen=True)
class RawTxn:
    txn_date: date
    narration: str
    amount_paise: int              # signed; credit positive
    value_date: date | None = None
    ref: str | None = None
    balance_paise: int | None = None
    row_no: int = 0


@dataclass(frozen=True)
class ColumnMap:
    """A bank statement layout as data. Roles: date, value_date, narration,
    ref, debit, credit, amount, drcr, balance."""
    id: str
    bank_hint: str
    header_synonyms: dict            # role → list of header spellings
    date_formats: tuple[str, ...] = ()
    amount_style: str = "separate"   # separate | single_signed | single_drcr
    credit_marker: str = "CR"
    debit_marker: str = "DR"
    header_search_rows: int = 15


@dataclass(frozen=True)
class ParseResult:
    adapter_id: str
    confidence: float                # 0..1
    txns: tuple[RawTxn, ...] = ()
    issues: tuple[str, ...] = ()
    header_row: int | None = None
    columns_used: dict = field(default_factory=dict)
