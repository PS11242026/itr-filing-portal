"""Head: Capital gains — post-23-Jul-2024 rate regime (all FY 2025-26
transfers).

Covers: Sec 50C stamp-duty deeming (110% tolerance), 112A grandfathering
(FMV 31-01-2018), the resident lower-of option for land/building acquired
before 23-07-2024 (12.5% unindexed vs 20% indexed), intra-year and
brought-forward loss set-offs.

Loss absorption order is deterministic and taxpayer-favorable (highest-rate
buckets first); calibrate against the ITD utility via golden tests.
"""

from datetime import date

from dateutil.relativedelta import relativedelta

from itrcore.tax.rounding import pct
from itrcore.tax.types import BroughtForwardLosses, CGEntry, CGResult, Line


def _fy_of(d: date) -> str:
    year = d.year if d.month >= 4 else d.year - 1
    return f"{year}-{str(year + 1)[2:]}"


def _is_long_term(entry: CGEntry, months: int) -> bool:
    if entry.acquisition_date is None:
        # No date on file: honour the reporter's own ST/LT determination
        # (AIS Asset Type / declared sheet column) before assuming LT.
        if entry.term_override in ("short", "long"):
            return entry.term_override == "long"
        return True  # missing date — staff must confirm; assume long-term
    return entry.transfer_date > (entry.acquisition_date
                                  + relativedelta(months=months))


def _indexed(amount: int, cii: dict, acq_fy: str, transfer_fy: str) -> int:
    base = cii.get(acq_fy)
    cur = cii.get(transfer_fy)
    if not base or not cur:
        return amount
    return (amount * cur) // base


def compute(entries: tuple[CGEntry, ...], cg_cfg: dict, *,
            resident: bool,
            brought_forward: BroughtForwardLosses) -> CGResult:
    if not entries and not (brought_forward.stcl or brought_forward.ltcl):
        return CGResult(head_total=0)

    lines: list[Line] = []
    stcg_slab = 0
    stcg_111a = 0
    pool_112a = 0
    pool_125 = 0
    pool_20 = 0

    cutoff = date.fromisoformat(cg_cfg["cg_regime_cutoff"]) if \
        "cg_regime_cutoff" in cg_cfg else None
    grandfather_112a = date.fromisoformat(cg_cfg["grandfather_112a_date"])
    holding = cg_cfg["holding_months"]
    cii = cg_cfg["cii"]

    for i, e in enumerate(entries, start=1):
        tag = e.description or f"Asset {i} ({e.asset_class})"
        fvc = e.full_value_consideration

        if e.asset_class == "property" and e.stamp_duty_value:
            tolerance = pct(fvc, cg_cfg["sec50c_tolerance_pct"])
            if e.stamp_duty_value > tolerance:
                lines.append(Line(
                    f"{tag}: stamp-duty value ₹{e.stamp_duty_value:,} "
                    f"exceeds 110% of consideration ₹{fvc:,} — SDV deemed "
                    "as full value", "50C", e.stamp_duty_value - fvc))
                fvc = e.stamp_duty_value

        long_term = _is_long_term(e, holding[e.asset_class])
        cost = max(0, e.cost_of_acquisition)

        if (long_term and e.asset_class in ("listed_equity_stt", "equity_mf")
                and e.fmv_31jan2018 and e.acquisition_date
                and e.acquisition_date <= grandfather_112a):
            step_up = min(e.fmv_31jan2018, fvc)
            if step_up > cost:
                lines.append(Line(f"{tag}: cost stepped up to FMV "
                                  "31-01-2018", "112A", step_up - cost))
                cost = step_up

        gain = (fvc - max(0, e.transfer_expenses) - cost
                - max(0, e.cost_of_improvement))
        exemption = min(max(0, e.exemption_claimed), max(0, gain))
        if exemption:
            lines.append(Line(
                f"{tag}: exemption {e.exemption_section or '54/54EC/54F'}",
                e.exemption_section or "54", -exemption))
        gain -= exemption

        if long_term:
            if e.asset_class in ("listed_equity_stt", "equity_mf"):
                pool_112a += gain
                lines.append(Line(f"{tag}: LTCG (112A pool)", "112A", gain))
            elif e.asset_class == "property":
                use_20 = False
                if (resident and gain > 0 and e.acquisition_date and cutoff
                        and e.acquisition_date < cutoff):
                    acq_fy = _fy_of(e.acquisition_date)
                    t_fy = _fy_of(e.transfer_date)
                    indexed_cost = _indexed(cost, cii, acq_fy, t_fy)
                    indexed_impr = _indexed(max(0, e.cost_of_improvement),
                                            cii, acq_fy, t_fy)
                    indexed_gain = max(0, (fvc - max(0, e.transfer_expenses)
                                           - indexed_cost - indexed_impr
                                           - exemption))
                    tax_125 = pct(gain, cg_cfg["ltcg_112_rate"])
                    tax_20 = pct(indexed_gain,
                                 cg_cfg["property_grandfather_indexed_rate"])
                    if tax_20 < tax_125:
                        use_20 = True
                        pool_20 += indexed_gain
                        lines.append(Line(
                            f"{tag}: grandfathered option — 20% on indexed "
                            f"gain ₹{indexed_gain:,} beats 12.5% on "
                            f"₹{gain:,}", "112", indexed_gain))
                if not use_20:
                    pool_125 += gain
                    lines.append(Line(f"{tag}: LTCG at 12.5% (unindexed)",
                                      "112", gain))
            else:
                pool_125 += gain
                lines.append(Line(f"{tag}: LTCG at 12.5%", "112", gain))
        else:
            if e.asset_class == "listed_equity_stt":
                stcg_111a += gain
                lines.append(Line(f"{tag}: STCG (111A)", "111A", gain))
            else:
                stcg_slab += gain
                lines.append(Line(f"{tag}: STCG at slab rates", "45", gain))

    # ---- intra-year set-off (favorable order), then brought-forward ----
    def absorb(loss: int, buckets: list[str], values: dict) -> int:
        """Absorb `loss` (positive number) into positive buckets in order;
        returns the unabsorbed remainder."""
        for name in buckets:
            if loss <= 0:
                break
            take = min(loss, max(0, values[name]))
            values[name] -= take
            loss -= take
        return loss

    values = {"stcg_slab": stcg_slab, "stcg_111a": stcg_111a,
              "112a": pool_112a, "125": pool_125, "20": pool_20}

    # current-year LTCL first (only against LTCG)
    ltcl = sum(-values[k] for k in ("112a", "125", "20") if values[k] < 0)
    for k in ("112a", "125", "20"):
        values[k] = max(0, values[k])
    cf_ltcl = absorb(ltcl, ["20", "125", "112a"], values)

    # current-year STCL (against STCG, then LTCG)
    stcl = sum(-values[k] for k in ("stcg_slab", "stcg_111a")
               if values[k] < 0)
    for k in ("stcg_slab", "stcg_111a"):
        values[k] = max(0, values[k])
    cf_stcl = absorb(stcl, ["stcg_slab", "stcg_111a", "20", "125", "112a"],
                     values)

    # brought-forward losses
    bf_stcl_left = absorb(max(0, brought_forward.stcl),
                          ["stcg_slab", "stcg_111a", "20", "125", "112a"],
                          values)
    bf_ltcl_left = absorb(max(0, brought_forward.ltcl),
                          ["20", "125", "112a"], values)
    cf_stcl += bf_stcl_left
    cf_ltcl += bf_ltcl_left
    if brought_forward.stcl or brought_forward.ltcl:
        lines.append(Line("Brought-forward losses applied", "74",
                          -(brought_forward.stcl - bf_stcl_left
                            + brought_forward.ltcl - bf_ltcl_left)))

    # 112A exemption applies to the netted pool
    taxable_112a = max(0, values["112a"]
                       - cg_cfg["ltcg_112a_exemption"])
    if values["112a"] > 0:
        lines.append(Line(
            f"112A exemption on first "
            f"₹{cg_cfg['ltcg_112a_exemption']:,}", "112A",
            -min(values["112a"], cg_cfg["ltcg_112a_exemption"])))

    head_total = (values["stcg_slab"] + values["stcg_111a"] + taxable_112a
                  + values["125"] + values["20"])
    if cf_stcl or cf_ltcl:
        lines.append(Line(
            f"Losses carried forward: STCL ₹{cf_stcl:,}, LTCL ₹{cf_ltcl:,}",
            "74", 0))
    lines.append(Line("Taxable capital gains", "45", head_total))

    return CGResult(head_total=head_total,
                    stcg_slab=values["stcg_slab"],
                    stcg_111a=values["stcg_111a"],
                    ltcg_112a_taxable=taxable_112a,
                    ltcg_125=values["125"],
                    ltcg_20_indexed=values["20"],
                    cf_stcl=cf_stcl, cf_ltcl=cf_ltcl,
                    lines=tuple(lines))
