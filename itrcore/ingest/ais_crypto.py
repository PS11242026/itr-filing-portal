"""Decrypt the AIS / TIS JSON exports downloaded from the ITD portal.

The files the e-filing/compliance portal serves as ``*_AIS_*.json`` /
``*_TIS_*.json`` are NOT plain JSON — they are the payload the official AIS
offline utility consumes, wrapped as::

    <IV : 32 hex chars><salt : 32 hex chars><base64 (or hex) AES ciphertext>

The utility derives the key from a password of the form
``<pan lowercase><CONST><dob ddmmyyyy>`` via PBKDF2-HMAC-SHA256 (1000
iterations, 32-byte key) and decrypts with AES-256-CBC / PKCS#7.  ``CONST``
is a fixed string embedded in the utility (the same for every file), so with
the taxpayer's own PAN + DOB the file decrypts locally — no portal round-trip.

This module ONLY reverses that envelope; the plaintext is the real AIS/TIS
JSON, parsed by :mod:`itrcore.ingest.ais` / :mod:`itrcore.ingest.tis`.

Nothing here talks to the network and no secret is logged.
"""

import base64
import binascii
import hashlib
import re

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Fixed middle segment the AIS utility splices between PAN and DOB when it
# builds the key password. Verified against the published community
# decryptors and against real v15 downloads.
_PASSWORD_CONST = "GQ39%*g"
_PBKDF2_ITERS = 1000
_KEY_LEN = 32                       # AES-256
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}")


class AisDecryptError(ValueError):
    """The envelope could not be decrypted (usually a wrong PAN/DOB)."""


def looks_encrypted(data: bytes) -> bool:
    """True when ``data`` is an ITD-encrypted AIS/TIS envelope rather than
    already-plain JSON. Cheap structural check: a 64-hex-char prefix that is
    NOT itself the start of a JSON document."""
    try:
        text = data.decode("utf-8-sig", "strict").lstrip()
    except UnicodeDecodeError:
        return False
    if text[:1] in ("{", "["):
        return False
    return bool(_HEX64.match(text))


def normalize_dob(dob: str) -> str:
    """Coerce a stored DOB to the ``ddmmyyyy`` form the key password needs.

    Accepts ``dd/mm/yyyy``, ``dd-mm-yyyy``, ``yyyy-mm-dd`` or an already-bare
    ``ddmmyyyy``. Raises ``AisDecryptError`` if it cannot be understood."""
    s = re.sub(r"[^0-9]", "", dob or "")
    if len(s) != 8:
        raise AisDecryptError(
            "date of birth must have 8 digits (ddmmyyyy) to decrypt the AIS "
            f"file — got {dob!r}")
    # yyyymmdd → ddmmyyyy (portal stores/displays dd/mm/yyyy, but be tolerant)
    if s[:2] in ("19", "20") and int(s[4:6] or 0) <= 12:
        yyyy, mm, dd = s[:4], s[4:6], s[6:8]
        return f"{dd}{mm}{yyyy}"
    return s


def iso_dob(dob: str | None) -> str | None:
    """Best-effort ``yyyy-mm-dd`` from any DOB the AIS/staff supply
    (dd/mm/yyyy, yyyy-mm-dd, ddmmyyyy …) — the storage form for the
    taxpayer record. None when the text is not a date."""
    try:
        s = normalize_dob(dob or "")            # → ddmmyyyy
    except AisDecryptError:
        return None
    return f"{s[4:8]}-{s[2:4]}-{s[0:2]}"


def build_password(pan: str, dob: str) -> str:
    """The key password the AIS utility uses: pan.lower()+CONST+ddmmyyyy."""
    pan = (pan or "").strip()
    if not re.fullmatch(r"[A-Za-z]{5}[0-9]{4}[A-Za-z]", pan):
        raise AisDecryptError(f"PAN {pan!r} is not a valid 10-char PAN")
    return pan.lower() + _PASSWORD_CONST + normalize_dob(dob)


def _decode_body(body: str) -> bytes:
    body = body.strip()
    try:
        return base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        try:
            return bytes.fromhex(body)
        except ValueError as exc:                       # pragma: no cover
            raise AisDecryptError(
                "AIS ciphertext is neither valid base64 nor hex") from exc


def decrypt(data: bytes, *, pan: str, dob: str) -> bytes:
    """Return the plaintext AIS/TIS JSON bytes from an encrypted envelope.

    Raises :class:`AisDecryptError` if the PAN/DOB are wrong (detected by an
    invalid PKCS#7 pad or a plaintext that is not JSON)."""
    try:
        text = data.decode("utf-8-sig", "strict").strip()
    except UnicodeDecodeError as exc:
        raise AisDecryptError("AIS file is not text — is it really the "
                              "portal's JSON download?") from exc
    if not _HEX64.match(text):
        raise AisDecryptError("AIS file does not start with the expected "
                              "IV+salt header — it may be already decrypted "
                              "or a different format")
    iv, salt, body = bytes.fromhex(text[:32]), bytes.fromhex(text[32:64]), \
        text[64:]
    key = hashlib.pbkdf2_hmac(
        "sha256", build_password(pan, dob).encode("utf-8"), salt,
        _PBKDF2_ITERS, _KEY_LEN)
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = dec.update(_decode_body(body)) + dec.finalize()

    pad = plain[-1] if plain else 0
    if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        raise AisDecryptError(
            "could not decrypt the AIS file — check the client's PAN and "
            "date of birth (the file is keyed to them)")
    plain = plain[:-pad]
    if plain[:1] not in (b"{", b"["):
        raise AisDecryptError(
            "AIS decrypted but the result is not JSON — the PAN/DOB may be "
            "wrong, or this is an unsupported utility version")
    return plain
