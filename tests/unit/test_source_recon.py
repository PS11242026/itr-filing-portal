"""AIS / TIS vs Form 26AS cross-reconciliation → client queries."""

from itrcore.authz import mint_scope
from itrcore.db.repos import importers, queries
from itrcore.recon import source_recon

AY = "2026-27"


def _seed(scope, *, ais=None, tis=None, as26=None):
    if ais:
        importers.import_ais(scope, AY, ais, "ais.json")
    if tis:
        importers.import_tis(scope, AY, tis, "tis.json")
    if as26:
        importers.import_26as(scope, AY, as26)


def test_mismatch_between_sources_flagged(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    _seed(scope,
          ais=[{"category": "interest_deposits", "description": "FD",
                "amount_paise": 100000_00}],
          tis=[{"category": "interest_deposits", "description": "FD",
                "processed_value_paise": 100000_00,
                "derived_value_paise": 100000_00}],
          as26=[{"tan": "MUMA12345B", "deductor_name": "BANK",
                 "section": "194A", "amount_paid_paise": 60000_00,
                 "tds_paise": 6000_00}])

    items = {i.category: i for i in source_recon.compare(scope, AY)}
    assert items["interest_deposits"].status == "mismatch"
    assert items["interest_deposits"].ais_inr == 100000
    assert items["interest_deposits"].as26_inr == 60000


def test_matching_sources(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    _seed(scope,
          ais=[{"category": "salary", "description": "Salary",
                "amount_paise": 1500000_00}],
          as26=[{"tan": "MUMA12345B", "deductor_name": "ACME",
                 "section": "192", "amount_paid_paise": 1500000_00,
                 "tds_paise": 120000_00}])

    items = {i.category: i for i in source_recon.compare(scope, AY)}
    assert items["salary"].status == "matched"


def test_one_sided_is_not_a_mismatch(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    _seed(scope, ais=[{"category": "interest_savings", "description": "SB",
                       "amount_paise": 12000_00}])

    items = {i.category: i for i in source_recon.compare(scope, AY)}
    assert items["interest_savings"].status == "one_sided"
    assert source_recon.mismatches(scope, AY) == []


def test_mismatch_raises_a_client_query(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    _seed(scope,
          ais=[{"category": "dividend", "description": "Div",
                "amount_paise": 50000_00}],
          as26=[{"tan": "MUMA12345B", "deductor_name": "CO",
                 "section": "194", "amount_paid_paise": 20000_00,
                 "tds_paise": 2000_00}])

    item = source_recon.mismatches(scope, AY)[0]
    queries.raise_query(scope, ay=AY, question=item.query_text(),
                        topic="source_recon")

    raised = queries.list_queries(scope, AY)
    assert len(raised) == 1
    assert "Dividend" in raised[0]["question"]
