"""Auto-fill: assemble a draft computation from imported feeds."""

from itrcore.analysis import autofill
from itrcore.authz import mint_scope
from itrcore.db.repos import computations as comp_repo
from itrcore.db.repos import importers

AY = "2026-27"


def test_salary_and_tds_from_form16_when_no_26as(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.save_form16(
        scope, AY, employer_name="Acme Ltd", employer_tan="MUMA12345B",
        gross_salary_paise=1500000_00, exempt_allowances_paise=50000_00,
        prof_tax_paise=2500_00, tds_paise=120000_00)

    a = autofill.assemble(scope, AY)

    assert len(a.inputs["salaries"]) == 1
    s = a.inputs["salaries"][0]
    assert s["employer_name"] == "Acme Ltd"
    assert s["gross_salary"] == 1500000
    assert s["exempt_allowances"] == 50000
    assert s["prof_tax"] == 2500
    # No 26AS → TDS falls back to Form 16 section 192.
    assert a.inputs["tds"] == [{"tan": "MUMA12345B", "deductor": "Acme Ltd",
                                "section": "192", "amount": 120000}]
    assert any("No Form 26AS" in f for f in a.follow_ups)
    assert "Salary" in a.filled_heads


def test_26as_supersedes_form16_tds_no_double_count(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.save_form16(
        scope, AY, employer_name="Acme Ltd", employer_tan="MUMA12345B",
        gross_salary_paise=1500000_00, exempt_allowances_paise=0,
        prof_tax_paise=0, tds_paise=120000_00)
    importers.import_26as(scope, AY, [
        {"tan": "MUMA12345B", "deductor_name": "ACME LTD", "section": "192",
         "amount_paid_paise": 1500000_00, "tds_paise": 120000_00},
        {"tan": "DELB55555D", "deductor_name": "BONUS PAYER",
         "section": "194", "amount_paid_paise": 200000_00,
         "tds_paise": 20000_00}])

    a = autofill.assemble(scope, AY)

    # TDS is 26AS only (2 rows) — not Form 16 + 26AS (which would double the
    # ₹1,20,000 salary TDS).
    assert len(a.inputs["tds"]) == 2
    assert sum(t["amount"] for t in a.inputs["tds"]) == 140000
    assert any("double counting" in f.lower() for f in a.follow_ups)


def test_other_sources_from_ais(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "interest_savings", "description": "SB interest",
         "amount_paise": 12000_00},
        {"category": "interest_deposits", "description": "FD interest",
         "amount_paise": 45000_00},
        {"category": "dividend", "description": "Dividend",
         "amount_paise": 8000_00}], "ais.json")

    a = autofill.assemble(scope, AY)

    os_ = a.inputs["other_sources"]
    assert os_["savings_interest"] == 12000
    assert os_["fd_interest"] == 45000
    assert os_["dividend"] == 8000
    assert "Interest & dividend" in a.filled_heads


def test_ais_capital_gains_and_rent_raise_followups(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "sale_securities", "description": "Sale of securities",
         "amount_paise": 500000_00},
        {"category": "rent", "description": "Rent received",
         "amount_paise": 240000_00}], "ais.json")

    a = autofill.assemble(scope, AY)

    # AIS totals never fabricate CG/HP rows — they prompt for the detail
    # (securities sales point at the AIS-pre-filled worksheet).
    assert a.inputs["capital_gains"] == []
    assert any("PRE-FILLED" in f for f in a.follow_ups)
    assert any("House Property" in f for f in a.follow_ups)


def test_base_manual_entries_preserved(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.save_form16(
        scope, AY, employer_name="Acme", employer_tan="",
        gross_salary_paise=1000000_00, exempt_allowances_paise=0,
        prof_tax_paise=0, tds_paise=0)
    base = {"ay": AY, "deductions": [["80C", 150000]],
            "capital_gains": [{"asset_class": "property",
                               "transfer_date": "2025-09-15"}],
            "other_sources": {"family_pension": 30000}}

    a = autofill.assemble(scope, AY, base=base)

    assert a.inputs["deductions"] == [["80C", 150000]]
    assert a.inputs["capital_gains"][0]["asset_class"] == "property"
    assert a.inputs["other_sources"]["family_pension"] == 30000
    assert a.inputs["salaries"][0]["gross_salary"] == 1000000


def test_prior_year_losses_rolled_forward(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    prior = "2025-26"
    result = {"recommended": "new",
              "new": {"heads": {"capital_gains": {"cf_stcl": 40000,
                                                  "cf_ltcl": 25000}},
                      "hp_loss_carried_forward": 15000}}
    comp_repo.save_snapshot(scope, prior, {"ay": prior}, result,
                            engine_version="x", config_hash="y",
                            inputs_digest="z")
    comp_repo.finalize(scope, prior, 1)

    a = autofill.assemble(scope, AY)

    assert a.inputs["brought_forward"] == {"hp_loss": 15000, "stcl": 40000,
                                           "ltcl": 25000}
    assert any(s.origin.startswith("Prior year") for s in a.sources)


def test_tis_derived_value_preferred_over_ais(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "interest_savings", "description": "SB",
         "amount_paise": 12000_00}], "ais.json")
    importers.import_tis(scope, AY, [
        {"category": "interest_savings", "description": "SB",
         "processed_value_paise": 12000_00,
         "derived_value_paise": 15000_00}], "tis.json")

    a = autofill.assemble(scope, AY)

    # TIS derived (₹15,000) wins over the raw AIS aggregate (₹12,000).
    assert a.inputs["other_sources"]["savings_interest"] == 15000
    assert any(s.origin.startswith("TIS") for s in a.sources)


def test_ais_tis_disagreement_flagged(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "interest_deposits", "description": "FD",
         "amount_paise": 50000_00}], "ais.json")
    importers.import_tis(scope, AY, [
        {"category": "interest_deposits", "description": "FD",
         "processed_value_paise": 50000_00,
         "derived_value_paise": 70000_00}], "tis.json")

    a = autofill.assemble(scope, AY)

    assert any("disagree" in f.lower() for f in a.follow_ups)
    assert a.inputs["other_sources"]["fd_interest"] == 70000


def test_empty_state_reports_nothing_to_fill(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)

    a = autofill.assemble(scope, AY)

    assert a.filled_heads == ()
    assert any("No Form 16" in f for f in a.follow_ups)
    assert a.inputs["ay"] == AY


def test_manual_salary_rows_survive_form16_merge(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.save_form16(
        scope, AY, employer_name="Acme Ltd", employer_tan="MUMA12345B",
        gross_salary_paise=1500000_00, exempt_allowances_paise=0,
        prof_tax_paise=0, tds_paise=0)
    base = {"salaries": [
        # same employer typed manually — Form 16 must supersede it...
        {"employer_name": "Acme Ltd", "employer_tan": "MUMA12345B",
         "gross_salary": 1400000},
        # ...but a second, manual-only employer must be KEPT.
        {"employer_name": "Side Gig LLP", "employer_tan": "",
         "gross_salary": 300000}]}

    a = autofill.assemble(scope, AY, base=base)

    rows = {s["employer_name"]: s["gross_salary"]
            for s in a.inputs["salaries"]}
    assert rows == {"Acme Ltd": 1500000, "Side Gig LLP": 300000}
    assert any(s.origin == "Manual entry (kept)" and
               s.detail == "Side Gig LLP" for s in a.sources)


def test_manual_salary_passes_through_without_imports(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    base = {"salaries": [{"employer_name": "Solo Employer",
                          "gross_salary": 800000}]}
    a = autofill.assemble(scope, AY, base=base)
    assert a.inputs["salaries"] == base["salaries"]
    assert any(s.head == "Salary" and s.origin == "Manual entry (kept)"
               for s in a.sources)


def test_manual_tds_row_survives_26as_merge(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_26as(scope, AY, [
        {"tan": "SBIN00001A", "deductor_name": "SBI", "section": "194A",
         "amount_paid_paise": 30000_00, "tds_paise": 3000_00}])
    base = {"tds": [
        # covered by 26AS (same TAN) — must not double count
        {"tan": "SBIN00001A", "deductor": "SBI", "section": "194A",
         "amount": 2999},
        # manual-only deductor — must be kept
        {"tan": "PUNE11111Z", "deductor": "Rent Payer", "section": "194I",
         "amount": 12000}]}

    a = autofill.assemble(scope, AY, base=base)

    amounts = {t["tan"]: t["amount"] for t in a.inputs["tds"]}
    assert amounts == {"SBIN00001A": 3000, "PUNE11111Z": 12000}
    assert any(s.origin == "Manual entry (kept)" and "Rent Payer"
               in s.detail for s in a.sources)


def test_manual_heads_get_classified_source_notes(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    base = {
        "house_properties": [{"kind": "let_out", "address": "Jaipur flat",
                              "annual_rent_received": 240000}],
        "capital_gains": [{"asset_class": "property",
                           "description": "Plot sale",
                           "transfer_date": "2025-12-01",
                           "full_value_consideration": 5000000}],
        "exempt_incomes": [{"section": "10(1)", "description": "farm",
                            "amount": 90000}],
        "deductions": [["80C", 150000]],
    }
    a = autofill.assemble(scope, AY, base=base)
    heads = {(s.head, s.detail) for s in a.sources}
    assert ("House property", "Jaipur flat") in heads
    assert ("Capital gains", "Plot sale") in heads
    assert ("Exempt income", "10(1) farm") in heads
    assert ("Deductions", "u/s 80C") in heads


def test_salary_derived_from_26as_192_amount_paid(partner, taxpayer_id):
    # Real-world shape: pension via 192 with amount paid, no Form 16.
    scope = mint_scope(partner, taxpayer_id)
    importers.import_26as(scope, AY, [
        {"tan": "JPRC05652D",
         "deductor_name": "CENTRAL PENSION DISTRIBUTION MONITORING CELL",
         "section": "192", "amount_paid_paise": 1419144_00,
         "tds_paise": 84887_00},
        {"tan": "CALI01639B", "deductor_name": "ITC LIMITED",
         "section": "194", "amount_paid_paise": 2006_00,
         "tds_paise": 200_00},
        # zero amount paid must not create a salary row
        {"tan": "MUMX00000X", "deductor_name": "ZERO PAY LTD",
         "section": "192", "amount_paid_paise": 0, "tds_paise": 0}])

    a = autofill.assemble(scope, AY)

    assert len(a.inputs["salaries"]) == 1
    row = a.inputs["salaries"][0]
    assert row["gross_salary"] == 1419144
    assert row["employer_tan"] == "JPRC05652D"
    assert "Salary" in a.filled_heads
    assert any(s.head == "Salary" and s.origin.startswith("Form 26AS")
               for s in a.sources)
    assert any("section-192" in f for f in a.follow_ups)
    # re-running with the produced inputs as base must not duplicate rows
    again = autofill.assemble(scope, AY, base=a.inputs)
    assert len(again.inputs["salaries"]) == 1


def test_salary_from_ais_aggregate_without_26as(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "salary", "description": "Salary",
         "amount_paise": 1419144_00}], "ais.json")
    a = autofill.assemble(scope, AY)
    assert a.inputs["salaries"] == [{
        "employer_name": "As per AIS", "employer_tan": "",
        "gross_salary": 1419144, "exempt_allowances": 0, "prof_tax": 0}]
    assert any("AIS aggregate" in f for f in a.follow_ups)


def test_form16_supersedes_26as_derived_salary(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.save_form16(
        scope, AY, employer_name="CENTRAL PENSION DISTRIBUTION"
                                 " MONITORING CELL",
        employer_tan="JPRC05652D", gross_salary_paise=1419144_00,
        exempt_allowances_paise=50000_00, prof_tax_paise=0,
        tds_paise=84887_00)
    importers.import_26as(scope, AY, [
        {"tan": "JPRC05652D",
         "deductor_name": "CENTRAL PENSION DISTRIBUTION MONITORING CELL",
         "section": "192", "amount_paid_paise": 1419144_00,
         "tds_paise": 84887_00}])
    a = autofill.assemble(scope, AY)
    assert len(a.inputs["salaries"]) == 1
    assert a.inputs["salaries"][0]["exempt_allowances"] == 50000  # from F16


def test_ais_other_income_categories_fill_other_field(partner, taxpayer_id):
    # Real-world shape: interest from others + AIS other-income items must
    # land in the OS 'other' field, family pension in its own field.
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "interest_others", "description": "Interest from "
         "income tax refund", "amount_paise": 10616_00},
        {"category": "other_income", "description": "Winnings from online "
         "games", "amount_paise": 50000_00},
        {"category": "family_pension", "description": "Family pension",
         "amount_paise": 100624_00}], "ais.json")

    a = autofill.assemble(scope, AY)

    os_ = a.inputs["other_sources"]
    assert os_["other"] == 10616 + 50000
    assert os_["family_pension"] == 100624
    assert "Other income" in a.filled_heads
    assert any(s.detail.startswith("Interest — others") for s in a.sources)
    assert any("115BB" in f for f in a.follow_ups)


def test_ais_other_income_replacement_flagged(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "interest_others", "description": "IT refund interest",
         "amount_paise": 8000_00}], "ais.json")
    base = {"other_sources": {"other": 20000}}
    a = autofill.assemble(scope, AY, base=base)
    assert a.inputs["other_sources"]["other"] == 8000
    assert any("Other income" in f and "replaced" in f
               for f in a.follow_ups)


def test_manual_other_income_kept_without_imports(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    base = {"other_sources": {"other": 15000, "family_pension": 30000}}
    a = autofill.assemble(scope, AY, base=base)
    assert a.inputs["other_sources"]["other"] == 15000
    assert a.inputs["other_sources"]["family_pension"] == 30000
    assert ("Other sources", "Other income") in {(s.head, s.detail)
                                                 for s in a.sources}


def test_adjustments_preserved_through_autofill(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "interest_deposits", "description": "FD",
         "amount_paise": 455497_00}], "ais.json")
    base = {"adjustments": [
        {"target": "other_sources.fd_interest",
         "label": "INTT. ON NSC (accrued)", "amount": 10616},
        {"target": "tcs", "label": "TCS on car", "amount": 25000}]}

    a = autofill.assemble(scope, AY, base=base)

    assert a.inputs["adjustments"] == base["adjustments"]
    assert a.inputs["other_sources"]["fd_interest"] == 455497
    assert any(s.origin == "Manual adjustment (kept)"
               and "NSC" in s.detail for s in a.sources)
    assert any(s.head == "TDS" and s.origin == "Manual adjustment (kept)"
               for s in a.sources)


def _salary_block(part, code, source, paise):
    return {"part": part, "part_title": "t", "category": "Salary",
            "category_code": "SAL", "info_code": code,
            "description": "Salary", "source": source, "count": 1,
            "amount_paise": paise, "derived_paise": None, "bucket": "x",
            "detail_labels": [], "detail_rows": []}


def test_salary_payerwise_from_ais_doc_catches_retirement_payout(
        partner, taxpayer_id):
    # Kishori's real shape: the pension cell's Annexure-II gross (45.4L,
    # retirement payout, no TDS) dwarfs its 26AS 'amount paid' (4.7L).
    from itrcore.ingest import ais as ais_mod
    scope = mint_scope(partner, taxpayer_id)
    importers.save_ais_document(scope, AY, {"rows": [
        _salary_block("B1", "TDS-192",
                      "COMMANDENT SEVENTH BATTALLIAN R.A.C., BHARATPUR "
                      "(JPRC02895E)", 725503_00),
        _salary_block("B1", "TDS-192",
                      "CENTRAL PENSION DISTRIBUTION MONITORING CELL "
                      "(JPRC05652D)", 467854_00),
        _salary_block("B7", "TDS-Ann.II-SAL",
                      "CENTRAL PENSION DISTRIBUTION MONITORING CELL "
                      "(JPRC05652D)", 4544346_00),
        _salary_block("B7", "TDS-Ann.II-SAL",
                      "COMMANDENT SEVENTH BATTALLIAN R.A.C., BHARATPUR "
                      "(JPRC02895E)", 725503_00),
    ]}, "ais.json")
    importers.import_ais(scope, AY, ais_mod.flat_entries(
        ais_mod.document_from_dict({"rows": [
            _salary_block("B1", "TDS-192", "X (JPRC02895E)", 725503_00),
        ]})), "ais.json")          # flat aggregate present but not used
    importers.import_26as(scope, AY, [
        {"tan": "JPRC02895E", "deductor_name": "COMMANDENT SEVENTH "
         "BATTALLIAN R.A.C., BHARATPUR", "section": "192",
         "amount_paid_paise": 725503_00, "tds_paise": 75000_00},
        {"tan": "JPRC05652D", "deductor_name": "CENTRAL PENSION "
         "DISTRIBUTION MONITORING CELL", "section": "192",
         "amount_paid_paise": 467854_00, "tds_paise": 0}])

    a = autofill.assemble(scope, AY)

    rows = {s["employer_tan"]: s["gross_salary"]
            for s in a.inputs["salaries"]}
    assert rows == {"JPRC05652D": 4544346, "JPRC02895E": 725503}
    assert sum(rows.values()) == 5269849           # ties to the TIS figure
    assert any("retirement benefits" in f and "10(10A)" in f
               for f in a.follow_ups)
    assert any(s.origin == "AIS salary statement (payer-wise)"
               for s in a.sources)
    # Idempotent re-run: no duplicate rows.
    again = autofill.assemble(scope, AY, base=a.inputs)
    assert len(again.inputs["salaries"]) == 2


def test_capital_gains_auto_created_from_saved_ais_doc(partner, taxpayer_id):
    from test_cg_excel import _ais_doc_with_sales

    from itrcore.ingest import ais as ais_mod
    scope = mint_scope(partner, taxpayer_id)
    importers.save_ais_document(
        scope, AY, ais_mod.document_to_dict(_ais_doc_with_sales()),
        "ais.json")

    a = autofill.assemble(scope, AY)

    rows = a.inputs["capital_gains"]
    assert len(rows) == 3
    assert rows[0]["asset_class"] == "listed_equity_stt"
    assert rows[0]["term_override"] == "long"
    assert rows[1]["term_override"] == "short"
    assert "Capital gains" in a.filled_heads
    assert any(s.head == "Capital gains" and s.origin == "AIS SFT detail"
               for s in a.sources)
    assert any("purchase dates" in f for f in a.follow_ups)
    # Idempotent: the populated table is never rebuilt over.
    again = autofill.assemble(scope, AY, base=a.inputs)
    assert again.inputs["capital_gains"] == rows


def test_capital_gains_never_rebuilt_over_existing_rows(partner,
                                                        taxpayer_id):
    from test_cg_excel import _ais_doc_with_sales

    from itrcore.ingest import ais as ais_mod
    scope = mint_scope(partner, taxpayer_id)
    importers.save_ais_document(
        scope, AY, ais_mod.document_to_dict(_ais_doc_with_sales()),
        "ais.json")
    base = {"capital_gains": [{"asset_class": "property",
                               "description": "Plot",
                               "transfer_date": "2025-12-01",
                               "full_value_consideration": 5000000}]}
    a = autofill.assemble(scope, AY, base=base)
    assert a.inputs["capital_gains"] == base["capital_gains"]


def test_ais_family_pension_not_misread_as_salary(partner, taxpayer_id):
    from itrcore.ingest import ais as ais_mod
    assert ais_mod.classify("Family pension") == "family_pension"
    assert ais_mod.classify("Pension") == "salary"
    assert ais_mod.classify("Winnings from lottery") == "other_income"
    assert ais_mod.classify("Cash deposits") == "other"   # never income


def test_ais_vs_26as_salary_mismatch_flagged(partner, taxpayer_id):
    # Kishori's real shape: AIS salary far above the 26AS-192 payments.
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "salary", "description": "Salary",
         "amount_paise": 5269849_00}], "ais.json")
    importers.import_26as(scope, AY, [
        {"tan": "JPRC02895E", "deductor_name": "COMMANDENT",
         "section": "192", "amount_paid_paise": 725503_00,
         "tds_paise": 75000_00}])
    a = autofill.assemble(scope, AY)
    assert a.inputs["salaries"][0]["gross_salary"] == 725503
    assert any("verify" in f.casefold() and "26AS" in f
               for f in a.follow_ups)
