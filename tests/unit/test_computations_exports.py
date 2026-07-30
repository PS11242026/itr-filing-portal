"""Computation snapshots repo + sheet exporters (smoke + integrity)."""

from datetime import date

from itrcore.authz import mint_scope
from itrcore.tax import serde
from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import (AssessmentInputs, PersonalInfo,
                               SalaryIncome)


def _result_dict():
    inputs = AssessmentInputs(
        ay="2026-27", personal=PersonalInfo(dob=date(1990, 5, 10)),
        salaries=(SalaryIncome(employer_name="Acme",
                               gross_salary=1500000),))
    result = compute_assessment(inputs)
    return serde.inputs_to_dict(inputs), serde.result_to_dict(result), result


def test_snapshot_versioning_and_finalize(partner, taxpayer_id):
    from itrcore.db.repos import computations as repo
    scope = mint_scope(partner, taxpayer_id)
    inputs_d, result_d, result = _result_dict()

    repo.save_inputs(scope, "2026-27", inputs_d)
    assert repo.load_inputs(scope, "2026-27") == inputs_d

    v1 = repo.save_snapshot(scope, "2026-27", inputs_d, result_d,
                            engine_version=result.engine_version,
                            config_hash=result.config_hash,
                            inputs_digest=result.inputs_digest)
    v2 = repo.save_snapshot(scope, "2026-27", inputs_d, result_d,
                            engine_version=result.engine_version,
                            config_hash=result.config_hash,
                            inputs_digest=result.inputs_digest)
    assert (v1, v2) == (1, 2)

    repo.finalize(scope, "2026-27", 1)
    repo.finalize(scope, "2026-27", 2)
    rows = {r["version_no"]: r["status"]
            for r in repo.list_snapshots(scope, "2026-27")}
    assert rows == {1: "superseded", 2: "final"}


def test_exports_render_all_three_formats(fresh_db):
    from itrcore.export import (computation_html, computation_pdf,
                                computation_xlsx)
    _, result_d, _ = _result_dict()
    kwargs = dict(taxpayer_name="Ramesh Kumar", pan_masked="XXXXX1234C",
                  ay="2026-27", result=result_d)

    html = computation_html.render(**kwargs)
    assert "Computation of Total Income" in html
    assert "115BAC" in html and "XXXXX1234C" in html

    xlsx = computation_xlsx.build(**kwargs)
    assert xlsx[:2] == b"PK" and len(xlsx) > 4000   # valid zip container

    pdf = computation_pdf.build(**kwargs)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 1500


def test_xlsx_includes_business_head(fresh_db):
    import io

    import openpyxl

    from itrcore.export import computation_xlsx
    from itrcore.tax.types import AssessmentInputs, PresumptiveBusiness

    inputs = AssessmentInputs(
        ay="2026-27",
        businesses=(PresumptiveBusiness(section="44ADA",
                                        gross_receipts=2000000),))
    result_d = serde.result_to_dict(compute_assessment(inputs))
    xlsx = computation_xlsx.build(taxpayer_name="Biz Client",
                                  pan_masked="XXXXX1234C", ay="2026-27",
                                  result=result_d)

    wb = openpyxl.load_workbook(io.BytesIO(xlsx))
    sched = wb["Schedule (new)"]
    text = " ".join(str(c.value) for row in sched.iter_rows()
                    for c in row if c.value).lower()
    assert "business or profession" in text


def test_xlsx_computation_sheet_shows_slab_ladder_and_tds_sections():
    """The practitioner-format sheet: slab-band subheads with the total,
    the NIL zero band, special-rate subheads and section-wise TDS —
    verified by reading the workbook back."""
    import io

    import openpyxl

    from itrcore.export import computation_xlsx
    from itrcore.tax import serde
    from itrcore.tax.engine import compute_assessment

    inputs_dict = {
        "ay": "2026-27",
        "personal": {"dob": "1940-01-01", "resident_status": "Resident"},
        "salaries": [{"employer_name": "CENTRAL PENSION",
                      "employer_tan": "JPRC05652D",
                      "gross_salary": 1419144}],
        "other_sources": {"savings_interest": 12024, "fd_interest": 25515,
                          "dividend": 36854},
        "tds": [{"tan": "JPRC05652D", "deductor": "CENTRAL PENSION",
                 "section": "192", "amount": 84887},
                {"tan": "CALI01639B", "deductor": "ITC LIMITED",
                 "section": "194", "amount": 3041}],
    }
    result = compute_assessment(serde.inputs_from_dict(inputs_dict))
    data = computation_xlsx.build(
        taxpayer_name="RAMESH KUMAR", pan_masked="XXXXX1234C",
        ay="2026-27", result=serde.result_to_dict(result)
        | {"recommended": result.recommended, "saving": result.saving},
        taxpayer={"dob": "1940-01-01", "resident_status": "Resident"},
        inputs=inputs_dict)

    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames[0] == "Computation"
    col_a = [str(c.value) for c in wb["Computation"]["A"] if c.value]
    joined = "\n".join(col_a)
    assert "COMPUTATION OF TOTAL INCOME" in joined
    assert "COMPUTATION OF TAX ON TOTAL INCOME" in joined
    # the slab ladder: NIL band + at least one rated band + the bold total
    assert any("Tax on ₹" in v and "@" not in v for v in col_a)
    assert any("@ 5%" in v for v in col_a)
    assert any(v.startswith("TAX ON ₹") and "SLAB RATES" in v
               for v in col_a)
    # section-wise TDS subheads
    assert any(v.startswith("SECTION 192") for v in col_a)
    assert any(v.startswith("SECTION 194") for v in col_a)
    assert any("288B" in v for v in col_a)
