"""Schema-version-specific key transforms.

When the official ITD schema lands (or a mid-season revision changes key
names), the builder stays untouched — the delta lives here, keyed by
(form, schema_version). apply() runs after the builder, before validation.
"""


def apply(json_dict: dict, form: str, schema_version: str) -> dict:
    transforms = _TRANSFORMS.get((form, schema_version), ())
    for fn in transforms:
        json_dict = fn(json_dict)
    return json_dict


# Example shape (empty for the placeholder schema):
# def _rename_ltcg_field(d):
#     inc = d["ITR"]["ITR1"]["ITR1_IncomeDeductions"]
#     inc["Ltcg112A"] = inc.pop("LongTermCapGain112A", 0)
#     return d

_TRANSFORMS: dict[tuple[str, str], tuple] = {
    ("ITR-1", "Ver1.0"): (),
    ("ITR-2", "Ver1.0"): (),
    ("ITR-3", "Ver1.0"): (),
    ("ITR-4", "Ver1.0"): (),
}
