"""Validation for Indian tax identifiers: PAN, Aadhaar (Verhoeff),
IFSC."""

import re

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

# Verhoeff dihedral tables
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def validate_pan(pan: str) -> str:
    """Return the canonical (upper-cased) PAN or raise ValueError."""
    canon = (pan or "").strip().upper().replace(" ", "")
    if not _PAN_RE.match(canon):
        raise ValueError("PAN must look like AAAPL1234C (5 letters, "
                         "4 digits, 1 letter)")
    return canon


def pan_holder_type(pan: str) -> str:
    """Fourth PAN character → holder type ('P' = individual)."""
    kinds = {"P": "Individual", "H": "HUF", "C": "Company", "F": "Firm",
             "A": "AOP", "T": "Trust", "B": "BOI", "L": "Local authority",
             "J": "Artificial juridical person", "G": "Government"}
    return kinds.get(pan[3], "Unknown")


def validate_aadhaar(aadhaar: str) -> str:
    """Return canonical 12-digit Aadhaar or raise (Verhoeff check)."""
    canon = re.sub(r"[\s-]", "", aadhaar or "")
    if not re.match(r"^[2-9][0-9]{11}$", canon):
        raise ValueError("Aadhaar must be 12 digits and cannot start with 0/1")
    c = 0
    for i, ch in enumerate(reversed(canon)):
        c = _D[c][_P[i % 8][int(ch)]]
    if c != 0:
        raise ValueError("Aadhaar checksum is invalid — please re-check")
    return canon


def validate_ifsc(ifsc: str) -> str:
    canon = (ifsc or "").strip().upper()
    if not _IFSC_RE.match(canon):
        raise ValueError("IFSC must look like HDFC0001234")
    return canon
