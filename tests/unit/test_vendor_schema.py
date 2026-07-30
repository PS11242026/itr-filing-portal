"""Schema vendoring helper (scripts/vendor_schema.py)."""

import json

from scripts import vendor_schema


def test_vendor_writes_registry_path(tmp_path, monkeypatch):
    monkeypatch.setattr(vendor_schema, "SCHEMAS", tmp_path)
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"$schema": "x", "type": "object"}),
                   encoding="utf-8")

    dest = vendor_schema.vendor(form="ITR-4", ay="2026-27",
                                version="TestVer", source=str(src))

    assert dest.name == "ITR4_TestVer.json"
    assert dest.parent.name == "ay2026_27"
    assert json.loads(dest.read_text(encoding="utf-8"))["type"] == "object"


def test_vendor_rejects_non_json(tmp_path, monkeypatch):
    monkeypatch.setattr(vendor_schema, "SCHEMAS", tmp_path)
    src = tmp_path / "bad.json"
    src.write_text("not json", encoding="utf-8")

    import pytest
    with pytest.raises(json.JSONDecodeError):
        vendor_schema.vendor(form="ITR-4", ay="2026-27", version="V",
                             source=str(src))
