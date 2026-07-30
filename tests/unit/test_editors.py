"""Editor ⇄ records plumbing: the NaT regression and general round-trips.

An auto-created capital-gains row has acquisition_date=None; the editor
seed coerces empty date cells to pandas NaT, whose isoformat() is the
literal string 'NaT' — collecting that crashed the engine with
"Invalid isoformat string: 'NaT'"."""

from datetime import date

from itrapp.editors import records, seed

CG_COLUMNS = {"asset_class": "text", "description": "text",
              "acquisition_date": "date", "transfer_date": "date",
              "term_override": "text", "full_value_consideration": "int",
              "cost_of_acquisition": "int"}
CG_DEFAULTS = {"asset_class": "other", "description": "",
               "acquisition_date": None, "transfer_date": None,
               "term_override": "", "full_value_consideration": 0,
               "cost_of_acquisition": 0}


def test_empty_date_cell_collapses_to_none_not_nat():
    df = seed([{"asset_class": "listed_equity_stt", "description": "ITC",
                "acquisition_date": None, "transfer_date": "2025-12-16",
                "term_override": "long", "full_value_consideration": 133650,
                "cost_of_acquisition": 71758}], CG_COLUMNS)
    rec = records(df, CG_DEFAULTS)[0]
    assert rec["acquisition_date"] is None          # NOT the string 'NaT'
    assert rec["transfer_date"] == "2025-12-16"
    assert rec["term_override"] == "long"
    assert rec["full_value_consideration"] == 133650


def test_auto_created_cg_rows_flow_through_engine():
    # The exact crash: auto-created rows (no acquisition date) → editor
    # seed → collect → serde → engine.
    from itrcore.tax import serde
    from itrcore.tax.engine import compute_assessment

    df = seed([{"asset_class": "listed_equity_stt", "description": "AIS",
                "acquisition_date": None, "transfer_date": "2025-09-01",
                "term_override": "short",
                "full_value_consideration": 100000,
                "cost_of_acquisition": 40000}], CG_COLUMNS)
    rows = records(df, CG_DEFAULTS)
    result = compute_assessment(serde.inputs_from_dict(
        {"ay": "2026-27", "capital_gains": rows}))
    assert result.new.heads["capital_gains"].stcg_111a == 60000


def test_real_dates_and_values_round_trip():
    df = seed([{"asset_class": "property", "description": "Plot",
                "acquisition_date": "2020-01-05",
                "transfer_date": date(2025, 12, 1),
                "term_override": "", "full_value_consideration": 5000000,
                "cost_of_acquisition": 3500000}], CG_COLUMNS)
    rec = records(df, CG_DEFAULTS)[0]
    assert rec["acquisition_date"] == "2020-01-05"
    assert rec["transfer_date"] == "2025-12-01"


def test_all_default_rows_are_dropped():
    df = seed([{"asset_class": "other", "description": "",
                "acquisition_date": None, "transfer_date": None,
                "term_override": "", "full_value_consideration": 0,
                "cost_of_acquisition": 0}], CG_COLUMNS)
    assert records(df, CG_DEFAULTS) == []


def test_serde_treats_stored_nat_string_as_no_date():
    # Inputs saved during the crash window may carry the literal 'NaT'.
    from itrcore.tax import serde
    inputs = serde.inputs_from_dict({"ay": "2026-27", "capital_gains": [
        {"asset_class": "listed_equity_stt", "transfer_date": "2025-09-01",
         "acquisition_date": "NaT", "full_value_consideration": 10000,
         "cost_of_acquisition": 5000}]})
    assert inputs.capital_gains[0].acquisition_date is None
