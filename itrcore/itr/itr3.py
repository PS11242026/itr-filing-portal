"""ITR-3 JSON builder — business/profession with regular books (and any
income head not eligible for the simpler forms). Shares the reduced-but-
faithful body with ITR-4; official-schema key deltas belong in quirks.py.

This is a portal-derived reduced structure (the official ITR-3 carries many
more schedules); import every generated JSON into the ITD offline utility to
validate before uploading.
"""

from itrcore.itr import quirks
from itrcore.itr._business_body import build_body
from itrcore.itr.schema_registry import SchemaBundle


def build(*, taxpayer: dict, inputs_dict: dict, result_dict: dict,
          regime: str, filing_meta: dict, bundle: SchemaBundle) -> dict:
    doc = build_body("ITR3", "ITR-3", taxpayer=taxpayer,
                     inputs_dict=inputs_dict, result_dict=result_dict,
                     regime=regime, filing_meta=filing_meta, bundle=bundle)
    return quirks.apply(doc, "ITR-3", bundle.version)
