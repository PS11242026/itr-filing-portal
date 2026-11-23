"""Session auth for the portal: login form, AuthContext, sidebar chrome."""

import secrets
import string
from dataclasses import dataclass

import streamlit as st

from itrcore import demo
from itrcore.authz import Principal, TaxpayerScope, mint_scope
from itrcore.db.repos import taxpayers as taxpayers_repo
from itrcore.db.repos import users as users_repo
from itrcore.version import APP_NAME, __version__

SESSION_KEY = "auth_ctx"
ACTIVE_TAXPAYER_KEY = "active_taxpayer_id"


@dataclass
class AuthContext:
    principal: Principal
    must_change_pw: bool = False

    @property
    def is_staff(self) -> bool:
        return self.principal.is_staff

    def own_scope(self) -> TaxpayerScope:
        if self.principal.taxpayer_id is None:
            raise ValueError("this account is not linked to a taxpayer")
        return mint_scope(self.principal, self.principal.taxpayer_id)

    def scope_for(self, taxpayer_id: int) -> TaxpayerScope:
        return mint_scope(self.principal, taxpayer_id)


def current() -> AuthContext | None:
    return st.session_state.get(SESSION_KEY)


def generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def render_login() -> None:
    st.title(APP_NAME)
    if demo.is_demo():
        _render_demo_login()
        return
    if users_repo.user_count() == 0:
        _render_bootstrap()
        return
    st.caption("Sign in to continue")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            result = users_repo.authenticate(username, password)
            if result is None:
                st.error("Invalid credentials, inactive account, or the "
                         "account is temporarily locked after repeated "
                         "failures.")
            else:
                st.session_state[SESSION_KEY] = AuthContext(
                    principal=result.principal,
                    must_change_pw=result.must_change_pw)
                st.rerun()


def _render_demo_login() -> None:
    """One-click sign-in for the public demo. There is no password form, so
    visitors cannot lock the shared accounts out with failed attempts."""
    st.info("**Public demo** — every taxpayer, PAN, bank account and figure "
            "here is fictional. Changes are shared with other visitors and "
            "reset when the app restarts. Please don't upload real documents.")
    st.caption("Choose a role to explore:")
    for username, password, role, blurb in demo.DEMO_ACCOUNTS:
        col1, col2 = st.columns([1, 3])
        with col1:
            clicked = st.button(f"Enter as {role}", key=f"demo_{username}",
                                type="primary" if role == "Partner" else
                                "secondary", width="stretch")
        with col2:
            st.markdown(f"**{username}** — {blurb}")
        if clicked:
            result = users_repo.authenticate(username, password)
            if result is None:
                st.error("This demo account is unavailable — the app will "
                         "reset on its next restart.")
            else:
                st.session_state[SESSION_KEY] = AuthContext(
                    principal=result.principal, must_change_pw=False)
                st.rerun()


def _render_bootstrap() -> None:
    st.info("First run — create the Partner (administrator) account.")
    with st.form("bootstrap"):
        username = st.text_input("Partner username")
        pw1 = st.text_input("Password (min 8 chars)", type="password")
        pw2 = st.text_input("Confirm password", type="password")
        if st.form_submit_button("Create account", type="primary"):
            if pw1 != pw2:
                st.error("Passwords do not match.")
            else:
                try:
                    users_repo.create_user(None, username, pw1, "Partner")
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Partner account created — sign in below.")
                    st.rerun()


def render_password_change(ctx: AuthContext) -> None:
    st.title("Set a new password")
    st.info("Your password was issued by the firm — please choose your own "
            "before continuing.")
    with st.form("force_pw_change"):
        old = st.text_input("Current password", type="password")
        pw1 = st.text_input("New password (min 8 chars)", type="password")
        pw2 = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Change password", type="primary"):
            if pw1 != pw2:
                st.error("Passwords do not match.")
            else:
                try:
                    users_repo.change_password(ctx.principal, old, pw1)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    ctx.must_change_pw = False
                    st.session_state[SESSION_KEY] = ctx
                    st.success("Password updated.")
                    st.rerun()


def sign_out() -> None:
    for key in (SESSION_KEY, ACTIVE_TAXPAYER_KEY):
        st.session_state.pop(key, None)
    st.rerun()


def render_sidebar_chrome(ctx: AuthContext) -> None:
    with st.sidebar:
        st.caption(f"Signed in as **{ctx.principal.username}** "
                   f"({ctx.principal.role})")
        if ctx.is_staff:
            _render_taxpayer_switcher(ctx)
        if demo.is_demo():
            st.caption("🧪 Public demo · fictional data")
        else:
            _render_self_password_change(ctx)
        if st.button("Sign out", width="stretch"):
            sign_out()
        st.caption(f"{APP_NAME} · v{__version__} · AY 2026-27")


def _render_self_password_change(ctx: AuthContext) -> None:
    with st.expander("🔑 Change my password"):
        with st.form("self_pw_change", clear_on_submit=True):
            old = st.text_input("Current password", type="password")
            pw1 = st.text_input("New password (min 8 chars)",
                                type="password")
            pw2 = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("Change password"):
                if pw1 != pw2:
                    st.error("Passwords do not match.")
                else:
                    try:
                        users_repo.change_password(ctx.principal, old, pw1)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Password changed — use it from "
                                   "your next sign-in.")


def _render_taxpayer_switcher(ctx: AuthContext) -> None:
    summaries = taxpayers_repo.list_taxpayers(ctx.principal)
    if not summaries:
        st.caption("No clients yet — use Onboarding.")
        return
    options = {f"{s.full_name} ({s.pan_masked})": s.id for s in summaries}
    labels = list(options)
    current_id = st.session_state.get(ACTIVE_TAXPAYER_KEY)
    index = 0
    ids = list(options.values())
    if current_id in ids:
        index = ids.index(current_id)
    chosen = st.selectbox("Active client", labels, index=index)
    st.session_state[ACTIVE_TAXPAYER_KEY] = options[chosen]
