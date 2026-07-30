"""Partner-only administration: users, audit chain."""

from itrapp import guard

ctx = guard.page(roles={"Partner"})

import pandas as pd
import streamlit as st

from itrapp import auth
from itrcore.db import auditlog
from itrcore.db.repos import users as users_repo

st.title("Users & audit")

st.subheader("Staff accounts")
rows = users_repo.list_users(ctx.principal)
df = pd.DataFrame([dict(r) for r in rows])
st.dataframe(df, hide_index=True, width="stretch")

flash = st.session_state.pop("admin_user_flash", None)
if flash:
    st.success(flash)

with st.expander("Create staff user"):
    with st.form("new_staff"):
        username = st.text_input("Username")
        role = st.selectbox("Role", ("Staff", "Manager", "Partner"))
        if st.form_submit_button("Create"):
            one_time = auth.generate_password()
            try:
                users_repo.create_user(ctx.principal, username, one_time,
                                       role, must_change_pw=True)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.info(f"Created **{username}** — one-time password: "
                        f"`{one_time}` (must be changed at first sign-in)")

with st.expander("Reset a password / activate / deactivate"):
    account_labels = {
        f"{r['id']} · {r['username']} — {r['role']} "
        f"({'active' if r['active'] else 'INACTIVE'})": r["id"]
        for r in rows}
    chosen_account = st.selectbox("Account", list(account_labels),
                                  key="admin_account_pick")
    target = account_labels[chosen_account]
    if target == ctx.principal.user_id:
        st.caption("⚠️ This is your own account — it cannot be deactivated.")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Reset password"):
            one_time = auth.generate_password()
            try:
                users_repo.reset_password(ctx.principal, target, one_time)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.info(f"New one-time password: `{one_time}`")
    with col2:
        if st.button("Activate"):
            try:
                users_repo.set_active(ctx.principal, target, True)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.session_state["admin_user_flash"] = (
                    f"Activated {chosen_account}.")
                st.rerun()
    with col3:
        if st.button("Deactivate"):
            try:
                users_repo.set_active(ctx.principal, target, False)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.session_state["admin_user_flash"] = (
                    f"Deactivated {chosen_account} — they can no longer "
                    "sign in.")
                st.rerun()

st.divider()
st.subheader("Backups")
from itrcore.db import connection as db_conn

if st.button("💾 Back up database now"):
    dest = db_conn.backup()
    st.success(f"Backup written to `{dest}`")
backups_dir = db_conn.data_dir() / "backups"
if backups_dir.exists():
    files = sorted(backups_dir.glob("*.db"), reverse=True)[:10]
    for f in files:
        st.caption(f"🗄️ {f.name} · {f.stat().st_size:,} bytes")
st.caption("Also back up the encryption key file at "
           "`%APPDATA%\\itr-portal\\keys.json` — without it the encrypted "
           "PII and documents are unrecoverable.")

st.divider()
st.subheader("Audit log integrity")
if st.button("Verify hash chain"):
    ok, bad_id = auditlog.verify_chain()
    if ok:
        st.success("Audit chain verified — no tampering detected.")
    else:
        st.error(f"Chain BROKEN at audit row id {bad_id} — investigate "
                 "immediately.")

st.subheader("Recent audit entries")
entries = auditlog.recent(limit=100)
if entries:
    st.dataframe(pd.DataFrame([dict(r) for r in entries]).drop(
        columns=["prev_hash", "row_hash"]), hide_index=True,
        width="stretch")
