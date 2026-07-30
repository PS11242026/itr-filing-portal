"""ITR form selection with human-readable reasons.

ITR-1 (Sahaj), AY 2026-27: resident individual, total income ≤ ₹50 lakh,
salary + ONE house property + other sources, LTCG u/s 112A within the
₹1.25L exemption (no tax, no carry-forward), no other capital gains, no
brought-forward losses, agricultural income ≤ ₹5,000, not a director, no
unlisted shares, no foreign assets. Anything else in scope → ITR-2.
"""

from dataclasses import dataclass, field

from itrcore.tax.ayconfig import load as load_cfg

ITR1_TI_LIMIT = 5_000_000


@dataclass(frozen=True)
class Selection:
    form: str                      # "ITR-1" | "ITR-2"
    reasons: tuple[str, ...] = field(default_factory=tuple)


def select_form(*, inputs_dict: dict, result_dict: dict,
                profile: dict) -> Selection:
    reasons: list[str] = []
    regime = result_dict["recommended"]
    res = result_dict[regime]

    if inputs_dict.get("personal", {}).get("resident_status") != "Resident":
        reasons.append("Not an ordinary resident")
    if res["total_income"] > ITR1_TI_LIMIT:
        reasons.append(f"Total income ₹{res['total_income']:,} exceeds "
                       "₹50 lakh")
    if len(inputs_dict.get("house_properties", [])) > 1:
        reasons.append("More than one house property")
    if res.get("hp_loss_carried_forward", 0) > 0:
        reasons.append("House-property loss carried forward")

    cg = res["heads"].get("capital_gains", {}) or {}
    if cg.get("stcg_slab", 0) or cg.get("stcg_111a", 0):
        reasons.append("Short-term capital gains present")
    if cg.get("ltcg_125", 0) or cg.get("ltcg_20_indexed", 0):
        reasons.append("LTCG other than listed equity u/s 112A "
                       "(e.g. property) present")
    if cg.get("ltcg_112a_taxable", 0) > 0:
        reasons.append("112A LTCG exceeds the ₹1.25 lakh exemption")
    if cg.get("cf_stcl", 0) or cg.get("cf_ltcl", 0):
        reasons.append("Capital losses to carry forward")

    bf = inputs_dict.get("brought_forward", {}) or {}
    if any(bf.get(k) for k in ("hp_loss", "stcl", "ltcl")):
        reasons.append("Brought-forward losses claimed")

    # Agricultural income above the statutory limit bars ITR-1 (and ITR-4).
    agri_limit = load_cfg(inputs_dict["ay"]).raw[
        "exempt_income"]["itr1_agri_limit"]
    agri = sum(int(e.get("amount", 0))
               for e in inputs_dict.get("exempt_incomes", [])
               if e.get("section") == "10(1)")
    if agri > agri_limit:
        reasons.append(f"Agricultural income ₹{agri:,} exceeds "
                       f"₹{agri_limit:,}")

    if profile.get("is_director"):
        reasons.append("Director in a company")
    if profile.get("has_unlisted_shares"):
        reasons.append("Holds unlisted equity shares")
    if profile.get("has_foreign_assets"):
        reasons.append("Foreign assets/income")

    # Business/professional income takes the return out of ITR-1/2 entirely.
    # ITR-4 (Sugam) is presumptive-only and otherwise simple; regular books
    # (or a disqualifier) → ITR-3.
    presumptive = inputs_dict.get("businesses", [])
    regular = inputs_dict.get("regular_businesses", [])
    if presumptive or regular:
        presumptive_only = (bool(presumptive) and not regular and all(
            b.get("section") in ("44AD", "44ADA", "44AE")
            for b in presumptive))
        if presumptive_only and not reasons \
                and res["total_income"] <= ITR1_TI_LIMIT:
            return Selection(form="ITR-4", reasons=(
                "Presumptive business/professional income "
                "(Sec 44AD/44ADA/44AE)",))
        return Selection(
            form="ITR-3",
            reasons=tuple(reasons) + ("Business or professional income",))

    return Selection(form="ITR-2" if reasons else "ITR-1",
                     reasons=tuple(reasons))
