"""Test harness: every test gets an isolated data dir, key file and DB."""

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ITR_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ITR_PORTAL_KEY_FILE", str(tmp_path / "keys.json"))
    from itrcore.db import crypto, migrate
    crypto.reset_cipher_cache()
    migrate.upgrade()
    yield tmp_path
    crypto.reset_cipher_cache()


@pytest.fixture()
def partner(fresh_db):
    from itrcore.db.repos import users
    users.create_user(None, "partner1", "secret123", "Partner")
    return users.authenticate("partner1", "secret123").principal


@pytest.fixture()
def taxpayer_id(partner):
    from itrcore.db.repos import taxpayers
    return taxpayers.create_taxpayer(
        partner, full_name="Ramesh Kumar", pan="AAAPL1234C",
        dob="1985-06-15", email="ramesh@example.com",
        profile={"has_salary": True, "wants_old_regime": True})


@pytest.fixture()
def client_principal(partner, taxpayer_id):
    from itrcore.db.repos import users
    users.create_user(partner, "client1", "clientpw1", "Client",
                      taxpayer_id=taxpayer_id)
    return users.authenticate("client1", "clientpw1").principal
