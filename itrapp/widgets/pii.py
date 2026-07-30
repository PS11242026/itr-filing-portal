"""PII display helpers — masked by default, reveal is audit-logged."""

import streamlit as st

from itrcore.authz import TaxpayerScope
from itrcore.db import auditlog


def masked_value(label: str, masked: str, full: str,
                 scope: TaxpayerScope, kind: str) -> None:
    """Render `masked`; an explicit reveal shows `full` and logs it."""
    col1, col2 = st.columns([3, 1])
    reveal_key = f"pii_reveal_{kind}_{scope.taxpayer_id}"
    revealed = st.session_state.get(reveal_key, False)
    with col1:
        # The key must CHANGE with the reveal state: a keyed widget keeps
        # its stored state and ignores a changed ``value`` param, so a
        # fixed key left the field masked forever after Reveal.
        st.text_input(label, value=full if revealed else masked,
                      disabled=True,
                      key=f"{reveal_key}_display_"
                          f"{'full' if revealed else 'masked'}")
    with col2:
        st.write("")
        if not revealed:
            if st.button("Reveal", key=f"{reveal_key}_btn"):
                auditlog.log(scope.principal.username, "PII_REVEAL",
                             entity="taxpayer", entity_id=scope.taxpayer_id,
                             taxpayer_id=scope.taxpayer_id,
                             details={"kind": kind})
                st.session_state[reveal_key] = True
                st.rerun()
        else:
            if st.button("Hide", key=f"{reveal_key}_btn_hide"):
                st.session_state[reveal_key] = False
                st.rerun()

