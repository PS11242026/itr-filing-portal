"""Cross-page state helpers (active client selection for staff, AY pick)."""

import streamlit as st

from itrcore.authz import TaxpayerScope
from itrcore.constants import AY_CHOICES, CURRENT_AY
from itrapp import auth


def active_scope(ctx: auth.AuthContext) -> TaxpayerScope | None:
    """The taxpayer the page should operate on.

    Clients always get their own scope. Staff get the sidebar-selected
    client, or None (caller shows a hint) when nothing is selected yet.
    """
    if not ctx.is_staff:
        return ctx.own_scope()
    tid = st.session_state.get(auth.ACTIVE_TAXPAYER_KEY)
    if tid is None:
        return None
    return ctx.scope_for(int(tid))


def require_active_scope(ctx: auth.AuthContext) -> TaxpayerScope:
    scope = active_scope(ctx)
    if scope is None:
        st.info("Select an **Active client** in the sidebar first "
                "(or onboard one).")
        st.stop()
    return scope


def ay_picker(key: str = "ay_pick") -> str:
    """Assessment-year selector, defaulting to the current filing season."""
    index = AY_CHOICES.index(CURRENT_AY) if CURRENT_AY in AY_CHOICES else 0
    return st.selectbox("Assessment year", AY_CHOICES, index=index, key=key)
