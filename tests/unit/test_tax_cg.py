"""Capital gains: Sec 50C, 112A, grandfathered indexation option, set-offs."""

from datetime import date

from itrcore.tax import capital_gains
from itrcore.tax.ayconfig import load
from itrcore.tax.types import BroughtForwardLosses, CGEntry

CFG = load("2026-27")
CG_CFG = {**CFG.raw["capital_gains"],
          "cg_regime_cutoff": CFG.raw["cg_regime_cutoff"]}
NO_BF = BroughtForwardLosses()


def test_sec_50c_deems_sdv_beyond_110pct():
    entry = CGEntry(asset_class="property",
                    transfer_date=date(2025, 9, 15),
                    acquisition_date=date(2015, 6, 1),
                    full_value_consideration=6000000,
                    stamp_duty_value=7000000,       # > 110% of 60L (66L)
                    cost_of_acquisition=3000000)
    res = capital_gains.compute((entry,), CG_CFG, resident=True,
                                brought_forward=NO_BF)
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
    res = capital_gains.compute((entry,), CG_CFG, resident=True,
                                brought_forward=NO_BF)
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
    res = capital_gains.compute((entry,), CG_CFG, resident=True,
                                brought_forward=NO_BF)
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
    res = capital_gains.compute(entries, CG_CFG, resident=True,
                                brought_forward=NO_BF)
    # pool = (8L−5L) + (3L−2L) = 4,00,000 → taxable beyond 1.25L = 2,75,000
    assert res.ltcg_112a_taxable == 275000


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
    res = capital_gains.compute(entries, CG_CFG, resident=True,
                                brought_forward=NO_BF)
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
    res = capital_gains.compute(entries, CG_CFG, resident=True,
                                brought_forward=NO_BF)
    assert res.stcg_111a == 80000       # untouched by LTCL
    assert res.cf_ltcl == 100000        # carried forward


def test_brought_forward_losses_applied():
    entries = (
        CGEntry(asset_class="listed_equity_stt",
                transfer_date=date(2025, 8, 1),
                acquisition_date=date(2020, 1, 1),
                full_value_consideration=600000,
                cost_of_acquisition=200000),           # 112A pool 4L
    )
    res = capital_gains.compute(entries, CG_CFG, resident=True,
                                brought_forward=BroughtForwardLosses(
                                    ltcl=150000))
    # pool 4L − b/f 1.5L = 2.5L → taxable beyond 1.25L = 1.25L
    assert res.ltcg_112a_taxable == 125000
    assert res.cf_ltcl == 0
