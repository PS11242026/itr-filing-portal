"""Chapter VI — set off and carry forward of losses (Sections 70–74).

The head modules each return their own income in isolation; this module is
the only place that lets one head's loss touch another head's income, and
the only place that decides what survives into next year.

Order is the statutory one: current-year intra-head (Sec 70) → current-year
inter-head (Sec 71) → brought-forward (Sec 71B/72/73/74) → carry forward the
remainder. Restrictions enforced:

* 71(2A) — a business loss is never set off against SALARY.
* 71(3)  — a capital loss never leaves the capital-gains head.
* 71(3A) — a house-property loss set off against other heads is capped at
           ₹2,00,000 (and under 115BAC it may not leave the head at all).
* 73(1)  — a SPECULATION loss is set off only against speculation profits,
           in this year and in the four following ones. A non-speculative
           business loss may still be absorbed by speculation profits, as
           both belong to the same head (Sec 70).
* 74     — a long-term capital loss is set off only against long-term gains;
           a short-term loss against either.
* 72/73/74 windows: 8 assessment years, except speculation's 4. A loss whose
           window has closed lapses and is reported as such.

Absorption order inside the surviving income is deterministic: slab-rate
income first (it carries the highest marginal cost), then the special-rate
buckets by descending rate, and the 112A pool last because its first
₹1,25,000 is already relieved. This is what practitioner software prints and
it is taxpayer-favourable in the ordinary case.
"""

from dataclasses import replace

from itrcore.tax.types import (BroughtForwardLosses, CGResult, Line,
                               LOSS_KINDS, LossRow)

# Special-rate buckets, worst-to-best for the taxpayer to lose.
_SPECIAL_ORDER = ("stcg_111a", "ltcg_20_indexed", "ltcg_125", "ltcg_112a")
_CG_ALL = ("stcg_slab",) + _SPECIAL_ORDER
_CG_LONG = ("ltcg_20_indexed", "ltcg_125", "ltcg_112a")


def _ay_start_year(ay: str) -> int | None:
    try:
        return int(str(ay).split("-")[0])
    except (ValueError, AttributeError, IndexError):
        return None


def _expired(loss_ay: str, current_ay: str, kind: str) -> bool:
    """A loss of AY X may be set off up to AY X + N. Blank/unparseable AYs
    never expire — staff owns that judgement, the engine does not guess."""
    start, now = _ay_start_year(loss_ay), _ay_start_year(current_ay)
    if start is None or now is None:
        return False
    return now > start + LOSS_KINDS.get(kind, ("", 8))[1]


class _Pool:
    """Mutable income buckets, so absorption reads as one loop everywhere."""

    def __init__(self, *, house_property: int, other_sources: int,
                 business: int, speculation: int, cg: dict[str, int]):
        self.house_property = house_property
        self.other_sources = other_sources
        self.business = business
        self.speculation = speculation
        self.cg = dict(cg)

    def get(self, name: str) -> int:
        if name in self.cg:
            return self.cg[name]
        return getattr(self, name)

    def take(self, name: str, amount: int) -> int:
        """Absorb up to ``amount`` from a positive bucket; returns what was
        actually absorbed."""
        available = max(0, self.get(name))
        used = min(max(0, amount), available)
        if used:
            if name in self.cg:
                self.cg[name] -= used
            else:
                setattr(self, name, self.get(name) - used)
        return used

    def absorb(self, loss: int, order: tuple[str, ...]) -> tuple[int, int]:
        """Spread ``loss`` (a positive number) over buckets in order.
        Returns (absorbed, remaining)."""
        absorbed = 0
        for name in order:
            if loss <= 0:
                break
            used = self.take(name, loss)
            absorbed += used
            loss -= used
        return absorbed, loss


def compute(*, salary: int, house_property: int, business: int,
            speculation: int, other_sources: int, cg: CGResult,
            brought_forward: BroughtForwardLosses, ay: str,
            hp_setoff_cap: int, hp_setoff_allowed: bool
            ) -> tuple[dict, tuple[Line, ...], tuple[LossRow, ...]]:
    """Returns (heads after set-off, narrative lines, losses table).

    ``hp_setoff_allowed`` is False under 115BAC, where a house-property loss
    may not be set off against any other head at all.
    """
    lines: list[Line] = []
    rows: list[LossRow] = []

    pool = _Pool(house_property=max(0, house_property),
                 other_sources=max(0, other_sources),
                 business=max(0, business),
                 speculation=max(0, speculation),
                 cg={"stcg_slab": cg.stcg_slab, "stcg_111a": cg.stcg_111a,
                     "ltcg_20_indexed": cg.ltcg_20_indexed,
                     "ltcg_125": cg.ltcg_125, "ltcg_112a": cg.ltcg_112a})

    # ---- Sec 70: within the business head -------------------------------
    # A non-speculative loss may be absorbed by speculation profits; the
    # reverse is barred by Sec 73(1).
    biz_loss = max(0, -business)
    spec_loss = max(0, -speculation)
    if biz_loss and pool.speculation:
        used, biz_loss = pool.absorb(biz_loss, ("speculation",))
        if used:
            lines.append(Line("Business loss set off against speculation "
                              "profits", "70", -used))
    if spec_loss:
        lines.append(Line(
            f"Speculation loss ₹{spec_loss:,} is not set off against any "
            "other income (Sec 73)", "73", 0))

    # ---- Sec 71: inter-head ---------------------------------------------
    # House-property loss first: it is the only loss that may still reach
    # salary, and its ₹2,00,000 ceiling would otherwise be wasted.
    hp_loss = max(0, -house_property)
    hp_cf = hp_loss
    hp_setoff = 0
    if hp_loss and hp_setoff_allowed:
        allowed = min(hp_loss, hp_setoff_cap)
        salary_left = max(0, salary)
        used_salary = min(allowed, salary_left)
        salary -= used_salary
        used, left = pool.absorb(allowed - used_salary,
                                 ("other_sources", "business", "speculation")
                                 + _CG_ALL)
        hp_setoff = used_salary + used
        hp_cf = hp_loss - hp_setoff
        if hp_setoff:
            lines.append(Line("House property loss set off against other "
                              "heads", "71", -hp_setoff))
        if hp_cf and hp_loss > hp_setoff_cap:
            lines.append(Line(
                f"House property loss ₹{hp_cf:,} carried forward — inter-head "
                f"set-off capped at ₹{hp_setoff_cap:,}", "71(3A)", 0))
    elif hp_loss:
        lines.append(Line(
            f"House property loss ₹{hp_loss:,} carried forward — no "
            "inter-head set-off is available under Sec 115BAC", "115BAC", 0))

    # Business loss: everything except salary (Sec 71(2A)).
    if biz_loss:
        before = dict(pool.cg)
        os_before = pool.other_sources
        hp_before = pool.house_property
        _, biz_loss = pool.absorb(
            biz_loss, ("stcg_slab", "other_sources", "house_property")
            + _SPECIAL_ORDER)
        for label, section, amount in (
                ("income from other sources", "71",
                 os_before - pool.other_sources),
                ("income from house property", "71",
                 hp_before - pool.house_property),
                ("STCG at slab rates", "71",
                 before["stcg_slab"] - pool.cg["stcg_slab"]),
                ("STCG @ 20%", "71",
                 before["stcg_111a"] - pool.cg["stcg_111a"]),
                ("LTCG @ 20% (indexed)", "71",
                 before["ltcg_20_indexed"] - pool.cg["ltcg_20_indexed"]),
                ("LTCG @ 12.5%", "71",
                 before["ltcg_125"] - pool.cg["ltcg_125"]),
                ("LTCG @ 12.5% (112A)", "71",
                 before["ltcg_112a"] - pool.cg["ltcg_112a"])):
            if amount:
                lines.append(Line(f"Business loss set off from {label}",
                                  section, -amount))
        if biz_loss:
            lines.append(Line(
                f"Business loss ₹{biz_loss:,} carried forward — it cannot be "
                "set off against salary (Sec 71(2A))", "71(2A)", 0))

    # ---- brought-forward losses (Sec 71B / 72 / 73 / 74) ----------------
    # Oldest first, so nothing lapses while a newer loss is absorbed.
    bf_targets: dict[str, tuple[str, ...]] = {
        "house_property": ("house_property",),
        "business": ("business", "speculation"),
        "speculation": ("speculation",),
        "stcl": _CG_ALL,
        "ltcl": _CG_LONG,
    }
    bf_rows = sorted(brought_forward.rows(),
                     key=lambda e: (_ay_start_year(e.ay) or 0, e.kind))
    for entry in bf_rows:
        if entry.kind not in bf_targets:
            continue
        if _expired(entry.ay, ay, entry.kind):
            rows.append(LossRow(ay=entry.ay, kind=entry.kind,
                                brought_forward=entry.amount,
                                lapsed=entry.amount))
            lines.append(Line(
                f"{LOSS_KINDS[entry.kind][0]} of AY {entry.ay} ₹"
                f"{entry.amount:,} lapsed — the "
                f"{LOSS_KINDS[entry.kind][1]}-year carry-forward window has "
                "closed", "72", 0))
            continue
        used, left = pool.absorb(entry.amount, bf_targets[entry.kind])
        if used:
            lines.append(Line(
                f"{LOSS_KINDS[entry.kind][0]} of AY {entry.ay or '—'} set off",
                "72" if "business" in entry.kind else "74", -used))
        rows.append(LossRow(ay=entry.ay, kind=entry.kind,
                            brought_forward=entry.amount, set_off=used,
                            carried_forward=left))

    # ---- carry forward this year's unabsorbed losses --------------------
    for kind, amount in (("house_property", hp_cf), ("business", biz_loss),
                         ("speculation", spec_loss), ("stcl", cg.cf_stcl),
                         ("ltcl", cg.cf_ltcl)):
        if amount > 0:
            rows.append(LossRow(ay=ay, kind=kind, carried_forward=amount))

    heads = {
        "salary": max(0, salary),
        "house_property": pool.house_property,
        "business": pool.business,
        "speculation": pool.speculation,
        "other_sources": pool.other_sources,
        "hp_loss_setoff": hp_setoff,
        "hp_loss_carried_forward": hp_cf,
        "cg": replace(cg,
                      stcg_slab=pool.cg["stcg_slab"],
                      stcg_111a=pool.cg["stcg_111a"],
                      ltcg_20_indexed=pool.cg["ltcg_20_indexed"],
                      ltcg_125=pool.cg["ltcg_125"],
                      ltcg_112a=pool.cg["ltcg_112a"],
                      head_total=sum(pool.cg.values())),
    }
    return heads, tuple(lines), tuple(rows)
