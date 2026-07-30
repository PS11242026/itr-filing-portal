"""Sections 288A/288B and Rule 119A rounding."""

from hypothesis import given, strategies as st

from itrcore.tax.rounding import pct, r10, r100_floor


def test_r10_half_up():
    assert r10(0) == 0
    assert r10(4) == 0
    assert r10(5) == 10
    assert r10(14) == 10
    assert r10(15) == 20
    assert r10(123456) == 123460
    assert r10(-15) == -20
    assert r10(-14) == -10


def test_r100_floor():
    assert r100_floor(0) == 0
    assert r100_floor(99) == 0
    assert r100_floor(101) == 100
    assert r100_floor(-50) == 0


def test_pct_decimal_exact():
    assert pct(400000, 5) == 20000
    assert pct(100000, 12.5) == 12500
    assert pct(1, 12.5) == 0        # 0.125 rounds to 0
    assert pct(10, 12.5) == 1       # 1.25 → 1
    assert pct(52500, 4) == 2100


@given(st.integers(min_value=0, max_value=10**12))
def test_r10_is_multiple_of_ten_and_close(x):
    r = r10(x)
    assert r % 10 == 0
    assert abs(r - x) <= 5
