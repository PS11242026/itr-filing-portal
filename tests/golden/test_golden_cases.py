"""Run every golden case; each expected value must match to the rupee."""

from pathlib import Path

import pytest
import yaml

from itrcore.tax import serde
from itrcore.tax.engine import compute_assessment

CASES_DIR = Path(__file__).parent / "cases"
CASES = sorted(CASES_DIR.glob("*.yaml"))


@pytest.mark.parametrize("case_path", CASES,
                         ids=[p.stem for p in CASES])
def test_golden_case(case_path):
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    inputs = serde.inputs_from_dict(case["inputs"])
    result = compute_assessment(inputs)
    mismatches = []
    for regime_name, expectations in case["expected"].items():
        regime = getattr(result, regime_name)
        for field, expected in expectations.items():
            actual = getattr(regime, field)
            if actual != expected:
                mismatches.append(
                    f"{regime_name}.{field}: expected {expected:,}, "
                    f"engine produced {actual:,}")
    assert not mismatches, (
        f"{case['name']} (verified_against_utility="
        f"{case.get('verified_against_utility')}):\n  "
        + "\n  ".join(mismatches))
