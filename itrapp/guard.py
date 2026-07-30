"""Page-level guard. Every file in app_pages/ must call guard.page(...) as
its first statement (enforced by a lint test). This is UX-layer defence;
the real boundary is itrcore.authz.mint_scope.
"""

import streamlit as st

from itrapp import auth


def page(roles: set[str]) -> auth.AuthContext:
    """roles: {"staff"}, {"client"}, or explicit roles like {"Partner"}."""
    ctx = auth.current()
    if ctx is None:
        st.error("Please sign in.")
        st.stop()
    group = "staff" if ctx.is_staff else "client"
    if group in roles or ctx.principal.role in roles:
        return ctx
    st.error("You don't have access to this page.")
    st.stop()
    raise RuntimeError("unreachable")
