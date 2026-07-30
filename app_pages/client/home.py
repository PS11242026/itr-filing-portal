"""Client home: progress overview."""

from itrapp import guard

ctx = guard.page(roles={"client"})

import streamlit as st

from itrcore.constants import CURRENT_AY
from itrcore.db.repos import checklist as checklist_repo
from itrcore.db.repos import queries as queries_repo
from itrcore.db.repos import taxpayers as taxpayers_repo

scope = ctx.own_scope()
tp = taxpayers_repo.get_taxpayer(scope)

st.title(f"Welcome, {tp.full_name}")
st.caption(f"Assessment year {CURRENT_AY} (income earned April 2025 – "
           "March 2026)")

states = checklist_repo.status(scope, CURRENT_AY)
required = [s for s in states if s.item.required]
done = [s for s in required if s.status in ("satisfied", "waived")]
open_q = queries_repo.open_count(scope, CURRENT_AY)

col1, col2 = st.columns(2)
col1.metric("Documents checklist", f"{len(done)} / {len(required)}")
col2.metric("Questions awaiting you", open_q)

if len(done) < len(required):
    st.warning("Some documents are still pending — please visit "
               "**Upload documents**.")
elif open_q:
    st.warning("Your tax preparer has questions for you — please visit "
               "**My queries**.")
else:
    st.success("Nothing pending from your side right now. Your preparer "
               "will contact you if anything else is needed.")

st.subheader("Your checklist")
for s in states:
    icon = {"satisfied": "✅", "waived": "⚪", "pending": "🔴"}[s.status]
    opt = "" if s.item.required else " *(optional)*"
    st.markdown(f"{icon} {s.item.label}{opt}")
    if s.item.help and s.status == "pending":
        st.caption("   " + s.item.help)
