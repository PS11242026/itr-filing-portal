"""AssessmentInputs / results ↔ JSON-safe dicts (dates as ISO strings)."""

import dataclasses
from datetime import date

from itrcore.tax.types import (AdjustmentEntry, AssessmentInputs,
                               BroughtForwardLosses, CGEntry, Challan,
                               ClubbedIncome, ExemptIncomeEntry,
                               HouseProperty, OtherSources, PersonalInfo,
                               PresumptiveBusiness, RegularBusiness,
                               SalaryIncome, TDSEntry)


def _clean(obj):
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def inputs_to_dict(inputs: AssessmentInputs) -> dict:
    return _clean(dataclasses.asdict(inputs))


def result_to_dict(result) -> dict:
    return _clean(dataclasses.asdict(result))


def _d(value) -> date | None:
    # 'NaT' is pandas' not-a-time leaking out of an empty editor date cell
    # (older saved inputs may carry it) — it means "no date", never a crash.
    if not value or value == "NaT":
        return None
    return date.fromisoformat(value)


def inputs_from_dict(d: dict) -> AssessmentInputs:
    return AssessmentInputs(
        ay=d["ay"],
        personal=PersonalInfo(
            dob=_d(d.get("personal", {}).get("dob")),
            resident_status=d.get("personal", {}).get("resident_status",
                                                      "Resident")),
        salaries=tuple(SalaryIncome(**s) for s in d.get("salaries", [])),
        house_properties=tuple(HouseProperty(**h)
                               for h in d.get("house_properties", [])),
        capital_gains=tuple(
            CGEntry(**{**e,
                       "transfer_date": _d(e["transfer_date"]),
                       "acquisition_date": _d(e.get("acquisition_date"))})
            for e in d.get("capital_gains", [])),
        businesses=tuple(PresumptiveBusiness(**b)
                         for b in d.get("businesses", [])),
        regular_businesses=tuple(RegularBusiness(**b)
                                 for b in d.get("regular_businesses", [])),
        other_sources=OtherSources(**d.get("other_sources", {})),
        clubbing=tuple(ClubbedIncome(**c) for c in d.get("clubbing", [])),
        exempt_incomes=tuple(ExemptIncomeEntry(**e)
                             for e in d.get("exempt_incomes", [])),
        deductions=tuple((s, int(a)) for s, a in d.get("deductions", [])),
        adjustments=tuple(AdjustmentEntry(**a)
                          for a in d.get("adjustments", [])),
        tds=tuple(TDSEntry(**t) for t in d.get("tds", [])),
        challans=tuple(
            Challan(**{**c, "paid_on": _d(c.get("paid_on"))})
            for c in d.get("challans", [])),
        brought_forward=BroughtForwardLosses(
            **d.get("brought_forward", {})),
        regime_election=d.get("regime_election", "auto"),
        filing_date=_d(d.get("filing_date")))
