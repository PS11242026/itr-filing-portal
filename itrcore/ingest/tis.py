"""TIS (Taxpayer Information Summary) JSON parser.

TIS is the ITD's category-level summary that accompanies AIS: for each
information category it reports a "processed value" (as reported by sources)
and a "derived value" (after the taxpayer's AIS feedback). The downloaded
JSON nests these under version-dependent keys, so — like the AIS parser —
this walks the tree tolerantly, classifies each category into the shared
AIS/TIS taxonomy, and reports nothing it cannot recognise rather than
failing.
"""

import json

from itrcore.ingest.ais import classify

_CATEGORY_KEYS = ("infoCategory", "informationCategory", "infoCategoryName",
                  "categoryName", "infoCategoryDesc", "infoDesc",
                  "description", "desc")
_DERIVED_KEYS = ("derivedValue", "derivedValueAmount", "derivedAmount")
_PROCESSED_KEYS = ("processedValue", "processedValueAmount",
                   "processedAmount", "amount")


def _to_paise(raw) -> int | None:
    if raw is None:
        return None
    try:
        return int(round(float(str(raw).replace(",", "").strip()) * 100))
    except (TypeError, ValueError):
        return None


def _first(node: dict, keys) -> object:
    for k in keys:
        if node.get(k) is not None:
            return node.get(k)
    return None


def _walk(node, hits: list[dict]) -> None:
    if isinstance(node, dict):
        desc = _first(node, _CATEGORY_KEYS)
        processed = _to_paise(_first(node, _PROCESSED_KEYS))
        derived = _to_paise(_first(node, _DERIVED_KEYS))
        if desc and (processed is not None or derived is not None):
            derived = derived if derived is not None else processed
            processed = processed if processed is not None else derived
            hits.append({"category": classify(str(desc)),
                         "description": str(desc)[:200],
                         "processed_value_paise": processed,
                         "derived_value_paise": derived})
        for value in node.values():
            _walk(value, hits)
    elif isinstance(node, list):
        for item in node:
            _walk(item, hits)


def parse_tis_json(data: bytes) -> list[dict]:
    try:
        tree = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a valid TIS JSON export: {exc}") from exc
    hits: list[dict] = []
    _walk(tree, hits)
    if not hits:
        raise ValueError("no information categories recognised in this TIS "
                         "JSON — enter figures manually and flag the file "
                         "format to the developer")
    return hits
