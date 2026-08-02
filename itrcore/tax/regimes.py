"""Per-regime pipeline: heads → set-offs → GTI → VI-A → TI → tax →
rebate → surcharge (with marginal relief) → cess → interest → payable."""

from dataclasses import replace
from datetime import date

from dateutil.relativedelta import relativedelta

from itrcore.tax import (business, capital_gains, clubbing, deductions,
                         exempt, house_property, interest, other_sources,
                         salary, setoff)
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
    """Tax on the special-rate buckets. ``cg.ltcg_112a`` arrives GROSS: the
    ₹1,25,000 in Sec 112A(2) is a rate concession applied here, never a
    deduction from total income (which is why GTI still carries the full
    gain — the way Genius and the ITD utility print it)."""
    cgc = cfg_raw["capital_gains"]
    lines: list[Line] = []
    total = 0
    exempt_112a = cgc["ltcg_112a_exemption"]
    taxable_112a = max(0, cg.ltcg_112a - exempt_112a)
    parts = [
        ("STCG u/s 111A", cg.stcg_111a, cgc["stcg_111a_rate"], "111A"),
        ("LTCG u/s 112A", taxable_112a, cgc["ltcg_112a_rate"], "112A"),
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
    if cg.ltcg_112a and not taxable_112a:
        lines.append(Line(
            f"Tax u/s 112A on LTCG ₹0 — ₹{cg.ltcg_112a:,} is within the "
            f"₹{exempt_112a:,} threshold", "112A", 0))
    return total, lines


def _tax_112a(cg: CGResult, cfg_raw: dict) -> int:
    cgc = cfg_raw["capital_gains"]
    return pct(max(0, cg.ltcg_112a - cgc["ltcg_112a_exemption"]),
               cgc["ltcg_112a_rate"])


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
    cg_raw_result = capital_gains.compute(inputs.capital_gains, cg_cfg,
                                          resident=resident)
    po_cfg = raw["exempt_income"]["post_office_savings_10_15"]
    os_ = other_sources.compute(inputs.other_sources, reg,
                                adjustments=os_adjustments,
                                post_office_cfg=po_cfg)
    biz_raw, spec_raw = business.compute(
        inputs.businesses, inputs.regular_businesses, raw["presumptive"])
    biz = _head_adjusted(biz_raw, _adj("business"))
    spec = spec_raw
    club = clubbing.compute(inputs.clubbing, raw["clubbing"])
    lines.extend(club.lines)
    ei = exempt.compute(inputs.exempt_incomes)   # Schedule EI — never in GTI

    # ---- Chapter VI: set off & carry forward (Sec 70–74) ----
    after, setoff_lines, loss_table = setoff.compute(
        salary=sal.amount, house_property=hp.amount, business=biz.amount,
        speculation=spec.amount, other_sources=os_.amount, cg=cg_raw_result,
        brought_forward=inputs.brought_forward, ay=inputs.ay,
        hp_setoff_cap=reg.get("hp_loss_setoff_cap", 0),
        hp_setoff_allowed=reg.get("hp_loss_setoff_against_other_heads", True))
    lines.extend(setoff_lines)
    cg = after["cg"]
    hp_setoff = -after["hp_loss_setoff"]
    hp_cf = after["hp_loss_carried_forward"]
    hp_lapsed = sum(row.lapsed for row in loss_table
                    if row.kind == "house_property")

    gti = (after["salary"] + after["house_property"] + after["business"]
           + after["speculation"] + cg.head_total + after["other_sources"]
           + club.total)
    special_total = cg.special_total
    normal_gti = gti - special_total

    # ---- Chapter VI-A ----
    os_eff = other_sources.adjusted_fields(inputs.other_sources,
                                           os_adjustments, po_cfg)
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

    slabs = _slabs_for(cfg, regime, age_cat)
    zero_band = _zero_band(slabs)
    agri = ei.agri_income
    rb = reg["rebate_87a"]

    def _tax_pipeline(ti_: int, normal_: int, cg_: CGResult,
                      narrate: list[Line] | None = None) -> dict:
        """Total income + special-rate buckets → liability.

        Runs twice: once for real, and once on the income the first proviso
        to Sec 234C(1) says nobody could have estimated in June, so that the
        early advance-tax instalments are tested against the right base.
        Only the real run narrates.
        """
        note = narrate if narrate is not None else []

        # Basic exemption unabsorbed by normal income shelters special-rate
        # income (proviso to Sec 111A(1)/112(1)/112A(2)) — residents only.
        adj = cg_
        if resident:
            shortfall = max(0, zero_band - normal_)
            if shortfall and cg_.special_total:
                exempt_112a = raw["capital_gains"]["ltcg_112a_exemption"]
                vals = {"stcg_111a": cg_.stcg_111a,
                        "ltcg_20_indexed": cg_.ltcg_20_indexed,
                        "ltcg_125": cg_.ltcg_125,
                        # only the part that would actually bear tax
                        "ltcg_112a": max(0, cg_.ltcg_112a - exempt_112a)}
                sheltered = exempt_112a if cg_.ltcg_112a else 0
                remaining = shortfall
                for k in ("stcg_111a", "ltcg_20_indexed", "ltcg_125",
                          "ltcg_112a"):
                    take = min(remaining, vals[k])
                    vals[k] -= take
                    remaining -= take
                    if remaining <= 0:
                        break
                absorbed = shortfall - remaining
                if absorbed:
                    note.append(Line(
                        "Unexhausted basic exemption adjusted against "
                        "special-rate income", "112/111A", -absorbed))
                adj = replace(
                    cg_, stcg_111a=vals["stcg_111a"],
                    ltcg_20_indexed=vals["ltcg_20_indexed"],
                    ltcg_125=vals["ltcg_125"],
                    ltcg_112a=vals["ltcg_112a"] + min(sheltered,
                                                      cg_.ltcg_112a))

        # Partial integration (Finance Act): net agricultural income above
        # the threshold is added for RATE purposes only.
        integrate = (agri > raw["exempt_income"]["agri_integration_threshold"]
                     and ti_ > zero_band)

        def _slab_normal(amount: int) -> int:
            if not integrate:
                return slab_tax(slabs, amount)
            return max(0, slab_tax(slabs, amount + agri)
                       - slab_tax(slabs, zero_band + agri))

        stax_ = _slab_normal(normal_)
        agri_extra = stax_ - slab_tax(slabs, normal_)
        if agri_extra:
            note.append(Line(f"Agricultural income ₹{agri:,} aggregated for "
                             "rate purposes (partial integration)", "10(1)",
                             agri_extra))
        if stax_ == 0:
            slab_lines_: tuple[Line, ...] = ()
        elif integrate:
            parts = list(slab_breakdown(slabs, normal_ + agri))
            relief_agg = slab_tax(slabs, zero_band + agri)
            if relief_agg:
                parts.append(Line(
                    "Less: tax on basic exemption + agricultural income "
                    f"(₹{zero_band + agri:,})", "10(1)", -relief_agg))
            slab_lines_ = tuple(parts)
        else:
            slab_lines_ = slab_breakdown(slabs, normal_)
        special_tax_, special_lines_ = _special_taxes(adj, raw)

        # ---- rebate 87A ----
        rebate_ = 0
        if ti_ <= rb["ti_limit"]:
            base = stax_ if rb["slab_tax_only"] else (
                stax_ + special_tax_ - _tax_112a(adj, raw))
            rebate_ = min(rb["max_rebate"], max(0, base))
        elif rb.get("marginal_relief"):
            excess = ti_ - rb["ti_limit"]
            if stax_ > excess:
                rebate_ = stax_ - excess
                note.append(Line("87A marginal relief applied", "87A", 0))

        if rb["slab_tax_only"]:
            after_rebate = max(0, stax_ - rebate_) + special_tax_
        else:
            after_rebate = max(0, stax_ + special_tax_ - rebate_)

        # ---- surcharge with marginal relief ----
        rate, threshold, rate_below = _surcharge_rate(reg["surcharge"], ti_)
        surcharge_ = 0
        relief_ = 0
        if rate:
            cap_special = reg["surcharge_cap_special_income"]
            tax_normal_part = max(0, stax_ - rebate_)   # rebate 0 at these TIs
            surcharge_ = (pct(tax_normal_part, rate)
                          + pct(special_tax_, min(rate, cap_special)))
            reduce_by = ti_ - threshold
            normal_at_t = max(0, normal_ - reduce_by)
            stax_t = _slab_normal(normal_at_t)
            surch_t = (pct(stax_t, rate_below)
                       + pct(special_tax_, min(rate_below, cap_special)))
            ceiling = stax_t + special_tax_ + surch_t + reduce_by
            if after_rebate + surcharge_ > ceiling:
                relief_ = after_rebate + surcharge_ - ceiling
                note.append(Line("Marginal relief on surcharge", "Surcharge",
                                 -relief_))

        cess_ = pct(after_rebate + surcharge_ - relief_, raw["cess_rate"])
        return {"slab_tax": stax_, "slab_lines": slab_lines_,
                "special_tax": special_tax_,
                "special_lines": tuple(special_lines_),
                "rebate": rebate_, "surcharge": surcharge_,
                "relief": relief_, "cess": cess_,
                "liability": after_rebate + surcharge_ - relief_ + cess_}

    tax = _tax_pipeline(ti, normal_income, cg, lines)
    stax = tax["slab_tax"]
    slab_lines = tax["slab_lines"]
    special_tax = tax["special_tax"]
    special_lines = tax["special_lines"]
    rebate = tax["rebate"]
    surcharge = tax["surcharge"]
    relief = tax["relief"]
    cess = tax["cess"]
    liability = tax["liability"]

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
                  and age_cat in ("senior", "super_senior")
                  and not (biz.amount or spec.amount))
    i234b = 0 if skip_234bc else interest.s234b(
        assessed, advance, inputs.filing_date, ay_start,
        icfg["rate_pm_234b"], icfg["advance_tax_threshold"])
    # 234C: the first three instalments ignore income the proviso excuses.
    deferrable_normal = min(max(0, normal_income),
                            os_eff["dividend"] if icfg.get(
                                "s234c_defer_dividend", True) else 0)
    undeferrable = _tax_pipeline(
        max(0, ti - deferrable_normal - special_total),
        max(0, normal_income - deferrable_normal),
        CGResult(head_total=0))["liability"]
    defer_sections = set(icfg.get("s234c_deferment_tds_sections", ()))
    tds_deferrable = sum(t.amount for t in inputs.tds
                         if str(t.section).strip().upper() in defer_sections)
    i234c = 0 if skip_234bc else interest.s234c(
        assessed, inputs.challans, icfg["s234c_schedule"],
        icfg["rate_pm_234c"], icfg["advance_tax_threshold"],
        assessed_tax_undeferrable=undeferrable
        - max(0, tds_total - tds_deferrable))
    balance_for_234a = max(0, liability - tds_total - advance - self_asst)
    i234a = interest.s234a(balance_for_234a, inputs.filing_date, due_date,
                           icfg["rate_pm_234a"])
    fee = interest.s234f(ti, inputs.filing_date, due_date,
                         raw["late_fee_234f"])

    # Sec 288B rounding sits BEFORE the self-assessment credit, because the
    # rounded figure IS what the client was told to pay: a challan for the
    # rounded amount then closes the account to NIL instead of leaving a ₹10
    # artefact. TDS and advance tax are inside the rounding — they were paid
    # before the liability was ever computed.
    payable = r10(liability + i234a + i234b + i234c + fee
                  - tds_total - advance) - self_asst

    # Head TOTALS are reported AFTER Chapter VI set-off (so they add up to
    # GTI), while each head keeps its own pre-set-off detail lines and the
    # set-off block above reconciles the two — the way a practitioner reads
    # a computation.
    heads = {
        "salary": replace(sal, amount=after["salary"]),
        "house_property": replace(hp, amount=after["house_property"]),
        "business": replace(biz, amount=after["business"]),
        "speculation": replace(spec, amount=after["speculation"]),
        "other_sources": replace(os_, amount=after["other_sources"]),
        "capital_gains": replace(cg, ltcg_112a_taxable=max(
            0, cg.ltcg_112a - raw["capital_gains"]["ltcg_112a_exemption"]))}

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
        slab_lines=slab_lines, setoff_lines=setoff_lines,
        loss_table=loss_table, speculation_income=after["speculation"],
        ltcg_112a_gross=cg.ltcg_112a)
