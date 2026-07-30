"""AppTest flows for the two admin actions users reported broken: the
Users-page deactivate button and client (taxpayer) deactivation, plus the
Clients page showing/editing full client information."""

from pathlib import Path

from itrapp.auth import ACTIVE_TAXPAYER_KEY, AuthContext, SESSION_KEY

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAGES = PROJECT_ROOT / "app_pages" / "staff"


def _apptest(page: str, principal, taxpayer_id=None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PAGES / page), default_timeout=60)
    at.session_state[SESSION_KEY] = AuthContext(principal=principal)
    if taxpayer_id is not None:
        at.session_state[ACTIVE_TAXPAYER_KEY] = taxpayer_id
    return at


def test_admin_deactivate_button_deactivates_selected_user(partner):
    from itrcore.db.repos import users
    uid = users.create_user(partner, "staff2", "password1", "Staff")

    at = _apptest("admin.py", partner)
    at.run()
    assert not at.exception, at.exception
    label = f"{uid} · staff2 — Staff (active)"
    at.selectbox(key="admin_account_pick").select(label)
    next(b for b in at.button if b.label == "Deactivate").click()
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]

    row = next(r for r in users.list_users(partner) if r["id"] == uid)
    assert row["active"] == 0
    # flash message shown after the rerun
    assert any("Deactivated" in s.value for s in at.success)


def test_admin_deactivating_self_shows_error_not_silence(partner):
    from itrcore.db.repos import users
    at = _apptest("admin.py", partner)
    at.run()
    label = f"{partner.user_id} · {partner.username} — Partner (active)"
    at.selectbox(key="admin_account_pick").select(label)
    next(b for b in at.button if b.label == "Deactivate").click()
    at.run()
    assert not at.exception, at.exception
    assert any("own account" in e.value for e in at.error)
    row = next(r for r in users.list_users(partner)
               if r["id"] == partner.user_id)
    assert row["active"] == 1


def test_clients_page_shows_information(partner, taxpayer_id):
    at = _apptest("taxpayers.py", partner, taxpayer_id)
    at.run()
    assert not at.exception, at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Date of birth" in body and "1985-06-15" in body
    assert "Email" in body and "ramesh@example.com" in body
    assert "Residential status" in body
    # PAN stays masked until an explicit reveal
    pan_fields = [t for t in at.text_input if t.label == "PAN"]
    assert pan_fields and pan_fields[0].value == "XXXXX1234C"


def test_pan_reveal_button_actually_reveals(partner, taxpayer_id):
    """Regression: a fixed widget key kept the field masked after Reveal
    (keyed widgets ignore a changed value param)."""
    at = _apptest("taxpayers.py", partner, taxpayer_id)
    at.run()
    masked = at.text_input(
        key=f"pii_reveal_pan_{taxpayer_id}_display_masked")
    assert masked.value == "XXXXX1234C"
    next(b for b in at.button if b.label == "Reveal").click()
    at.run()
    assert not at.exception, at.exception
    revealed = at.text_input(
        key=f"pii_reveal_pan_{taxpayer_id}_display_full")
    assert revealed.value == "AAAPL1234C"
    # Hide flips it back to the masked widget
    next(b for b in at.button if b.label == "Hide").click()
    at.run()
    assert at.text_input(
        key=f"pii_reveal_pan_{taxpayer_id}_display_masked"
    ).value == "XXXXX1234C"


def test_clients_page_deactivate_and_reactivate(partner, taxpayer_id):
    from itrcore.db.repos import taxpayers

    at = _apptest("taxpayers.py", partner, taxpayer_id)
    at.run()
    assert not at.exception, at.exception
    at.checkbox(key=f"deact_sure_{taxpayer_id}").check()
    at.run()
    at.button(key=f"deact_btn_{taxpayer_id}").click()
    at.run()
    assert not at.exception, at.exception

    archived = {s.id: s.status
                for s in taxpayers.list_taxpayers(partner,
                                                  include_archived=True)}
    assert archived[taxpayer_id] == "archived"
    # hidden from the default (active-only) list the sidebar uses
    assert taxpayer_id not in {
        s.id for s in taxpayers.list_taxpayers(partner)}

    # reactivate through the page (needs the show-deactivated toggle on)
    at2 = _apptest("taxpayers.py", partner)
    at2.session_state["clients_show_archived"] = True
    at2.run()
    assert not at2.exception, at2.exception
    at2.button(key=f"react_btn_{taxpayer_id}").click()
    at2.run()
    assert not at2.exception, at2.exception
    active = {s.id: s.status for s in taxpayers.list_taxpayers(partner)}
    assert active[taxpayer_id] == "active"


def test_clients_page_identity_edit_updates_pan(partner, taxpayer_id):
    from itrcore.db.repos import taxpayers

    at = _apptest("taxpayers.py", partner, taxpayer_id)
    at.run()
    assert not at.exception, at.exception
    at.text_input(key=f"identity_pan_{taxpayer_id}").set_value("AAAPZ9999K")
    next(b for b in at.button if b.label == "Update identity").click()
    at.run()
    assert not at.exception, at.exception

    from itrcore.authz import mint_scope
    tp = taxpayers.get_taxpayer(mint_scope(partner, taxpayer_id))
    assert tp.pan == "AAAPZ9999K" and tp.pan_masked == "XXXXX9999K"


def test_edited_and_deleted_rows_reflect_in_computation(partner,
                                                        taxpayer_id):
    """Regression: data_editor deltas must be cleared on save — the old
    flow replayed them against the re-seeded data, cascading deletions
    onto the wrong rows on the next rerun."""
    from itrcore.authz import mint_scope
    from itrcore.db.repos import computations as comp_repo

    scope = mint_scope(partner, taxpayer_id)
    ay = "2026-27"
    key = f"{taxpayer_id}_{ay}"
    comp_repo.save_inputs(scope, ay, {
        "ay": ay,
        "salaries": [{"employer_name": "Acme Ltd", "employer_tan": "M",
                      "gross_salary": 1000000, "exempt_allowances": 0,
                      "prof_tax": 0, "entertainment_allowance": 0}],
        "tds": [
            {"tan": "AAAA00001A", "deductor": "Keep Me",
             "section": "194A", "amount": 5000},
            {"tan": "BBBB00002B", "deductor": "Delete Me",
             "section": "194C", "amount": 7000}],
        "other_sources": {}})

    at = _apptest("computation.py", partner, taxpayer_id)
    at.run()
    assert not at.exception, at.exception
    # Manual edits exactly as the widget records them: delete TDS row 1,
    # raise the salary to 12L.
    at.session_state[f"tds_{key}"] = {
        "edited_rows": {}, "added_rows": [], "deleted_rows": [1]}
    at.session_state[f"sal_{key}"] = {
        "edited_rows": {0: {"gross_salary": 1200000}}, "added_rows": [],
        "deleted_rows": []}
    next(b for b in at.button if "Compute" in b.label).click()
    at.run()
    assert not at.exception, at.exception

    saved = comp_repo.load_inputs(scope, ay)
    assert [t["deductor"] for t in saved["tds"]] == ["Keep Me"]
    assert saved["salaries"][0]["gross_salary"] == 1200000
    res = at.session_state[f"comp_result_{key}"]
    assert res["result"]["new"]["tds_total"] == 5000
    assert res["inputs"]["salaries"][0]["gross_salary"] == 1200000
    # deltas cleared → no replay against the new seed
    assert at.session_state[f"tds_{key}"]["deleted_rows"] == []

    # the old bug: this extra rerun cascaded the deletion onto 'Keep Me'
    at.run()
    saved2 = comp_repo.load_inputs(scope, ay)
    assert [t["deductor"] for t in saved2["tds"]] == ["Keep Me"]


def test_filing_page_unblocks_snapshot_and_finalise_inline(partner,
                                                           taxpayer_id):
    """Regression: computed-but-never-snapshotted work dead-ended the
    Filing page ('no saved computations' / 'not finalised') even after
    reconciliation. Both steps must now be doable on the page itself."""
    from itrcore.authz import mint_scope
    from itrcore.db.repos import computations as comp_repo
    from itrcore.tax import serde
    from itrcore.tax.engine import compute_assessment

    ay = "2026-27"
    inputs_dict = {
        "ay": ay,
        "salaries": [{"employer_name": "Acme Ltd", "employer_tan": "M",
                      "gross_salary": 800000, "exempt_allowances": 0,
                      "prof_tax": 0, "entertainment_allowance": 0}],
        "other_sources": {}}
    result = compute_assessment(serde.inputs_from_dict(inputs_dict))
    session_result = {
        "inputs": inputs_dict, "result": serde.result_to_dict(result),
        "meta": {"engine_version": result.engine_version,
                 "config_hash": result.config_hash,
                 "inputs_digest": result.inputs_digest,
                 "recommended": result.recommended}}

    at = _apptest("filing.py", partner, taxpayer_id)
    at.session_state[f"comp_result_{taxpayer_id}_{ay}"] = session_result
    at.run()
    assert not at.exception, at.exception
    # Step 1: the page offers to snapshot the session's computed result.
    next(b for b in at.button if "snapshot v1" in b.label).click()
    at.run()
    assert not at.exception, at.exception
    scope = mint_scope(partner, taxpayer_id)
    assert comp_repo.list_snapshots(scope, ay)

    # Step 2: it now offers to finalise v1 inline.
    next(b for b in at.button if "Mark v1 final" in b.label).click()
    at.run()
    assert not at.exception, at.exception
    snap = comp_repo.get_snapshot(scope, ay, 1)
    assert snap["status"] == "final"
    # Past both stops: the eligibility verdict renders.
    assert any("ITR-1" in (s.value or "") for s in at.success) or \
        any("applies" in (w.value or "") for w in at.warning)


def test_income_profile_subheads_grouped_under_overheads(partner,
                                                         taxpayer_id):
    """Clicking the Business & Profession overhead reveals its subheads;
    ticking one and saving persists through the grouped widget."""
    from itrcore.authz import mint_scope
    from itrcore.db.repos import taxpayers

    at = _apptest("taxpayers.py", partner, taxpayer_id)
    at.run()
    assert not at.exception, at.exception
    # the business overhead's subheads render as individually-keyed boxes
    at.checkbox(key=f"prof_{taxpayer_id}_has_business_books").check()
    at.run()
    next(b for b in at.button if b.label == "Save profile").click()
    at.run()
    assert not at.exception, at.exception

    tp = taxpayers.get_taxpayer(mint_scope(partner, taxpayer_id))
    assert tp.profile["has_business_books"] is True
    assert tp.profile["has_salary"] is True          # fixture flag kept
