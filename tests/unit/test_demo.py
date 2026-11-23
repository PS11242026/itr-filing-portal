"""Public demo mode: temp storage, fictional seed, no live portal, and shared
accounts that visitors cannot lock or take over."""

import pytest

from itrcore import demo
from itrcore.ingest import itd_portal


@pytest.fixture()
def demo_on(monkeypatch):
    monkeypatch.setenv("ITR_PORTAL_DEMO", "1")


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("YES", True), ("", False), ("0", False)])
def test_is_demo_flag(monkeypatch, value, expected):
    monkeypatch.setenv("ITR_PORTAL_DEMO", value)
    assert demo.is_demo() is expected


def test_prepare_environment_noop_outside_demo(monkeypatch):
    monkeypatch.delenv("ITR_PORTAL_DEMO", raising=False)
    monkeypatch.delenv("ITR_PORTAL_DATA_DIR", raising=False)
    demo.prepare_environment()
    import os
    assert "ITR_PORTAL_DATA_DIR" not in os.environ


def test_prepare_environment_uses_temp_dir(monkeypatch, demo_on):
    monkeypatch.delenv("ITR_PORTAL_DATA_DIR", raising=False)
    monkeypatch.delenv("ITR_PORTAL_KEY_FILE", raising=False)
    demo.prepare_environment()
    import os
    import tempfile
    assert os.environ["ITR_PORTAL_DATA_DIR"].startswith(tempfile.gettempdir())
    assert os.environ["ITR_PORTAL_KEY_FILE"].startswith(tempfile.gettempdir())


def test_seed_creates_demo_accounts_once(fresh_db, demo_on):
    from itrcore.db.repos import users
    assert demo.seed_if_empty() is True
    for username, password, role, _ in demo.DEMO_ACCOUNTS:
        result = users.authenticate(username, password)
        assert result is not None and result.principal.role == role
    assert demo.seed_if_empty() is False


def test_demo_uses_mock_portal_client(demo_on):
    assert isinstance(itd_portal.default_client(),
                      itd_portal.MockITDPortalClient)


def test_live_portal_client_outside_demo(monkeypatch):
    monkeypatch.delenv("ITR_PORTAL_DEMO", raising=False)
    assert isinstance(itd_portal.default_client(),
                      itd_portal.SeleniumITDPortalClient)


def test_demo_refuses_browser_filing(demo_on, tmp_path):
    json_path = tmp_path / "itr.json"
    json_path.write_text("{}", encoding="utf-8")
    with pytest.raises(itd_portal.PortalError, match="public demo"):
        itd_portal.SeleniumFilingClient().upload(
            pan="AAAPL1234C", password="pw", ay="2026-27",
            json_path=str(json_path))


def test_demo_blocks_account_takeover(fresh_db, demo_on):
    from itrcore.db.repos import users
    demo.seed_if_empty()
    partner = users.authenticate("partner", "partner123").principal
    staff = users.authenticate("staff1", "staff1234").principal
    with pytest.raises(PermissionError):
        users.change_password(partner, "partner123", "hijacked123")
    with pytest.raises(PermissionError):
        users.reset_password(partner, staff.user_id, "hijacked123")
    with pytest.raises(PermissionError):
        users.set_active(partner, staff.user_id, False)
    assert users.authenticate("partner", "partner123") is not None
    assert users.authenticate("staff1", "staff1234") is not None
