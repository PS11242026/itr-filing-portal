"""Client answers the preparer's questions, with optional attachments."""

from itrapp import guard

ctx = guard.page(roles={"client"})

import streamlit as st

from itrcore.constants import CURRENT_AY
from itrcore.db.repos import documents as documents_repo
from itrcore.db.repos import queries as queries_repo

scope = ctx.own_scope()

st.title("My queries")
st.caption("Your tax preparer needs these clarifications to finish your "
           "return.")

rows = queries_repo.list_queries(scope, ay=CURRENT_AY)
open_rows = [r for r in rows if r["status"] == "open"]
other_rows = [r for r in rows if r["status"] != "open"]

if not rows:
    st.success("No questions for you right now.")

for row in open_rows:
    with st.container(border=True):
        st.markdown(f"**{row['question']}**")
        response = st.text_area("Your answer", key=f"resp_{row['id']}")
        attachment = st.file_uploader(
            "Attach a supporting document (optional)",
            key=f"file_{row['id']}")
        if st.button("Submit answer", key=f"send_{row['id']}",
                     type="primary"):
            if not (response or "").strip():
                st.error("Please write an answer first.")
            else:
                doc_id = None
                if attachment is not None:
                    doc_id = documents_repo.save_document(
                        scope, ay=CURRENT_AY, doc_type="query_response",
                        filename=attachment.name,
                        data=attachment.getvalue(),
                        note=f"attachment for query {row['id']}")
                queries_repo.answer(scope, row["id"], response, doc_id)
                st.success("Answer sent.")
                st.rerun()

if other_rows:
    st.subheader("Answered / closed")
    for row in other_rows:
        icon = {"answered": "🔵", "resolved": "✅", "withdrawn": "⚪"}
        with st.container(border=True):
            st.markdown(f"{icon[row['status']]} {row['question']}")
            if row["client_response"]:
                st.markdown(f"> Your answer: {row['client_response']}")
