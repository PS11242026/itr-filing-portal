"""Persistence of the full parsed AIS / 26AS documents (migration 0005)."""

from itrcore.authz import mint_scope
from itrcore.db.repos import importers

AY = "2026-27"


def test_ais_document_save_get_and_replace(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    assert importers.get_ais_document(scope, AY) is None

    doc = {"pan": "AAAPL1234C", "name": "TEST", "rows": [
        {"part": "B1", "category": "Salary", "amount_paise": 100000000}]}
    importers.save_ais_document(scope, AY, doc, "ais.json")
    got = importers.get_ais_document(scope, AY)
    assert got["doc"]["name"] == "TEST"
    assert got["source_file"] == "ais.json"

    # a re-import replaces the single current document (UNIQUE taxpayer+ay)
    importers.save_ais_document(scope, AY, {"pan": "X", "name": "REV",
                                            "rows": []}, "ais2.json")
    got = importers.get_ais_document(scope, AY)
    assert got["doc"]["name"] == "REV"
    assert got["source_file"] == "ais2.json"


def test_26as_document_save_get(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    doc = {"pan": "AAAPL1234C", "parts": [{"part_id": "I", "deductors": []}]}
    importers.save_26as_document(scope, AY, doc, "26as.pdf")
    got = importers.get_26as_document(scope, AY)
    assert got["doc"]["pan"] == "AAAPL1234C"
    assert importers.get_ais_document(scope, AY) is None      # kept separate
