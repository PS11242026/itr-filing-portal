"""Counterparty extraction from noisy Indian bank narrations.

Handles both delimiter styles, e.g.
  HDFC: UPI-RAMESH KUMAR-ramesh@okhdfc-YESB0YBLUPI-512345678901-PAYMENT
  SBI:  TO TRANSFER-UPI/DR/512345678901/RAMESH K/SBIN/ramesh@oksbi/Pay--
The VPA is the strongest stable key; otherwise the surviving name tokens.
"""

import re

MODES = ("UPI", "NEFT", "IMPS", "RTGS", "NACH", "ACH", "ECS", "POS",
         "ATM", "CHQ", "CASH", "INT", "TPT", "MMT", "FT")

# No hyphen in the local part: bank narrations use '-' as a field
# delimiter, and including it makes the match swallow the preceding token.
_VPA_RE = re.compile(r"\b([a-z0-9._]{2,}@[a-z][a-z0-9]{1,})\b",
                     re.IGNORECASE)
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_REF_RE = re.compile(r"^\d{6,}$")
_DATEISH_RE = re.compile(r"^\d{1,2}[-/]?[A-Z]{3}[-/]?\d{2,4}$|^\d{6,8}$")

_FILLER = {"PAYMENT", "TRANSFER", "REF", "REFNO", "NO", "DR", "CR", "MB",
           "IB", "INB", "BY", "TO", "FROM", "MR", "MRS", "MS", "M/S",
           "PVT", "PRIVATE", "LTD", "LIMITED", "LLP", "PAY", "COLLECT",
           "OKHDFC", "OKSBI", "OKICICI", "OKAXIS", "YBL", "PAYTM", "A/C",
           "AC", "SBIN", "HDFC", "ICIC", "UTIB", "KKBK", "BANK"}


def parse(narration: str) -> tuple[str | None, str, str]:
    """→ (mode, counterparty_key, counterparty_display)."""
    up = (narration or "").upper()
    mode = None
    for m in MODES:
        if re.search(rf"(?<![A-Z]){m}(?![A-Z])", up):
            mode = m
            break

    vpa = _VPA_RE.search(narration or "")
    if vpa:
        key = vpa.group(1).lower()
        name = _name_tokens(up)
        return mode, key, (name or key)

    name = _name_tokens(up)
    if name:
        return mode, name, name
    return mode, up.strip()[:40] or "UNKNOWN", up.strip()[:40] or "UNKNOWN"


def _name_tokens(up: str) -> str:
    tokens = [t.strip() for t in re.split(r"[-/|*:,]+", up) if t.strip()]
    keep: list[str] = []
    for tok in tokens:
        if tok in MODES or tok in _FILLER:
            continue
        if (_IFSC_RE.match(tok) or _REF_RE.match(tok)
                or _DATEISH_RE.match(tok)):
            continue
        words = [w for w in tok.split()
                 if w not in _FILLER and w not in MODES
                 and not _REF_RE.match(w) and not _IFSC_RE.match(w)]
        if not words:
            continue
        if all(re.fullmatch(r"[A-Z][A-Z.&' ]*", w) or w.isalpha()
               for w in words):
            keep.append(" ".join(words))
    if not keep:
        return ""
    keep.sort(key=len, reverse=True)
    return keep[0][:60]
