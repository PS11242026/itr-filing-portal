"""Typed inputs and results for the tax engine. All amounts: whole rupees."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

AssetClass = Literal["listed_equity_stt", "equity_mf", "listed_other",
                     "property", "unlisted", "other"]


@dataclass(frozen=True)
class PersonalInfo:
    dob: date | None = None
    resident_status: str = "Resident"


@dataclass(frozen=True)
class SalaryIncome:
    employer_name: str
    employer_tan: str = ""
    gross_salary: int = 0            # 17(1)+(2)+(3)
    exempt_allowances: int = 0       # Sec 10 exemptions per Form 16
    prof_tax: int = 0                # 16(iii)
    entertainment_allowance: int = 0  # 16(ii) — govt employees, old regime


@dataclass(frozen=True)
class HouseProperty:
    kind: Literal["self_occupied", "let_out"] = "let_out"
    address: str = ""
    annual_rent_received: int = 0
    municipal_taxes_paid: int = 0
    interest_24b: int = 0
    pre_construction_interest_total: int = 0   # engine claims 1/5 per year
    co_owner_share_pct: float = 100.0
    arrears_rent_received: int = 0             # 25A — 30% deduction applies


@dataclass(frozen=True)
class CGEntry:
    asset_class: AssetClass
    transfer_date: date
    acquisition_date: date | None = None
    description: str = ""
    full_value_consideration: int = 0
    stamp_duty_value: int | None = None        # property → Sec 50C test
    cost_of_acquisition: int = 0
    cost_of_improvement: int = 0
    transfer_expenses: int = 0
    fmv_31jan2018: int | None = None           # 112A grandfathering
    exemption_claimed: int = 0                 # 54/54EC/54F etc. (validated
    exemption_section: str = ""                # by staff with proofs)
    # Buyer/transferee particulars — Schedule CG requires them for
    # immovable property (and the practitioner statement prints them).
    buyer_name: str = ""
    buyer_pan: str = ""
    buyer_address: str = ""
    buyer_share_pct: float = 100.0
    # "short"/"long": the ST/LT determination reported WITH the data (the
    # AIS SFT detail's Asset Type, or the Genius sheet's declared column)
    # for rows whose acquisition date is not yet on file. Dates, when
    # present, always win — this only replaces the undated=long-term
    # assumption with the reporter's own holding-period answer.
    term_override: str = ""


@dataclass(frozen=True)
class PresumptiveBusiness:
    section: Literal["44AD", "44ADA", "44AE"] = "44AD"
    name: str = ""
    turnover_digital: int = 0      # 44AD: receipts via bank/digital modes
    turnover_cash: int = 0         # 44AD: cash receipts
    gross_receipts: int = 0        # 44ADA: professional gross receipts
    heavy_ton_months: int = 0      # 44AE: Σ(tonnage × months), >12MT vehicles
    other_vehicle_months: int = 0  # 44AE: Σ(vehicles × months), other vehicles
    declared_income: int = 0       # optional higher income actually declared


@dataclass(frozen=True)
class RegularBusiness:
    """Business/profession income from regular books (non-presumptive):
    net profit per P&L, adjusted book→tax. Depreciation is taken as inputs
    (book charge added back, Sec 32 allowance deducted); a block-of-assets
    calculator is a later enhancement."""

    name: str = ""
    net_profit_before_tax: int = 0     # per profit & loss account
    depreciation_books: int = 0        # add back depreciation charged in books
    depreciation_it: int = 0           # less depreciation allowable u/s 32
    disallowances: int = 0             # add back 40 / 40A(3) / 43B etc.
    other_additions: int = 0
    other_deductions: int = 0
    # Speculation business (Sec 43(5)) — intra-day equity, and anything the
    # practitioner marks as speculative. Kept in its own bucket because a
    # speculation LOSS may be set off only against speculation PROFITS
    # (Sec 73) and carries forward for four years, not eight.
    is_speculative: bool = False
    turnover: int = 0                  # for the speculative P&L statement


@dataclass(frozen=True)
class ClubbedIncome:
    section: Literal["60", "61", "64(1)", "64(1A)", "64(2)"] = "64(1A)"
    head: str = "other_sources"    # character retained (informational in v1)
    amount: int = 0
    minor_child: bool = False      # triggers the Sec 10(32) exemption
    description: str = ""


@dataclass(frozen=True)
class OtherSources:
    savings_interest: int = 0
    fd_interest: int = 0
    dividend: int = 0
    family_pension: int = 0
    taxable_gifts: int = 0                     # 56(2)(x) — already screened
    other: int = 0
    deductions_57: int = 0                      # expenses to earn OS income
    # Post Office savings-bank interest is reported separately because it
    # carries its own exemption — Sec 10(15)(i) r/w Rule 2BBA: ₹3,500 for a
    # single account, ₹7,000 jointly held. The exemption survives 115BAC,
    # so the engine claims it under BOTH regimes automatically.
    post_office_savings_interest: int = 0
    post_office_joint_account: bool = False


@dataclass(frozen=True)
class ExemptIncomeEntry:
    """Income exempt under the Act (Schedule EI) — reported, never taxed.

    Net agricultural income (section "10(1)") additionally participates in
    partial integration: it raises the slab rate on the taxable income when
    it exceeds the Finance-Act threshold (both regimes)."""

    section: str = "Other"           # e.g. "10(1)", "10(2A)", "10(10D)"
    description: str = ""
    amount: int = 0


@dataclass(frozen=True)
class TDSEntry:
    tan: str = ""
    deductor: str = ""
    section: str = ""
    amount: int = 0


# Valid AdjustmentEntry targets → practitioner-facing labels. Head-level
# targets add/less against the computed head; other_sources.* targets adjust
# the field BEFORE the 57(iia)/80TTA/80TTB math; tds/tcs adjust the prepaid
# credit the same way both feed assessed tax for 234B/234C.
ADJUSTMENT_TARGETS: dict[str, str] = {
    "salary": "Salary — head total",
    "house_property": "House property — head total",
    "business": "Business/profession — head total",
    "other_sources.savings_interest": "Other sources — savings interest",
    "other_sources.post_office_savings_interest":
        "Other sources — post office savings interest",
    "other_sources.fd_interest": "Other sources — FD/deposit interest",
    "other_sources.dividend": "Other sources — dividend",
    "other_sources.family_pension": "Other sources — family pension",
    "other_sources.taxable_gifts": "Other sources — taxable gifts",
    "other_sources.other": "Other sources — other income",
    "tds": "TDS credit",
    "tcs": "TCS credit",
}


@dataclass(frozen=True)
class AdjustmentEntry:
    """A manual add (+) / less (−) line inside a head, subhead or tax
    credit — the Genius-style adjustment row ("PENSION FROM LIC",
    "INTT. ON NSC", "Exempt u/s 10(15)" …). Shown as its own line in the
    computation."""

    target: str = "other_sources.other"     # key of ADJUSTMENT_TARGETS
    label: str = ""
    amount: int = 0                          # signed rupees


@dataclass(frozen=True)
class Challan:
    kind: Literal["advance", "self_assessment"] = "advance"
    paid_on: date | None = None
    amount: int = 0
    reference: str = ""        # "BSR code - challan serial", from 26AS PART-C


# Loss kinds that survive into a later year → (label, carry-forward years
# allowed after the AY of the loss, what it may be set off against later).
LOSS_KINDS: dict[str, tuple[str, int]] = {
    "house_property": ("House Property Loss", 8),      # 71B
    "business": ("Ordinary Business Loss", 8),         # 72
    "speculation": ("Speculation Business Loss", 4),   # 73
    "stcl": ("Short Term Capital Loss", 8),            # 74
    "ltcl": ("Long Term Capital Loss", 8),             # 74
}


@dataclass(frozen=True)
class BFLossEntry:
    """One brought-forward loss, as it appears in the losses table: the AY in
    which it was first determined, its character, and the amount still
    unabsorbed at the start of this year."""

    ay: str = ""                       # e.g. "2023-24"; blank = age unknown
    kind: str = "business"             # key of LOSS_KINDS
    amount: int = 0


@dataclass(frozen=True)
class BroughtForwardLosses:
    """Legacy flat totals (kept so older saved inputs load unchanged) plus
    the per-AY rows the losses table and the expiry test need. When
    ``entries`` is non-empty it is authoritative and the flat fields are
    ignored."""

    hp_loss: int = 0
    stcl: int = 0
    ltcl: int = 0
    business: int = 0
    speculation: int = 0
    entries: tuple[BFLossEntry, ...] = ()

    def rows(self) -> tuple[BFLossEntry, ...]:
        if self.entries:
            return tuple(e for e in self.entries if e.amount > 0)
        flat = (("house_property", self.hp_loss), ("business", self.business),
                ("speculation", self.speculation), ("stcl", self.stcl),
                ("ltcl", self.ltcl))
        return tuple(BFLossEntry(ay="", kind=k, amount=v)
                     for k, v in flat if v > 0)


@dataclass(frozen=True)
class LossRow:
    """A line of the losses table: what came in, what was absorbed this year,
    what goes forward (or lapsed because its carry-forward window closed)."""

    ay: str
    kind: str
    brought_forward: int = 0
    set_off: int = 0
    carried_forward: int = 0
    lapsed: int = 0

    @property
    def label(self) -> str:
        return LOSS_KINDS.get(self.kind, (self.kind, 0))[0]


@dataclass(frozen=True)
class AssessmentInputs:
    ay: str
    personal: PersonalInfo = PersonalInfo()
    salaries: tuple[SalaryIncome, ...] = ()
    house_properties: tuple[HouseProperty, ...] = ()
    capital_gains: tuple[CGEntry, ...] = ()
    businesses: tuple[PresumptiveBusiness, ...] = ()
    regular_businesses: tuple[RegularBusiness, ...] = ()
    other_sources: OtherSources = OtherSources()
    clubbing: tuple[ClubbedIncome, ...] = ()
    exempt_incomes: tuple[ExemptIncomeEntry, ...] = ()
    deductions: tuple[tuple[str, int], ...] = ()   # (section, claimed)
    adjustments: tuple[AdjustmentEntry, ...] = ()
    tds: tuple[TDSEntry, ...] = ()
    challans: tuple[Challan, ...] = ()
    brought_forward: BroughtForwardLosses = BroughtForwardLosses()
    regime_election: Literal["auto", "old", "new"] = "auto"
    filing_date: date | None = None


# ---------------------------------------------------------------- results --

@dataclass(frozen=True)
class Line:
    label: str
    section: str
    amount: int


@dataclass(frozen=True)
class HeadResult:
    head: str
    amount: int
    lines: tuple[Line, ...] = ()


@dataclass(frozen=True)
class CGResult:
    """Capital gains after intra-head set-off.

    ``ltcg_112a`` is the GROSS 112A gain. The ₹1,25,000 relief in
    Sec 112A(2) is a rate concession, not an exclusion from total income —
    the gain stays in GTI and only the excess is taxed at 12.5%. So the
    threshold is applied by the tax stage, never here."""

    head_total: int                  # taxable CG included in GTI
    stcg_slab: int = 0               # taxed at slab (in normal income)
    stcg_111a: int = 0               # 20%
    ltcg_112a: int = 0               # 12.5% beyond ₹1.25L (applied at tax)
    ltcg_125: int = 0                # 12.5% unindexed bucket
    ltcg_20_indexed: int = 0         # 20% grandfathered-option bucket
    cf_stcl: int = 0
    cf_ltcl: int = 0
    lines: tuple[Line, ...] = ()
    reco_lines: tuple[Line, ...] = ()   # 20%-indexed vs 12.5%-unindexed
    # ltcg_112a net of the ₹1.25L rate concession — what Schedule CG reports
    # as the 112A figure actually bearing tax. Filled by the regime pipeline,
    # which is the only layer that knows the threshold.
    ltcg_112a_taxable: int = 0

    @property
    def special_total(self) -> int:
        return (self.stcg_111a + self.ltcg_112a + self.ltcg_125
                + self.ltcg_20_indexed)


@dataclass(frozen=True)
class RegimeResult:
    regime: str
    heads: dict
    gti: int
    hp_loss_setoff: int
    hp_loss_carried_forward: int
    hp_loss_lapsed: int
    deductions_total: int
    deduction_lines: tuple[Line, ...]
    total_income: int                # r10 (288A)
    normal_income: int               # slab-rate portion of TI
    slab_tax: int
    special_tax_lines: tuple[Line, ...]
    special_tax: int
    rebate_87a: int
    surcharge: int
    marginal_relief: int
    cess: int
    tax_liability: int               # after rebate/surcharge/cess
    tds_total: int
    advance_tax_paid: int
    self_assessment_paid: int
    interest_234a: int
    interest_234b: int
    interest_234c: int
    fee_234f: int
    payable_or_refund: int           # r10 (288B); positive = payable
    lines: tuple[Line, ...] = ()     # narrative schedule lines
    exempt_total: int = 0            # Schedule EI — reported, not in TI
    exempt_lines: tuple[Line, ...] = ()
    slab_lines: tuple[Line, ...] = ()   # band-wise split of slab_tax
    # Chapter VI set-off / carry-forward (Sec 70–74)
    setoff_lines: tuple[Line, ...] = ()
    loss_table: tuple[LossRow, ...] = ()
    speculation_income: int = 0      # after Sec 73, floored at zero
    ltcg_112a_gross: int = 0         # before the ₹1.25L rate concession


@dataclass(frozen=True)
class ComputationResult:
    ay: str
    engine_version: str
    config_hash: str
    inputs_digest: str
    old: RegimeResult
    new: RegimeResult
    recommended: str
    saving: int
