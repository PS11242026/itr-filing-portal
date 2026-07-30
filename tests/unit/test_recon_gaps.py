"""Pre-compute gap detection: imported AIS/TIS/26AS vs computation inputs."""

from itrcore.analysis import recon_gaps
from itrcore.authz import mint_scope
from itrcore.db.repos import importers

AY = "2026-27"


def _seed_imports(scope):
    importers.import_ais(scope, AY, [
        {"category": "interest_savings", "description": "SB",
         "amount_paise": 30000_00},
        {"category": "dividend", "description": "Div",
         "amount_paise": 12000_00},
        {"category": "salary", "description": "Salary",
         "amount_paise": 1200000_00},
        {"category": "sale_securities", "description": "Shares",
         "amount_paise": 500000_00},
    ], "ais.json")
    importers.import_26as(scope, AY, [
        {"tan": "MUMA12345B", "deductor_name": "Acme Ltd",
         "section": "192", "amount_paid_paise": 1200000_00,
         "tds_paise": 90000_00},
        {"tan": "SBIN00001A", "deductor_name": "SBI",
         "section": "194A", "amount_paid_paise": 30000_00,
         "tds_paise": 3000_00},
    ])


def test_gaps_flag_missing_income_and_tds(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    _seed_imports(scope)
    inputs = {"salaries": [], "house_properties": [], "capital_gains": [],
              "other_sources": {"savings_interest": 0, "dividend": 0},
              "tds": []}
    gaps = recon_gaps.find_gaps(scope, AY, inputs)
    heads = [g.head for g in gaps]
    assert "Salary" in heads
    assert "Other sources" in heads
    assert "Capital gains" in heads
    assert "TDS" in heads
    assert all(g.severity == "critical" for g in gaps)
    joined = " ".join(g.message for g in gaps)
    assert "₹1,200,000" in joined or "₹12,00,000" in joined


def test_no_gaps_when_inputs_carry_the_imports(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    _seed_imports(scope)
    inputs = {
        "salaries": [{"employer_name": "Acme Ltd",
                      "gross_salary": 1200000}],
        "house_properties": [],
        "capital_gains": [{"asset_class": "listed_equity_stt",
                           "transfer_date": "2025-09-01"}],
        "other_sources": {"savings_interest": 30000, "dividend": 12000},
        "tds": [{"tan": "MUMA12345B", "deductor": "Acme Ltd",
                 "section": "192", "amount": 90000},
                {"tan": "SBIN00001A", "deductor": "SBI",
                 "section": "194A", "amount": 3000}],
    }
    assert recon_gaps.find_gaps(scope, AY, inputs) == []


def test_small_differences_within_tolerance_pass(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "interest_savings", "description": "SB",
         "amount_paise": 30050_00}], "ais.json")
    inputs = {"salaries": [], "house_properties": [], "capital_gains": [],
              "other_sources": {"savings_interest": 30000}, "tds": []}
    assert recon_gaps.find_gaps(scope, AY, inputs) == []


def test_tds_total_shortfall_flagged_even_with_all_tans(partner,
                                                        taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_26as(scope, AY, [
        {"tan": "MUMA12345B", "deductor_name": "Acme Ltd",
         "section": "192", "amount_paid_paise": 0,
         "tds_paise": 90000_00}])
    inputs = {"salaries": [], "house_properties": [], "capital_gains": [],
              "other_sources": {},
              "tds": [{"tan": "MUMA12345B", "deductor": "Acme Ltd",
                       "section": "192", "amount": 40000}]}
    gaps = recon_gaps.find_gaps(scope, AY, inputs)
    assert len(gaps) == 1 and gaps[0].head == "TDS"
    assert "90,000" in gaps[0].message and "40,000" in gaps[0].message


def test_clean_slate_no_imports_no_gaps(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    assert recon_gaps.find_gaps(scope, AY, {"other_sources": {}}) == []


def test_other_income_and_family_pension_gaps(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "interest_others", "description": "IT refund int",
         "amount_paise": 10000_00},
        {"category": "other_income", "description": "Winnings",
         "amount_paise": 40000_00},
        {"category": "family_pension", "description": "Family pension",
         "amount_paise": 100624_00}], "ais.json")
    inputs = {"salaries": [], "other_sources": {"other": 0,
                                                "family_pension": 0}}
    gaps = recon_gaps.find_gaps(scope, AY, inputs)
    messages = " | ".join(g.message for g in gaps)
    assert "Other income" in messages
    assert "Family pension" in messages
    # Carrying the figures (other via base, family pension via an
    # adjustment line) clears both gaps.
    ok = {"salaries": [],
          "other_sources": {"other": 50000, "family_pension": 0},
          "adjustments": [{"target": "other_sources.family_pension",
                           "label": "Family pension", "amount": 100624}]}
    assert recon_gaps.find_gaps(scope, AY, ok) == []


def test_adjustments_count_toward_entered_totals(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    _seed_imports(scope)
    inputs = {
        "salaries": [{"employer_name": "Acme Ltd",
                      "gross_salary": 1100000}],
        "house_properties": [],
        "capital_gains": [{"asset_class": "listed_equity_stt",
                           "transfer_date": "2025-09-01"}],
        "other_sources": {"savings_interest": 22000, "dividend": 12000},
        "adjustments": [
            {"target": "salary", "label": "arrears", "amount": 100000},
            {"target": "other_sources.savings_interest",
             "label": "2nd bank", "amount": 8000}],
        "tds": [{"tan": "MUMA12345B", "deductor": "Acme Ltd",
                 "section": "192", "amount": 90000},
                {"tan": "SBIN00001A", "deductor": "SBI",
                 "section": "194A", "amount": 3000}],
    }
    assert recon_gaps.find_gaps(scope, AY, inputs) == []
