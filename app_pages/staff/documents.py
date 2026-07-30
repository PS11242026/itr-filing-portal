"""Documents & checklist for the active client."""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import streamlit as st

from itrapp import state
from itrapp.widgets import assessee
from itrcore.db.repos import checklist as checklist_repo
from itrcore.db.repos import documents as documents_repo

st.title("Documents & checklist")
scope = state.require_active_scope(ctx)
ay = state.ay_picker()
assessee.header(scope, ay)

st.subheader("Checklist")
states = checklist_repo.status(scope, ay)
for s in states:
    icon = {"satisfied": "✅", "waived": "⚪", "pending": "🔴"}[s.status]
    req = "" if s.item.required else " *(optional)*"
    cols = st.columns([6, 1, 2])
    cols[0].markdown(f"{icon} **{s.item.label}**{req}"
                     + (f" — _{s.note}_" if s.note else ""))
    cols[1].caption(f"{s.docs_count} file(s)")
    with cols[2]:
        if s.status == "pending" and s.item.required:
            if st.button("Waive", key=f"waive_{s.item.key}"):
                checklist_repo.waive(scope, ay, s.item.key,
                                     "waived by staff")
                st.rerun()
        elif s.status == "waived":
            if st.button("Un-waive", key=f"unwaive_{s.item.key}"):
                checklist_repo.clear_override(scope, ay, s.item.key)
                st.rerun()

st.divider()
st.subheader("Upload on behalf of the client")
doc_types = sorted({dt for s in states for dt in s.item.doc_types} | {"other"})
with st.form("staff_upload", clear_on_submit=True):
    doc_type = st.selectbox("Document type", doc_types)
    note = st.text_input("Note (optional)")
    files = st.file_uploader("Files", accept_multiple_files=True)
    if st.form_submit_button("Upload") and files:
        for f in files:
            documents_repo.save_document(scope, ay=ay, doc_type=doc_type,
                                         filename=f.name,
                                         data=f.getvalue(), note=note or None)
        st.success(f"Uploaded {len(files)} file(s).")
        st.rerun()

st.subheader("Files on record")
docs = documents_repo.list_documents(scope, ay=ay)
if not docs:
    st.caption("Nothing uploaded yet for this year.")
for meta in docs:
    cols = st.columns([4, 2, 2, 2])
    cols[0].markdown(f"**{meta.orig_name}**  \n`{meta.doc_type}` · "
                     f"{meta.size_bytes:,} bytes")
    cols[1].caption(f"by {meta.uploaded_by}\n{meta.uploaded_at}")
    with cols[2]:
        if st.button("Download", key=f"dl_{meta.id}"):
            _, data = documents_repo.load_document(scope, meta.id)
            st.download_button("Save file", data=data,
                               file_name=meta.orig_name,
                               key=f"save_{meta.id}")
    with cols[3]:
        if st.button("Void", key=f"void_{meta.id}"):
            documents_repo.void_document(scope, meta.id, "voided by staff")
            st.rerun()
