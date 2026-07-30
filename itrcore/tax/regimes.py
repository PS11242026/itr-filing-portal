"""Per-regime pipeline: heads → set-offs → GTI → VI-A → TI → tax →
rebate → surcharge (with marginal relief) → cess → interest → payable."""

from datetime import date

from dateutil.relativedelta import relativedelta

from itrcore.tax import (business, capital_gains, clubbing, deductions,
                         exempt, house_property, interest, other_sources,
                         salary)
from itrcore.tax.ayconfig import AYConfig
from itrcore.tax.rounding import pct, r10
from itrcore.tax.types import (ADJUSTMENT_TARGETS, AssessmentInputs,
                               CGResult, HeadResult, Line, RegimeResult)


def age_category(dob: date | None, fy_end: date) -> str:
    if dob is None:
        return "general"
    age = relativedelta(fy_end, dob).years
    if age >= 80:
        return "super_senior"
    if age >= 60:
        return "senior"
    return "general"


def _slabs_for(cfg: AYConfig, regime: str, age_cat: str) -> list[dict]:
    reg = cfg.regime(regime)
    if regime == "new":
        return reg["slabs"]
    key = {"general": "slabs_general", "senior": "slabs_senior",
           "super_senior": "slabs_super_senior"}[age_cat]
    return reg[key]


def slab_tax(slabs: list[dict], amount: int) -> int:
    tax = 0
    prev = 0
    for band in slabs:
        upto = band["upto"] if band["upto"] is not None else 10 ** 15
        take = min(amount, upto) - prev
        if take > 0:
            tax += pct(take, band["rate"])
        prev = upto
        if amount <= upto:
            break
    return tax


def _zero_band(slabs: list[dict]) -> int:
    first = slabs[0]
    return first["upto"] if first["rate"] == 0 else 0


def slab_breakdown(slabs: list[dict], amount: int) -> tuple[Line, ...]:
    """Band-wise split of ``slab_tax(slabs, amount)`` for the computation
    sheet, phrased the way practitioner software prints it: the zero band
    as a NIL line, then 'Tax on ₹take (upper − lower) @ rate%' per band
    reached — matching the slab_tax arithmetic exactly."""
    lines: list[Line] = []
    prev = 0
    for band in slabs:
        upto = band["upto"] if band["upto"] is not None else 10 ** 15
        take = min(amount, upto) - prev
        if take > 0:
            if band["rate"]:
                lines.append(Line(
                    f"Tax on ₹{take:,} (₹{min(amount, upto):,} − "
                    f"₹{prev:,}) @ {band['rate']}%", "Slabs",
                    pct(take, band["rate"])))
            else:
                lines.append(Line(f"Tax on ₹{take:,}", "Slabs", 0))
        prev = upto
        if amount <= upto:
            break
    return tuple(lines)


def _surcharge_rate(bands: list[dict], ti: int) -> tuple[int, int, int]:
    """Returns (rate, threshold_of_current_band, rate_of_band_below)."""
    rate, threshold, below = 0, 0, 0
    for band in sorted(bands, key=lambda b: b["above"]):
        if ti > band["above"]:
            below = rate
            rate = band["rate"]
            threshold = band["above"]
    return rate, threshold, below


def _special_taxes(cg: CGResult, cfg_raw: dict) -> tuple[int, list[Line]]:
    cgc = cfg_raw["capital_gains"]
    lines: list[Line] = []
    total = 0
    parts = [
        ("STCG u/s 111A", cg.stcg_111a, cgc["stcg_111a_rate"], "111A"),
        ("LTCG u/s 112A (beyond exemption)", cg.ltcg_112a_taxable,
         cgc["ltcg_112a_rate"], "112A"),
        ("LTCG u/s 112 @ 12.5%", cg.ltcg_125, cgc["ltcg_112_rate"], "112"),
        ("LTCG u/s 112 @ 20% (indexed option)", cg.ltcg_20_indexed,
         cgc["property_grandfather_indexed_rate"], "112"),
    ]
    for label, base, rate, section in parts:
        if base > 0:
            t = pct(base, rate)
            lines.append(Line(f"Tax on {label} (₹{base:,} @ {rate}%)",
                              section, t))
            total += t
    return total, lines


def _head_adjusted(head: HeadResult, entries, *,
                   floor_zero: bool = False) -> HeadResult:
    """Apply manual add/less rows to a computed head, each as its own
    visible line."""
    if not entries:
        return head
    new_lines = list(head.lines)
    for a in entries:
        new_lines.append(Line(f"Adjustment: {a.label or 'manual'}",
                              "Manual", a.amount))
    amount = head.amount + sum(a.amount for a in entries)
    if floor_zero and amount < 0:
        new_lines.append(Line("Head floored at zero (adjustments exceed "
                              "the income)", "Manual", -amount))
        amount = 0
    return HeadResult(head=head.head, amount=amount, lines=tuple(new_lines))


def compute_regime(inputs: AssessmentInputs, cfg: AYConfig,
                   regime: str) -> RegimeResult:
    raw = cfg.raw
    reg = cfg.regime(regime)
    fy_end = date.fromisoformat(raw["fy_end"])
    age_cat = age_category(inputs.personal.dob, fy_end)
    resident = inputs.personal.resident_status == "Resident"
    new_regime = regime == "new"
    lines: list[Line] = []

    # ---- manual add/less adjustments, bucketed by target ----
    unknown = sorted({a.target for a in inputs.adjustments
                      if a.target not in ADJUSTMENT_TARGETS})
    if unknown:
        raise ValueError(f"unknown adjustment target(s): "
                         f"{', '.join(unknown)}")

    def _adj(target: str):
        return [a for a in inputs.adjustments if a.target == target]

    os_adjustments = tuple(a for a in inputs.adjustments
                           if a.target.startswith("other_sources."))

    # ---- heads ----
    sal = _head_adjusted(salary.compute(inputs.salaries, reg),
                         _adj("salary"), floor_zero=True)
    hp = _head_adjusted(                       # before the 71B set-off cap
        house_property.compute(inputs.house_properties,
                               raw["house_property"],
                               new_regime=new_regime),
        _adj("house_property"))
    cg_cfg = {**raw["capital_gains"],
              "cg_regime_cutoff": raw["cg_regime_cutoff"]}
    cg = capital_gains.compute(inputs.capital_gains, cg_cfg,
                               resident=resident,
                               brought_forward=inputs.brought_forward)
    os_ = other_sources.compute(inputs.other_sources, reg,
                                adjustments=os_adjustments)
    biz = _head_adjusted(
        business.compute(inputs.businesses, inputs.regular_businesses,
                         raw["presumptive"]),
        _adj("business"))
    club = clubbing.compute(inputs.clubbing, raw["clubbing"])
    lines.extend(club.lines)
    ei = exempt.compute(inputs.exempt_incomes)   # Schedule EI — never in GTI

    # ---- HP loss set-off ----
    hp_amount = hp.amount
    hp_setoff = hp_amount
    hp_cf = 0
    hp_lapsed = 0
    if hp_amount < 0:                      # only possible in old regime
        cap = reg.get("hp_loss_setoff_cap", 0)
        hp_setoff = max(hp_amount, -cap)
        hp_cf = -(hp_amount - hp_setoff)
        if hp_cf:
            lines.append(Line(f"HP loss ₹{hp_cf:,} carried forward "
                              "(set-off capped at ₹2,00,000)", "71B", 0))

    gti = (sal.amount + hp_setoff + biz.amount + cg.head_total + os_.amount
           + club.total)
    special_total = cg.special_total
    normal_gti = gti - special_total

    # ---- Chapter VI-A ----
    os_eff = other_sources.adjusted_fields(inputs.other_sources,
                                           os_adjustments)
    total_interest_income = (os_eff["savings_interest"]
                             + os_eff["fd_interest"])
    ded_total, ded_lines = deductions.compute(
        inputs.deductions, raw, regime=regime,
        senior=age_cat in ("senior", "super_senior"),
        cap_base=normal_gti,
        savings_interest=os_eff["savings_interest"],
        total_interest=total_interest_income)

    ti_unrounded = max(0, gti - ded_total)
    ti = r10(ti_unrounded)                 # 288A
    normal_income = max(0, ti - special_total)

    # ---- basic-exemption adjustment against special-rate income ----
    slabs = _slabs_for(cfg, regime, age_cat)
    adj_cg = cg
    if resident:
        shortfall = max(0, _zero_band(slabs) - normal_income)
        if shortfall and special_total:
            order = ["stcg_111a", "ltcg_20_indexed", "ltcg_125",
                     "ltcg_112a_taxable"]
            vals = {"stcg_111a": cg.stcg_111a,
                    "ltcg_20_indexed": cg.ltcg_20_indexed,
                    "ltcg_125": cg.ltcg_125,
                    "ltcg_112a_taxable": cg.ltcg_112a_taxable}
            remaining = shortfall
            for key in order:
                take = min(remaining, vals[key])
                vals[key] -= take
                remaining -= take
                if remaining <= 0:
                    break
            absorbed = shortfall - remaining
            if absorbed:
                lines.append(Line("Unexhausted basic exemption adjusted "
                                  "against special-rate income", "112/111A",
                                  -absorbed))
            adj_cg = CGResult(head_total=cg.head_total,
                              stcg_slab=cg.stcg_slab,
                              stcg_111a=vals["stcg_111a"],
                              ltcg_112a_taxable=vals["ltcg_112a_taxable"],
                              ltcg_125=vals["ltcg_125"],
                              ltcg_20_indexed=vals["ltcg_20_indexed"],
                              cf_stcl=cg.cf_stcl, cf_ltcl=cg.cf_ltcl,
                              lines=cg.lines)

    # ---- tax ----
    # Partial integration (Finance Act): net agricultural income above the
    # threshold is added for RATE purposes only — tax(normal + agri) minus
    # tax(exemption limit + agri) — whenever non-agri TI exceeds the basic
    # exemption. Applies under both regimes, on slab-rate income only.
    agri = ei.agri_income
    zero_band = _zero_band(slabs)
    integrate = (agri > raw["exempt_income"]["agri_integration_threshold"]
                 and ti > zero_band)

    def _slab_normal(amount: int) -> int:
        if not integrate:
            return slab_tax(slabs, amount)
        return max(0, slab_tax(slabs, amount + agri)
                   - slab_tax(slabs, zero_band + agri))

    stax = _slab_normal(normal_income)
    agri_extra = stax - slab_tax(slabs, normal_income)
    if agri_extra:
        lines.append(Line(f"Agricultural income ₹{agri:,} aggregated for "
                          "rate purposes (partial integration)", "10(1)",
                          agri_extra))
    # Band-wise presentation of the slab tax for the computation sheet.
    if stax == 0:
        slab_lines: tuple[Line, ...] = ()
    elif integrate:
        parts = list(slab_breakdown(slabs, normal_income + agri))
        relief_agg = slab_tax(slabs, zero_band + agri)
        if relief_agg:
            parts.append(Line(
                "Less: tax on basic exemption + agricultural income "
                f"(₹{zero_band + agri:,})", "10(1)", -relief_agg))
        slab_lines = tuple(parts)
    else:
        slab_lines = slab_breakdown(slabs, normal_income)
    special_tax, special_lines = _special_taxes(adj_cg, raw)

    # ---- rebate 87A ----
    rb = reg["rebate_87a"]
    rebate = 0
    if ti <= rb["ti_limit"]:
        base = stax if rb["slab_tax_only"] else (
            stax + special_tax
            - pct(adj_cg.ltcg_112a_taxable,
                  raw["capital_gains"]["ltcg_112a_rate"]))
        rebate = min(rb["max_rebate"], max(0, base))
    elif rb.get("marginal_relief"):
        excess = ti - rb["ti_limit"]
        if stax > excess:
            rebate = stax - excess
            lines.append(Line("87A marginal relief applied", "87A", 0))

    if rb["slab_tax_only"]:
        tax_after_rebate = max(0, stax - rebate) + special_tax
    else:
        tax_after_rebate = max(0, stax + special_tax - rebate)

    # ---- surcharge with marginal relief ----
    rate, threshold, rate_below = _surcharge_rate(reg["surcharge"], ti)
    surcharge = 0
    relief = 0
    if rate:
        cap_special = reg["surcharge_cap_special_income"]
        tax_normal_part = max(0, stax - rebate)  # rebate is 0 at these TIs
        surcharge = (pct(tax_normal_part, rate)
                     + pct(special_tax, min(rate, cap_special)))
        # marginal relief: total tax+surcharge cannot exceed tax at the
        # threshold plus the income above it
        reduce_by = ti - threshold
        normal_at_t = max(0, normal_income - reduce_by)
        stax_t = _slab_normal(normal_at_t)
        surch_t = (pct(stax_t, rate_below)
                   + pct(special_tax, min(rate_below, cap_special)))
        ceiling = stax_t + special_tax + surch_t + reduce_by
        total_now = tax_after_rebate + surcharge
        if total_now > ceiling:
            relief = total_now - ceiling
            lines.append(Line("Marginal relief on surcharge", "Surcharge",
                              -relief))

    cess = pct(tax_after_rebate + surcharge - relief, raw["cess_rate"])
    liability = tax_after_rebate + surcharge - relief + cess

    # ---- credits & interest ----
    tds_total = sum(t.amount for t in inputs.tds)
    for a in (*_adj("tds"), *_adj("tcs")):
        kind = "TCS" if a.target == "tcs" else "TDS"
        lines.append(Line(f"Adjustment: {a.label or 'manual'} "
                          f"({kind} credit)", "Manual", a.amount))
        tds_total += a.amount
    tds_total = max(0, tds_total)
    advance = sum(c.amount for c in inputs.challans
                  if c.kind == "advance")
    self_asst = sum(c.amount for c in inputs.challans
                    if c.kind == "self_assessment")

    icfg = raw["interest"]
    due_date = date.fromisoformat(raw["due_date"])
    ay_start = fy_end + relativedelta(days=1)
    assessed = max(0, liability - tds_total)
    skip_234bc = (icfg.get("senior_no_advance_tax_without_business")
                  and age_cat in ("senior", "super_senior"))
    i234b = 0 if skip_234bc else interest.s234b(
        assessed, advance, inputs.filing_date, ay_start,
        icfg["rate_pm_234b"], icfg["advance_tax_threshold"])
    i234c = 0 if skip_234bc else interest.s234c(
        assessed, inputs.challans, icfg["s234c_schedule"],
        icfg["rate_pm_234c"], icfg["advance_tax_threshold"])
    balance_for_234a = max(0, liability - tds_total - advance - self_asst)
    i234a = interest.s234a(balance_for_234a, inputs.filing_date, due_date,
                           icfg["rate_pm_234a"])
    fee = interest.s234f(ti, inputs.filing_date, due_date,
                         raw["late_fee_234f"])

    payable = r10(liability + i234a + i234b + i234c + fee
                  - tds_total - advance - self_asst)

    heads = {"salary": sal, "house_property": hp, "business": biz,
             "capital_gains": cg, "other_sources": os_}

    return RegimeResult(
        regime=regime, heads=heads, gti=gti,
        hp_loss_setoff=min(0, hp_setoff),
        hp_loss_carried_forward=hp_cf, hp_loss_lapsed=hp_lapsed,
        deductions_total=ded_total, deduction_lines=ded_lines,
        total_income=ti, normal_income=normal_income,
        slab_tax=stax, special_tax_lines=tuple(special_lines),
        special_tax=special_tax, rebate_87a=rebate,
        surcharge=surcharge, marginal_relief=relief, cess=cess,
        tax_liability=liability, tds_total=tds_total,
        advance_tax_paid=advance, self_assessment_paid=self_asst,
        interest_234a=i234a, interest_234b=i234b, interest_234c=i234c,
        fee_234f=fee, payable_or_refund=payable, lines=tuple(lines),
        exempt_total=ei.total, exempt_lines=ei.lines,
        slab_lines=slab_lines)
