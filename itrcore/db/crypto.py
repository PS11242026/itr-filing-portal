"""Field-level encryption (Fernet) and deterministic HMAC blind indexes.

Keys live OUTSIDE the repository: %APPDATA%\\itr-portal\\keys.json by
default, override with ITR_PORTAL_KEY_FILE (the test suite points this at a
temp file). The file is generated on first run — BACK IT UP: without it the
encrypted PAN/Aadhaar/account-number columns and the document store are
unrecoverable.

Blind indexes allow exact-match lookup (e.g. find taxpayer by PAN) without
decrypt-scanning: bidx = HMAC-SHA256(separate key, NFKC-uppercased value).
"""

import base64
import hashlib
import hmac
import json
import os
import unicodedata
from pathlib import Path

from cryptography.fernet import Fernet, MultiFernet


def key_file_path() -> Path:
    override = os.environ.get("ITR_PORTAL_KEY_FILE")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "itr-portal" / "keys.json"


def _generate_key_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fernet_keys": [Fernet.generate_key().decode("ascii")],
        "bidx_key": base64.b64encode(os.urandom(32)).decode("ascii"),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class FieldCipher:
    def __init__(self, fernet_keys: list[str], bidx_key: bytes) -> None:
        if not fernet_keys:
            raise ValueError("at least one Fernet key required")
        self._fernet = MultiFernet([Fernet(k) for k in fernet_keys])
        self._bidx_key = bidx_key

    @classmethod
    def load(cls) -> "FieldCipher":
        path = key_file_path()
        if not path.exists():
            _generate_key_file(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["fernet_keys"],
                   base64.b64decode(data["bidx_key"]))

    # -- strings (PAN, Aadhaar, account numbers) --
    def enc_str(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode("utf-8"))

    def dec_str(self, blob: bytes) -> str:
        return self._fernet.decrypt(bytes(blob)).decode("utf-8")

    # -- bytes (document store) --
    def enc_bytes(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def dec_bytes(self, blob: bytes) -> bytes:
        return self._fernet.decrypt(bytes(blob))

    def blind_index(self, value: str) -> str:
        norm = unicodedata.normalize("NFKC", value).strip().upper()
        norm = "".join(norm.split())
        return hmac.new(self._bidx_key, norm.encode("utf-8"),
                        hashlib.sha256).hexdigest()


_cipher: FieldCipher | None = None


def get_cipher() -> FieldCipher:
    global _cipher
    if _cipher is None:
        _cipher = FieldCipher.load()
    return _cipher


def reset_cipher_cache() -> None:
    """Test hook: force reload after ITR_PORTAL_KEY_FILE changes."""
    global _cipher
    _cipher = None
