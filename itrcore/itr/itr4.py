"""ITR-4 (Sugam) JSON builder — presumptive business/profession + salary,
one house property and other sources. Shares the reduced-but-faithful body
with ITR-3; official-schema key deltas belong in quirks.py.
"""

from itrcore.itr import quirks
from itrcore.itr._business_body import build_body
from itrcore.itr.schema_registry import SchemaBundle


def build(*, taxpayer: dict, inputs_dict: dict, result_dict: dict,
          regime: str, filing_meta: dict, bundle: SchemaBundle) -> dict:
    doc = build_body("ITR4", "ITR-4", taxpayer=taxpayer,
                     inputs_dict=inputs_dict, result_dict=result_dict,
                     regime=regime, filing_meta=filing_meta, bundle=bundle)
    return quirks.apply(doc, "ITR-4", bundle.version)
