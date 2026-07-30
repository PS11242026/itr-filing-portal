"""PAN / Aadhaar / IFSC validation."""

import pytest

from itrcore.validation import (validate_aadhaar, validate_ifsc,
                                validate_pan)


def test_pan_accepts_and_canonicalizes():
    assert validate_pan(" aaapl1234c ") == "AAAPL1234C"


@pytest.mark.parametrize("bad", ["", "AAAPL1234", "1AAPL1234C",
                                 "AAAPL12345", "AAAP L1234C X"])
def test_pan_rejects(bad):
    with pytest.raises(ValueError):
        validate_pan(bad)


def _valid_aadhaar() -> str:
    base = "23456789012"
    for d in range(10):
        try:
            return validate_aadhaar(base + str(d))
        except ValueError:
            continue
    raise AssertionError("no valid check digit found")


def test_aadhaar_verhoeff_roundtrip():
    good = _valid_aadhaar()
    assert validate_aadhaar(good) == good
    # flip one digit → checksum must fail
    flipped = ("9" if good[5] != "9" else "1")
    bad = good[:5] + flipped + good[6:]
    with pytest.raises(ValueError):
        validate_aadhaar(bad)


def test_aadhaar_rejects_bad_shapes():
    with pytest.raises(ValueError):
        validate_aadhaar("123")
    with pytest.raises(ValueError):
        validate_aadhaar("012345678901")  # cannot start with 0


def test_ifsc():
    assert validate_ifsc("hdfc0001234") == "HDFC0001234"
    with pytest.raises(ValueError):
        validate_ifsc("HDFC1001234")
