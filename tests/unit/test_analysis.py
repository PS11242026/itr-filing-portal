"""Analysis engine units: narration, recurrence, self-transfer, classify."""

from datetime import date

from itrcore.analysis import narration
from itrcore.analysis.classify import analyze
from itrcore.analysis.recurrence import find_series
from itrcore.analysis.self_transfer import pair_reversals, pair_self_transfers

FY_END = date(2026, 3, 31)


def test_narration_hdfc_style_vpa_wins():
    mode, key, display = narration.parse(
        "UPI-RAMESH KUMAR-ramesh@okhdfc-YESB0YBLUPI-512345678901-PAYMENT")
    assert mode == "UPI"
    assert key == "ramesh@okhdfc"
    assert "RAMESH" in display


def test_narration_sbi_style():
    mode, key, display = narration.parse(
        "TO TRANSFER-UPI/DR/512345678901/RAMESH K/SBIN/ramesh@oksbi/Pay--")
    assert mode == "UPI"
    assert key == "ramesh@oksbi"


def test_narration_neft_company():
    mode, key, _ = narration.parse("NEFT CR-ACME CONSULTING PVT LTD-SAL "
                                   "JUL 2025-N123456789")
    assert mode == "NEFT"
    assert "ACME CONSULTING" in key


def _credit(i, d, amount, key="acme", narr="NEFT SALARY ACME"):
    return {"id": i, "account_id": 1, "txn_date": d,
            "amount_paise": amount, "narration": narr,
            "counterparty_key": key, "counterparty_display": key.upper()}


def test_salary_series_detected():
    txns = [_credit(i, date(2025, 4 + i, 1), 15000000)
            for i in range(6)]                     # Apr..Sep, 1.5L monthly
    series = find_series(txns, _thresholds(), ["Acme Ltd"], FY_END)
    assert len(series) == 1
    assert series[0].kind == "salary"
    assert series[0].ended_early                  # stops in September


def test_rent_series_with_gap():
    dates = [date(2025, 4, 5), date(2025, 5, 5), date(2025, 6, 5),
             date(2025, 8, 5), date(2025, 9, 5)]  # July missing
    txns = [_credit(i, d, 2500000, key="ramesh@oksbi",
                    narr="UPI-RAMESH-ramesh@oksbi") for i, d in
            enumerate(dates)]
    series = find_series(txns, _thresholds(), [], FY_END)
    assert len(series) == 1
    s = series[0]
    assert s.kind == "rent_candidate"
    assert "2025-07" in s.missing_months
    assert s.ended_early


def _thresholds():
    from itrcore.analysis.classify import load_thresholds
    return load_thresholds()


def test_self_transfer_pairing():
    txns = [
        {"id": 1, "account_id": 1, "txn_date": date(2025, 5, 1),
         "amount_paise": -10000000, "mode": "NEFT",
         "counterparty_key": "own transfer"},
        {"id": 2, "account_id": 2, "txn_date": date(2025, 5, 2),
         "amount_paise": 10000000, "mode": "NEFT",
         "counterparty_key": "own transfer"},
        {"id": 3, "account_id": 1, "txn_date": date(2025, 5, 3),
         "amount_paise": -5000000, "mode": "NEFT",
         "counterparty_key": "someone"},
    ]
    assert pair_self_transfers(txns, 3) == {1, 2}


def test_reversal_pairing():
    txns = [
        {"id": 1, "account_id": 1, "txn_date": date(2025, 6, 1),
         "amount_paise": -450000, "counterparty_key": "flipkart"},
        {"id": 2, "account_id": 1, "txn_date": date(2025, 6, 3),
         "amount_paise": 450000, "counterparty_key": "flipkart"},
    ]
    assert pair_reversals(txns, 7) == {1, 2}


def test_classify_end_to_end_mixture():
    txns = [
        {"id": 1, "account_id": 1, "txn_date": date(2025, 4, 1),
         "amount_paise": 15000000, "narration": "NEFT-ACME LTD-SALARY APR"},
        {"id": 2, "account_id": 1, "txn_date": date(2025, 4, 3),
         "amount_paise": -45000, "narration": "UPI-SWIGGY-swiggy@icici"},
        {"id": 3, "account_id": 1, "txn_date": date(2025, 4, 5),
         "amount_paise": 125050, "narration": "INT.PD:SB INTEREST"},
        {"id": 4, "account_id": 1, "txn_date": date(2025, 4, 30),
         "amount_paise": -59000, "narration": "CONSOLIDATED CHARGES GST"},
        {"id": 5, "account_id": 1, "txn_date": date(2025, 8, 14),
         "amount_paise": 30000000,
         "narration": "IMPS-RAKESH VERMA-P2A-508812345"},
        {"id": 6, "account_id": 1, "txn_date": date(2025, 9, 2),
         "amount_paise": 7500000, "narration": "CASH DEP BRANCH ANDHERI"},
    ]
    out = analyze(txns, employer_names=["Acme Ltd"], fy_end=FY_END)
    cats = {u.txn_id: u.auto_category for u in out.updates}
    flags = {u.txn_id: u.flag_reason for u in out.updates}

    assert cats[1] == "income:salary"                 # keyword rule
    assert cats[2] == "non_income:personal"
    assert cats[3] == "income:other_sources:interest"
    assert cats[4] == "noise"
    assert cats[5] is None and flags[5] == "large unexplained credit"
    assert flags[6] == "cash deposit"

    topics = {q.topic for q in out.queries}
    assert "txn:5" in topics                          # identify big credit
    assert "txn:6" in topics                          # cash deposit
    gift_q = next(q for q in out.queries if q.topic == "txn:5")
    assert "56(2)(x)" in gift_q.question              # gift screening hint
