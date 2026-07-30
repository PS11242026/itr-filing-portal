"""TIS parser + importer, and the AIS/TIS pairing."""

import json

import pytest

from itrcore.authz import mint_scope
from itrcore.db.repos import importers
from itrcore.ingest import tis

AY = "2026-27"


def _tis_bytes(categories):
    return json.dumps({"pan": "AAAPL1234C", "ay": AY,
                       "tisPartBDetails": categories}).encode()


def test_parse_tis_classifies_and_keeps_both_values():
    rows = tis.parse_tis_json(_tis_bytes([
        {"infoCategory": "Interest from savings bank",
         "processedValue": 12000, "derivedValue": 14000},
        {"infoCategory": "Dividend", "processedValue": 8000,
         "derivedValue": 8000}]))

    by = {r["category"]: r for r in rows}
    assert by["interest_savings"]["processed_value_paise"] == 1200000
    assert by["interest_savings"]["derived_value_paise"] == 1400000
    assert by["dividend"]["derived_value_paise"] == 800000


def test_parse_tis_mirrors_missing_derived_value():
    rows = tis.parse_tis_json(_tis_bytes([
        {"infoCategory": "Interest from deposit", "processedValue": 50000}]))

    r = rows[0]
    assert r["category"] == "interest_deposits"
    assert r["derived_value_paise"] == r["processed_value_paise"] == 5000000


def test_parse_tis_rejects_unrecognised():
    with pytest.raises(ValueError):
        tis.parse_tis_json(b'{"pan": "AAAPL1234C"}')


def test_import_and_list_tis(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    n = importers.import_tis(scope, AY, tis.parse_tis_json(_tis_bytes([
        {"infoCategory": "Interest from savings bank",
         "processedValue": 12000, "derivedValue": 14000}])), "tis.json")

    assert n == 1
    rows = importers.list_tis(scope, AY)
    assert rows[0]["category"] == "interest_savings"
    assert rows[0]["derived_value_paise"] == 1400000
