"""Shared domain dataclasses.

Money convention: bank-ledger amounts are INTEGER PAISE end to end;
the tax engine works in whole rupees (converted once at its boundary).
Floats never carry money.
"""

from dataclasses import dataclass

from itrcore.authz import Principal


@dataclass(frozen=True)
class Taxpayer:
    id: int
    full_name: str
    pan: str                      # decrypted; UI must mask by default
    pan_masked: str
    dob: str | None
    email: str | None
    mobile: str | None
    address: str | None
    resident_status: str
    profile: dict
    status: str


@dataclass(frozen=True)
class TaxpayerSummary:
    """List-view row — no decrypted PII."""
    id: int
    full_name: str
    pan_masked: str
    status: str


@dataclass(frozen=True)
class DocumentMeta:
    id: int
    taxpayer_id: int
    ay: str
    doc_type: str
    orig_name: str
    sha256: str
    size_bytes: int
    note: str | None
    uploaded_by: str | None
    uploaded_at: str
    status: str


@dataclass(frozen=True)
class ChecklistItem:
    key: str
    label: str
    applies: str                  # "always" or a profile flag name
    doc_types: tuple[str, ...] = ()
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class ChecklistState:
    item: ChecklistItem
    docs_count: int
    status: str                   # pending | satisfied | waived
    note: str = ""


@dataclass(frozen=True)
class AuthUser:
    """Result of a successful authentication."""
    principal: Principal
    must_change_pw: bool
