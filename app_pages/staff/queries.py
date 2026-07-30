"""Raise and resolve explanation queries for the active client."""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import streamlit as st

from itrapp import state
from itrapp.widgets import assessee
from itrcore.db.repos import queries as queries_repo

st.title("Client queries")
scope = state.require_active_scope(ctx)
ay = state.ay_picker()
assessee.header(scope, ay)

with st.expander("Raise a new query"):
    with st.form("raise_query", clear_on_submit=True):
        question = st.text_area(
            "Question to the client",
            placeholder="e.g. Please explain the ₹2,50,000 credit on "
                        "14-08-2025 from 'RAKESH VERMA'. Was this a gift, "
                        "loan or sale proceeds?")
        if st.form_submit_button("Send to client") and question.strip():
            queries_repo.raise_query(scope, ay=ay, question=question,
                                     topic="manual")
            st.success("Query raised — the client will see it on their "
                       "portal.")
            st.rerun()

rows = queries_repo.list_queries(scope, ay=ay)
if not rows:
    st.caption("No queries for this year.")
for row in rows:
    badge = {"open": "🟠 awaiting client", "answered": "🔵 answered — "
             "review", "resolved": "✅ resolved",
             "withdrawn": "⚪ withdrawn"}[row["status"]]
    with st.container(border=True):
        st.markdown(f"**Q{row['id']}** · {badge}  \n{row['question']}")
        if row["client_response"]:
            st.markdown(f"> **Client:** {row['client_response']}")
        if row["status"] in ("open", "answered"):
            col1, col2 = st.columns([3, 2])
            with col1:
                disposition = st.text_input(
                    "Disposition / how it was classified",
                    key=f"disp_{row['id']}")
            with col2:
                st.write("")
                if st.button("Resolve", key=f"res_{row['id']}"):
                    queries_repo.resolve(scope, row["id"],
                                         disposition or "resolved")
                    st.rerun()
                if st.button("Withdraw", key=f"wd_{row['id']}"):
                    queries_repo.resolve(scope, row["id"], "withdrawn",
                                         withdraw=True)
                    st.rerun()
