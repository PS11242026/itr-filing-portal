"""Self-healing AIS derived rows: refresh_ais_derived re-derives the flat
entries + TIS from the saved document under the CURRENT parser rules."""

import json

from test_ais_v15 import _dividend_tree

from itrcore.authz import mint_scope
from itrcore.db.repos import importers
from itrcore.ingest import ais

AY = "2026-27"


def _saved_doc():
    return ais.parse_ais_document(
        json.dumps(_dividend_tree(with_sft=True)).encode())


def test_refresh_heals_stale_dividend(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    doc = _saved_doc()
    importers.save_ais_document(scope, AY, ais.document_to_dict(doc),
                                "ais.json")
    # The stale state the old parser left behind: B1+B2 double counted.
    importers.import_ais(scope, AY, [
        {"category": "dividend", "description": "Dividend",
         "amount_paise": 60709_00}], "ais.json")
    importers.import_tis(scope, AY, [
        {"category": "dividend", "description": "Dividend",
         "processed_value_paise": 60709_00,
         "derived_value_paise": 60709_00}], "ais.json")

    assert importers.refresh_ais_derived(scope, AY) is True

    ais_rows = {r["category"]: r["amount_paise"]
                for r in importers.list_ais(scope, AY)}
    assert ais_rows["dividend"] == 36854_00          # SFT-only figure
    tis_rows = {r["category"]: r["derived_value_paise"]
                for r in importers.list_tis(scope, AY)}
    assert tis_rows["dividend"] == 36854_00
    # Second run: nothing stale, nothing rewritten.
    assert importers.refresh_ais_derived(scope, AY) is False


def test_refresh_recomputes_buckets_saved_under_old_taxonomy(partner,
                                                             taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    doc_dict = ais.document_to_dict(_saved_doc())
    # Simulate a doc saved before the taxonomy knew family pension: a B7
    # block mis-bucketed as salary.
    doc_dict["rows"].append({
        "part": "B7", "part_title": "Part B7-Any other information",
        "category": "Family pension", "category_code": "",
        "info_code": "OTH", "description": "Family pension",
        "source": "PENSION OFFICE", "count": 1,
        "amount_paise": 100624_00, "derived_paise": None,
        "bucket": "salary",                    # the OLD, wrong bucket
        "detail_labels": [], "detail_rows": []})
    importers.save_ais_document(scope, AY, doc_dict, "ais.json")

    assert importers.refresh_ais_derived(scope, AY) is True
    rows = {r["category"]: r["amount_paise"]
            for r in importers.list_ais(scope, AY)}
    assert rows["family_pension"] == 100624_00
    assert rows.get("salary") is None              # nothing left in salary


def test_refresh_never_overwrites_the_itd_downloaded_tis(partner,
                                                         taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.save_ais_document(scope, AY,
                                ais.document_to_dict(_saved_doc()),
                                "ITD portal")
    # Stale derived AIS entries → refresh must fix them...
    importers.import_ais(scope, AY, [
        {"category": "dividend", "description": "Dividend",
         "amount_paise": 60709_00}], "ITD portal")
    # ...but the ITD's OWN TIS download is authoritative and must stay.
    importers.import_tis(scope, AY, [
        {"category": "dividend", "description": "Dividend",
         "processed_value_paise": 36000_00,
         "derived_value_paise": 36000_00}], "ITD portal (TIS download)")

    assert importers.refresh_ais_derived(scope, AY) is True

    ais_rows = {r["category"]: r["amount_paise"]
                for r in importers.list_ais(scope, AY)}
    assert ais_rows["dividend"] == 36854_00          # derived AIS healed
    tis_rows = importers.list_tis(scope, AY)
    assert len(tis_rows) == 1
    assert tis_rows[0]["derived_value_paise"] == 36000_00   # untouched
    assert "(TIS download)" in tis_rows[0]["source_file"]


def test_refresh_without_saved_document_is_a_noop(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.import_ais(scope, AY, [
        {"category": "dividend", "description": "Dividend",
         "amount_paise": 1000_00}], "ais.json")
    assert importers.refresh_ais_derived(scope, AY) is False
    rows = importers.list_ais(scope, AY)
    assert rows[0]["amount_paise"] == 1000_00      # untouched
