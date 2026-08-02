"""Capital gains: Sec 50C, 112A, grandfathered indexation option, set-offs.

Brought-forward losses moved to :mod:`itrcore.tax.setoff` (they may only be
applied once every head is known), so they are covered in test_tax_setoff.py.
"""

from datetime import date

from itrcore.tax import capital_gains
from itrcore.tax.ayconfig import load
from itrcore.tax.types import CGEntry

CFG = load("2026-27")
CG_CFG = {**CFG.raw["capital_gains"],
          "cg_regime_cutoff": CFG.raw["cg_regime_cutoff"]}


def test_sec_50c_deems_sdv_beyond_110pct():
    entry = CGEntry(asset_class="property",
                    transfer_date=date(2025, 9, 15),
                    acquisition_date=date(2015, 6, 1),
                    full_value_consideration=6000000,
                    stamp_duty_value=7000000,       # > 110% of 60L (66L)
                    cost_of_acquisition=3000000)
    res = capital_gains.compute((entry,), CG_CFG, resident=True)
    # 12.5% unindexed on 40L (5,00,000) beats 20% indexed
    # (indexed cost 30L×376/254 = 44,40,944 → gain 25,59,056 → 5,11,811)
    assert res.ltcg_125 == 4000000
    assert res.ltcg_20_indexed == 0
    assert any(ln.section == "50C" for ln in res.lines)


def test_sec_50c_within_tolerance_not_deemed():
    entry = CGEntry(asset_class="property",
                    transfer_date=date(2025, 9, 15),
                    acquisition_date=date(2015, 6, 1),
                    full_value_consideration=6000000,
                    stamp_duty_value=6500000,       # ≤ 110% → no deeming
                    cost_of_acquisition=3000000)
    res = capital_gains.compute((entry,), CG_CFG, resident=True)
    # No 50C deeming; and here the grandfathered indexed option wins:
    # indexed cost 30L×376/254 = 44,40,944 → gain 15,59,056 @ 20%
    # (3,11,811) beats 30L @ 12.5% (3,75,000)
    assert not any(ln.section == "50C" for ln in res.lines)
    assert res.ltcg_20_indexed == 1559056
    assert res.ltcg_125 == 0


def test_grandfathered_indexed_option_chosen_when_cheaper():
    # High-cost recent-ish property where indexation wipes out the gain:
    # cost 50L (FY 2018-19, CII 280), sale 70L (FY 2025-26, CII 376)
    # indexed cost = 50L×376/280 = 67,14,285 → indexed gain 2,85,715
    # tax20 = 57,143 < tax12.5 on 20L (2,50,000) → indexed option wins
    entry = CGEntry(asset_class="property",
                    transfer_date=date(2025, 9, 15),
                    acquisition_date=date(2018, 6, 1),
                    full_value_consideration=7000000,
                    cost_of_acquisition=5000000)
    res = capital_gains.compute((entry,), CG_CFG, resident=True)
    assert res.ltcg_20_indexed == 285715
    assert res.ltcg_125 == 0


def test_112a_pooling_exemption_and_grandfather_stepup():
    entries = (
        CGEntry(asset_class="listed_equity_stt",
                transfer_date=date(2025, 8, 1),
                acquisition_date=date(2017, 1, 1),
                full_value_consideration=800000,
                cost_of_acquisition=300000,
                fmv_31jan2018=500000),              # step-up to 5L
        CGEntry(asset_class="listed_equity_stt",
                transfer_date=date(2026, 1, 10),
                acquisition_date=date(2024, 6, 1),
                full_value_consideration=300000,
                cost_of_acquisition=200000),
    )
    res = capital_gains.compute(entries, CG_CFG, resident=True)
    # pool = (8L−5L) + (3L−2L) = 4,00,000 — GROSS: the ₹1.25L in 112A(2) is
    # a rate concession applied by the tax stage, not a cut in the income.
    assert res.ltcg_112a == 400000
    assert res.head_total == 400000


def test_stcg_111a_and_slab_buckets():
    entries = (
        CGEntry(asset_class="listed_equity_stt",
                transfer_date=date(2025, 12, 1),
                acquisition_date=date(2025, 5, 1),
                full_value_consideration=500000,
                cost_of_acquisition=420000),
        CGEntry(asset_class="other",
                transfer_date=date(2025, 12, 1),
                acquisition_date=date(2025, 1, 1),
                full_value_consideration=200000,
                cost_of_acquisition=150000),
    )
    res = capital_gains.compute(entries, CG_CFG, resident=True)
    assert res.stcg_111a == 80000
    assert res.stcg_slab == 50000
    assert res.head_total == 130000


def test_ltcl_sets_off_against_ltcg_only():
    entries = (
        CGEntry(asset_class="listed_equity_stt",       # LTCL −1,00,000
                transfer_date=date(2025, 12, 1),
                acquisition_date=date(2023, 1, 1),
                full_value_consideration=200000,
                cost_of_acquisition=300000),
        CGEntry(asset_class="listed_equity_stt",       # STCG +80,000
                transfer_date=date(2025, 12, 1),
                acquisition_date=date(2025, 6, 1),
                full_value_consideration=300000,
                cost_of_acquisition=220000),
    )
    res = capital_gains.compute(entries, CG_CFG, resident=True)
    assert res.stcg_111a == 80000       # untouched by LTCL
    assert res.cf_ltcl == 100000        # carried forward


def test_equity_mf_stcg_is_111a_not_slab():
    # Sec 111A covers units of an equity-oriented fund on which STT is paid,
    # not just shares — a debt/liquid fund stays at the slab.
    entries = (
        CGEntry(asset_class="equity_mf", description="SBI PSU Fund",
                transfer_date=date(2025, 11, 6),
                acquisition_date=date(2025, 4, 1),
                full_value_consideration=232, cost_of_acquisition=217),
        CGEntry(asset_class="listed_other", description="Liquid fund",
                transfer_date=date(2026, 3, 20),
                acquisition_date=date(2025, 4, 20),
                full_value_consideration=50000, cost_of_acquisition=49952),
    )
    res = capital_gains.compute(entries, CG_CFG, resident=True)
    assert res.stcg_111a == 15
    assert res.stcg_slab == 48


def test_non_resident_property_has_no_indexation_option():
    entry = CGEntry(asset_class="property", description="Flat",
                    transfer_date=date(2025, 12, 20),
                    acquisition_date=date(2011, 8, 4),
                    full_value_consideration=4000000,
                    stamp_duty_value=4000000,
                    cost_of_acquisition=1730375)
    res = capital_gains.compute((entry,), CG_CFG, resident=False)
    assert res.ltcg_125 == 2269625      # 12.5% unindexed, no option
    assert res.ltcg_20_indexed == 0
    assert any("RESIDENT" in ln.label for ln in res.reco_lines)
