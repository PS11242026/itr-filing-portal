"""ITD portal connector: mock pull, browser-driven pull orchestration (with a
fake browser), and the encrypted-AIS decrypt/import path."""

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from itrcore.analysis import autofill
from itrcore.authz import mint_scope
from itrcore.db.repos import importers
from itrcore.ingest import ais_crypto, itd_portal

AY = "2026-27"


# --------------------------------------------------------------- mock client
def test_mock_pull_returns_all_three_payloads():
    pull = itd_portal.MockITDPortalClient().pull(
        pan="AAAPL1234C", password="x", ay=AY)
    assert pull.ais_json and pull.tis_json and pull.as26_text
    assert any("MOCK" in m for m in pull.messages)


def test_mock_pull_requires_pan_and_password():
    with pytest.raises(itd_portal.PortalError):
        itd_portal.MockITDPortalClient().pull(pan="", password="", ay=AY)


def test_pull_and_import_populates_all_sources(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    out = itd_portal.pull_and_import(
        scope, AY, itd_portal.MockITDPortalClient(),
        pan="AAAPL1234C", password="pw")

    assert out["counts"]["ais"] >= 1
    assert out["counts"]["tis"] >= 1
    assert out["counts"]["as26"] >= 1
    assert importers.list_ais(scope, AY)
    assert importers.list_tis(scope, AY)
    assert importers.list_tds(scope, AY, source="26as")


def test_pulled_data_flows_into_autofill(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    itd_portal.pull_and_import(
        scope, AY, itd_portal.MockITDPortalClient(),
        pan="AAAPL1234C", password="pw")

    a = autofill.assemble(scope, AY)
    # TIS derived deposit interest (Rs 64,000 in the mock) drives fd_interest.
    assert a.inputs["other_sources"]["fd_interest"] == 64000
    assert any(s.origin.startswith("TIS") for s in a.sources)
    assert a.inputs["tds"]  # 26AS rows became TDS entries


# ----------------------------------------------------- browser-driven client
class FakeBrowser:
    """Records the pull steps and returns canned payloads; lets us exercise the
    live client's orchestration without launching Chrome."""

    def __init__(self, *, ais=None, pdf=None, fail_ais=False, fail_26as=False):
        self.ais, self.pdf = ais, pdf
        self.fail_ais, self.fail_26as = fail_ais, fail_26as
        self.calls: list = []

    def open_and_prefill_login(self, *, pan, password):
        self.calls.append(("login", pan, password))

    def wait_until_logged_in(self, timeout):
        self.calls.append(("wait", timeout))

    def download_ais_json(self, timeout):
        self.calls.append(("ais", timeout))
        if self.fail_ais:
            raise itd_portal.PortalError("AIS unavailable")
        return self.ais

    def download_26as_pdf(self, *, ay, timeout):
        self.calls.append(("as26", ay))
        if self.fail_26as:
            raise itd_portal.PortalError("26AS unavailable")
        return self.pdf

    def quit(self):
        self.calls.append(("quit",))


def test_selenium_client_orchestrates_full_pull():
    fb = FakeBrowser(ais=b'{"data":[]}', pdf=b"%PDF-1.7 fake")
    pull = itd_portal.SeleniumITDPortalClient(browser=fb).pull(
        pan="AAAPL1234C", password="pw", ay=AY)
    assert pull.ais_json == fb.ais and pull.as26_pdf == fb.pdf
    assert [c[0] for c in fb.calls] == ["login", "wait", "ais", "as26", "quit"]
    # PAN + password were pre-filled by the tool, not typed by a human.
    assert fb.calls[0] == ("login", "AAAPL1234C", "pw")


def test_selenium_client_returns_partial_when_26as_fails():
    fb = FakeBrowser(ais=b'{"data":[]}', fail_26as=True)
    pull = itd_portal.SeleniumITDPortalClient(browser=fb).pull(
        pan="AAAPL1234C", password="pw", ay=AY)
    assert pull.ais_json and pull.as26_pdf is None
    assert ("quit",) in fb.calls          # browser always cleaned up


def test_selenium_client_raises_when_nothing_retrieved():
    fb = FakeBrowser(fail_ais=True, fail_26as=True)
    with pytest.raises(itd_portal.PortalError):
        itd_portal.SeleniumITDPortalClient(browser=fb).pull(
            pan="AAAPL1234C", password="pw", ay=AY)
    assert ("quit",) in fb.calls


def test_selenium_client_requires_pan_and_password():
    with pytest.raises(itd_portal.PortalError):
        itd_portal.SeleniumITDPortalClient(browser=FakeBrowser()).pull(
            pan="", password="", ay=AY)


class _ResetFakeBrowser:
    def __init__(self):
        self.calls: list = []

    def open_forgot_password(self, *, pan):
        self.calls.append(("forgot", pan))
        return ["user ID pre-filled", "Forgot Password link: clicked"]

    def quit(self):
        self.calls.append(("quit",))


def test_selenium_reset_step1_opens_browser_and_keeps_it():
    fb = _ResetFakeBrowser()
    out = itd_portal.SeleniumITDPortalClient(browser=fb).reset_password(
        pan="AAAPL1234C")
    assert out.status == "otp_sent"
    assert ("forgot", "AAAPL1234C") in fb.calls
    assert ("quit",) not in fb.calls      # human completes in the browser
    assert "pre-filled" in out.message


def test_selenium_reset_step2_records_without_browser():
    fb = _ResetFakeBrowser()
    client = itd_portal.SeleniumITDPortalClient(browser=fb)
    out = client.reset_password(pan="AAAPL1234C", otp="completed-on-portal",
                                new_password="NewPw123")
    assert out.status == "done"
    assert fb.calls == []                 # step 2 never touches the browser
    missing = client.reset_password(pan="AAAPL1234C",
                                    otp="completed-on-portal")
    assert missing.status == "error"


def test_default_client_is_the_live_browser_client():
    assert isinstance(itd_portal.default_client(),
                      itd_portal.SeleniumITDPortalClient)


# --------------------------------------- 26AS navigation helpers (no Chrome)
class _FakeSwitchTo:
    def __init__(self):
        self.windows: list = []

    def window(self, handle):
        self.windows.append(handle)


class _FakeDriver:
    """Just enough driver surface for the tab-switch / text-match helpers."""

    def __init__(self, handles, url=""):
        self.window_handles = list(handles)
        self.current_url = url
        self.switch_to = _FakeSwitchTo()

    def find_elements(self, by, value):
        return []


def _browser(driver) -> "itd_portal.SeleniumPortalBrowser":
    b = itd_portal.SeleniumPortalBrowser("unused_download_dir")
    b._driver = driver
    return b


def test_switch_to_traces_switches_to_the_new_tab():
    d = _FakeDriver(["efiling", "ais", "traces"])
    trace: list = []
    _browser(d)._switch_to_traces({"efiling", "ais"}, trace)
    assert d.switch_to.windows == ["traces"]
    assert "TRACES tab opened" in trace


def test_switch_to_traces_accepts_same_tab_navigation():
    d = _FakeDriver(["efiling"],
                    url="https://services.tdscpc.gov.in/serv/tapn/x.xhtml")
    trace: list = []
    _browser(d)._switch_to_traces({"efiling"}, trace)
    assert d.switch_to.windows == []          # no tab switch needed
    assert "TRACES opened in the same tab" in trace


def test_click_by_text_records_misses_instead_of_silence():
    trace: list = []
    ok = _browser(_FakeDriver(["t"]))._click_by_text(
        ("26as",), timeout=0, trace=trace, step="View Form 26AS item")
    assert ok is False
    assert trace == ["View Form 26AS item: NOT FOUND"]


# ------------------------------------------------ encrypted AIS via the pull
def _encrypt_v15(*, pan: str, dob: str) -> bytes:
    """A minimal encrypted AIS v15 envelope keyed to pan/dob, so the pull's
    decrypt-then-import path can be exercised end to end."""
    tree = {
        "metadata": {"loggedInPan": pan, "jsonVersion": "15.0.0"},
        "partA": {"columnLabel": ["Permanent Account Number (PAN)",
                                  "Name of Assessee", "Date of Birth"],
                  "columnData": [pan, "RAMESH KUMAR", dob]},
        "partB": {"sections": [
            {"title": "Part B1-Information relating to tax deducted or "
                      "collected at source",
             "elements": [{
                 "l2": {"columnLabel": [
                     "Information Category", "Information Code",
                     "Information Description", "Information Source", "Count",
                     "Amount", "Information Category Code", "Derived Amount"],
                     "columnData": [["Interest from deposit", "TDS-194A",
                                     "Interest (194A)", "SBI (BLRC00002B)",
                                     "1", "50,000.00", "IND", None]]},
                 "l1": {"columnLabel": [{"name": "Quarter"},
                                        {"name": "Amount"}],
                        "columnData": [["Q1", "50,000.00"]]}}]}]}}
    plain = json.dumps(tree).encode()
    iv, salt = bytes(range(16)), bytes(range(16, 32))
    key = hashlib.pbkdf2_hmac(
        "sha256", ais_crypto.build_password(pan, dob).encode(), salt, 1000, 32)
    pad = 16 - (len(plain) % 16)
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(plain + bytes([pad]) * pad) + enc.finalize()
    return (iv.hex() + salt.hex()).encode() + base64.b64encode(ct)


def test_pull_and_import_decrypts_encrypted_ais(partner, taxpayer_id):
    # taxpayer_id fixture: PAN AAAPL1234C, DOB 1985-06-15.
    scope = mint_scope(partner, taxpayer_id)
    env = _encrypt_v15(pan="AAAPL1234C", dob="1985-06-15")
    fb = FakeBrowser(ais=env, fail_26as=True)
    out = itd_portal.pull_and_import(
        scope, AY, itd_portal.SeleniumITDPortalClient(browser=fb),
        pan="AAAPL1234C", password="pw")

    assert out["counts"]["ais"] >= 1
    assert out["counts"]["tis"] >= 1        # derived TIS is counted, not 0
    assert importers.list_ais(scope, AY)
    assert importers.list_tis(scope, AY)                 # derived from AIS
    saved = importers.get_ais_document(scope, AY)         # rich v15 doc saved
    assert saved and saved["doc"]["rows"]


def test_pull_without_dob_parks_ais_and_keeps_rest(partner):
    from itrcore.db.repos import taxpayers
    tid = taxpayers.create_taxpayer(
        partner, full_name="No Dob Client", pan="AAAPL9999D")   # no DOB
    scope = mint_scope(partner, tid)
    env = _encrypt_v15(pan="AAAPL9999D", dob="01/01/1950")
    fb = FakeBrowser(ais=env, fail_26as=True)

    out = itd_portal.pull_and_import(          # must NOT raise
        scope, AY, itd_portal.SeleniumITDPortalClient(browser=fb),
        pan="AAAPL9999D", password="pw")

    assert "ais" not in out["counts"]
    assert not importers.list_ais(scope, AY)
    assert "PARKED" in " ".join(out["messages"])
    assert importers.get_pending_ais(scope, AY) is not None


def test_parked_ais_imports_itself_once_dob_arrives(partner):
    from itrcore.db.repos import taxpayers
    tid = taxpayers.create_taxpayer(
        partner, full_name="Parked Client", pan="AAAPL9999D")
    scope = mint_scope(partner, tid)
    env = _encrypt_v15(pan="AAAPL9999D", dob="01/01/1950")
    importers.save_pending_ais(scope, AY, env, "ITD portal (parked)")

    # DOB still missing → stays parked.
    assert itd_portal.import_pending_ais(scope, AY) is None
    # Wrong DOB → decrypt fails, stays parked for correction.
    taxpayers.update_contact(scope, dob="1990-05-05")
    assert itd_portal.import_pending_ais(scope, AY) is None
    assert importers.get_pending_ais(scope, AY) is not None

    taxpayers.update_contact(scope, dob="1950-01-01")
    out = itd_portal.import_pending_ais(scope, AY)

    assert out and out["counts"]["ais"] >= 1
    assert importers.list_ais(scope, AY)
    assert importers.get_pending_ais(scope, AY) is None    # cleared
    assert itd_portal.import_pending_ais(scope, AY) is None   # idempotent


def test_retained_pull_file_recovered_from_disk(partner, monkeypatch,
                                                tmp_path):
    # A pull from BEFORE parking existed left only the retained file on
    # disk (portal filename carries a masked PAN + the FY token).
    from itrcore.db.repos import taxpayers
    tid = taxpayers.create_taxpayer(
        partner, full_name="Retained Client", pan="AAAPL9999D",
        dob="1950-01-01")
    scope = mint_scope(partner, tid)
    pull_dir = tmp_path / "downloads"
    pull_dir.mkdir()
    monkeypatch.setenv("ITR_PORTAL_PULL_DIR", str(pull_dir))
    env = _encrypt_v15(pan="AAAPL9999D", dob="01/01/1950")
    (pull_dir / "20260723_XXXPL9999X_2025-26_AIS_23072026.json"
     ).write_bytes(env)
    # A decoy that cannot decrypt with this client's PAN must be skipped.
    (pull_dir / "20260723_XXXPL9999X_2025-26_AIS_22072026.json"
     ).write_bytes(_encrypt_v15(pan="AAAPL0000A", dob="01/01/1950"))

    out = itd_portal.import_pending_ais(scope, AY)

    assert out and out["counts"]["ais"] >= 1
    assert importers.list_ais(scope, AY)
    assert importers.get_ais_document(scope, AY)
    # With the document imported, later renders do nothing.
    assert itd_portal.import_pending_ais(scope, AY) is None


def test_pull_backfills_missing_dob_from_plain_ais(partner):
    from itrcore.db.repos import taxpayers
    tid = taxpayers.create_taxpayer(
        partner, full_name="Plain Ais Client", pan="AAAPL8888E")
    scope = mint_scope(partner, tid)
    plain = json.dumps({
        "metadata": {"loggedInPan": "AAAPL8888E", "jsonVersion": "15.0.0"},
        "partA": {"columnLabel": ["Permanent Account Number (PAN)",
                                  "Name of Assessee", "Date of Birth"],
                  "columnData": ["AAAPL8888E", "PLAIN CLIENT",
                                 "21/03/1958"]},
        "partB": {"sections": [
            {"title": "Part B1-Information relating to tax deducted or "
                      "collected at source",
             "elements": [{
                 "l2": {"columnLabel": [
                     "Information Category", "Information Code",
                     "Information Description", "Information Source",
                     "Count", "Amount", "Information Category Code",
                     "Derived Amount"],
                     "columnData": [["Salary", "TDS-192", "Salary",
                                     "EMP (BLRC00001A)", "1",
                                     "10,00,000.00", "SAL", None]]},
                 "l1": {"columnLabel": [{"name": "Quarter"}],
                        "columnData": [["Q1"]]}}]}]}}).encode()
    fb = FakeBrowser(ais=plain, fail_26as=True)

    out = itd_portal.pull_and_import(
        scope, AY, itd_portal.SeleniumITDPortalClient(browser=fb),
        pan="AAAPL8888E", password="pw")

    assert out["counts"]["ais"] >= 1
    # The AIS partA DOB landed on the client record automatically.
    assert taxpayers.get_taxpayer(scope).dob == "1958-03-21"
    assert any("saved to the client record" in m for m in out["messages"])


# ----------------------------------------------------------- password reset
def test_mock_reset_is_two_step():
    c = itd_portal.MockITDPortalClient()
    assert c.reset_password(pan="AAAPL1234C").status == "otp_sent"
    done = c.reset_password(pan="AAAPL1234C", otp="123456",
                            new_password="NewPw123")
    assert done.status == "done"


def test_mock_reset_needs_new_password():
    out = itd_portal.MockITDPortalClient().reset_password(
        pan="AAAPL1234C", otp="123456")
    assert out.status == "error"


def test_reset_stores_new_password_in_vault(partner, taxpayer_id):
    from itrcore.db.repos import portal_credentials as creds
    scope = mint_scope(partner, taxpayer_id)
    out = itd_portal.reset_password(
        scope, itd_portal.MockITDPortalClient(), pan="AAAPL1234C",
        new_password="Fresh!pw9", otp="123456")
    assert out.status == "done"
    assert creds.get_password(scope) == "Fresh!pw9"


# ------------------------------------------------- browser-driven ITR upload
class FilingFakeBrowser:
    def __init__(self, *, fail_login=False, fail_upload=False):
        self.fail_login = fail_login
        self.fail_upload = fail_upload
        self.calls: list = []

    def open_and_prefill_login(self, *, pan, password):
        self.calls.append(("login", pan, password))

    def wait_until_logged_in(self, timeout):
        self.calls.append(("wait", timeout))
        if self.fail_login:
            raise itd_portal.PortalError("login timed out")

    def upload_itr_json(self, *, json_path, ay, timeout):
        self.calls.append(("upload", json_path, ay))
        if self.fail_upload:
            raise itd_portal.PortalError("no upload control")
        return ["JSON attached - review and submit in the browser"]

    def quit(self):
        self.calls.append(("quit",))


def test_filing_client_uploads_and_leaves_browser_open(tmp_path):
    json_path = tmp_path / "ITR1.json"
    json_path.write_text("{}")
    fb = FilingFakeBrowser()
    msgs = itd_portal.SeleniumFilingClient(browser=fb).upload(
        pan="AAAPL1234C", password="pw", ay=AY,
        json_path=str(json_path))
    assert [c[0] for c in fb.calls] == ["login", "wait", "upload"]
    assert ("quit",) not in fb.calls        # human finishes in the browser
    assert any("BEFORE submission" in m for m in msgs)


def test_filing_client_quits_browser_when_login_fails(tmp_path):
    json_path = tmp_path / "ITR1.json"
    json_path.write_text("{}")
    fb = FilingFakeBrowser(fail_login=True)
    with pytest.raises(itd_portal.PortalError):
        itd_portal.SeleniumFilingClient(browser=fb).upload(
            pan="AAAPL1234C", password="pw", ay=AY,
            json_path=str(json_path))
    assert ("quit",) in fb.calls


def test_filing_client_keeps_browser_on_upload_failure(tmp_path):
    json_path = tmp_path / "ITR1.json"
    json_path.write_text("{}")
    fb = FilingFakeBrowser(fail_upload=True)
    with pytest.raises(itd_portal.PortalError):
        itd_portal.SeleniumFilingClient(browser=fb).upload(
            pan="AAAPL1234C", password="pw", ay=AY,
            json_path=str(json_path))
    assert ("quit",) not in fb.calls        # salvageable by the human


def test_filing_client_requires_existing_json():
    with pytest.raises(itd_portal.PortalError, match="not found"):
        itd_portal.SeleniumFilingClient(browser=FilingFakeBrowser()).upload(
            pan="AAAPL1234C", password="pw", ay=AY,
            json_path="X:/nope/missing.json")
