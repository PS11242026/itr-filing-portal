"""Read-only view of the client's computation and filing status."""

from itrapp import guard

ctx = guard.page(roles={"client"})

import streamlit as st

from itrcore.constants import CURRENT_AY
from itrcore.db import connection

scope = ctx.own_scope()

st.title("My return")

comps = connection.query(
    "SELECT version_no, status, created_at FROM computations"
    " WHERE taxpayer_id=? AND ay=? ORDER BY version_no DESC",
    (scope.taxpayer_id, CURRENT_AY))
filings = connection.query(
    "SELECT form, filing_type, status, ack_no, filed_on, everified_on"
    " FROM filings WHERE taxpayer_id=? AND ay=? ORDER BY id DESC",
    (scope.taxpayer_id, CURRENT_AY))

if not comps and not filings:
    st.info("Your computation isn't ready yet. Once your preparer has "
            "processed your documents, the summary and filing status will "
            "appear here.")
    st.stop()

if comps:
    st.subheader("Computation")
    latest = comps[0]
    st.markdown(f"Version {latest['version_no']} — status "
                f"**{latest['status']}** ({latest['created_at']})")
    st.caption("Ask your preparer for the detailed computation sheet.")

if filings:
    st.subheader("Filing status")
    for f in filings:
        line = (f"**{f['form']}** ({f['filing_type']}) — {f['status']}")
        if f["ack_no"]:
            line += f" · Acknowledgement **{f['ack_no']}**"
        if f["everified_on"]:
            line += f" · e-verified {f['everified_on']}"
        st.markdown(line)
