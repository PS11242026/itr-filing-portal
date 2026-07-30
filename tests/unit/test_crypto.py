"""Field encryption and blind indexes."""

from itrcore.db.crypto import get_cipher


def test_string_roundtrip(fresh_db):
    c = get_cipher()
    blob = c.enc_str("AAAPL1234C")
    assert blob != b"AAAPL1234C"
    assert c.dec_str(blob) == "AAAPL1234C"


def test_bytes_roundtrip(fresh_db):
    c = get_cipher()
    data = b"%PDF-1.7 fake statement bytes" * 100
    assert c.dec_bytes(c.enc_bytes(data)) == data


def test_blind_index_deterministic_and_normalized(fresh_db):
    c = get_cipher()
    a = c.blind_index("aaapl1234c")
    b = c.blind_index("  AAAPL 1234C ")
    assert a == b
    assert len(a) == 64
    assert c.blind_index("AAAPL1234D") != a


def test_encryption_is_randomized_but_bidx_is_not(fresh_db):
    c = get_cipher()
    assert c.enc_str("X") != c.enc_str("X")          # Fernet nonce
    assert c.blind_index("X") == c.blind_index("X")  # deterministic
