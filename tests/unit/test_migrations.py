"""Migration runner behaviour."""


def test_upgrade_is_idempotent(fresh_db):
    from itrcore.db import migrate
    assert migrate.upgrade() == []          # fresh_db already migrated
    assert migrate.pending() == []


def test_schema_has_expected_tables(fresh_db):
    from itrcore.db import connection
    tables = {r["name"] for r in connection.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"users", "taxpayers", "documents", "transactions",
                "explanation_queries", "computations", "filings",
                "tds_entries", "ais_entries", "audit_log", "column_maps",
                "schema_version"}
    missing = expected - tables
    assert not missing, f"missing tables: {missing}"
