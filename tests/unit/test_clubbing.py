"""Clubbing of income (Sec 60–64) + Sec 10(32) minor exemption."""

from itrcore.tax import clubbing, serde
from itrcore.tax.ayconfig import load as load_cfg
from itrcore.tax.engine import compute_assessment
from itrcore.tax.types import AssessmentInputs, ClubbedIncome

CFG = load_cfg("2026-27").raw["clubbing"]


def test_spouse_income_clubbed_in_full():
    res = clubbing.compute(
        (ClubbedIncome(section="64(1)", amount=200000),), CFG)
    assert res.total == 200000


def test_minor_child_gets_10_32_exemption():
    res = clubbing.compute(
        (ClubbedIncome(section="64(1A)", amount=50000, minor_child=True),),
        CFG)
    assert res.total == 50000 - 1500


def test_minor_exemption_capped_at_income():
    res = clubbing.compute(
        (ClubbedIncome(section="64(1A)", amount=1000, minor_child=True),),
        CFG)
    assert res.total == 0  # min(1500, 1000) exemption


def test_clubbed_income_flows_into_total_income():
    inputs = AssessmentInputs(
        ay="2026-27",
        clubbing=(ClubbedIncome(section="64(1)", amount=300000),))
    result = compute_assessment(inputs)
    assert result.new.total_income == 300000
    assert result.old.total_income == 300000


def test_serde_roundtrip_preserves_clubbing():
    inputs = AssessmentInputs(
        ay="2026-27",
        clubbing=(ClubbedIncome(section="64(1A)", amount=50000,
                                minor_child=True,
                                description="Minor FD interest"),))
    back = serde.inputs_from_dict(serde.inputs_to_dict(inputs))
    assert back.clubbing[0].minor_child is True
    assert back.clubbing[0].amount == 50000
    assert back.clubbing[0].section == "64(1A)"
