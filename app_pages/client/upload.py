"""Client document upload against the checklist."""

from itrapp import guard

ctx = guard.page(roles={"client"})

import streamlit as st

from itrcore.constants import CURRENT_AY
from itrcore.db.repos import checklist as checklist_repo
from itrcore.db.repos import documents as documents_repo

scope = ctx.own_scope()

st.title("Upload documents")
st.caption("PDF, CSV, Excel or images. Everything you upload is stored "
           "encrypted and visible only to you and the firm.")

states = checklist_repo.status(scope, CURRENT_AY)
pending = [s for s in states if s.status == "pending"]
labels = {s.item.label: s for s in states}

choice = st.selectbox(
    "What are you uploading?",
    [s.item.label for s in pending] +
    [s.item.label for s in states if s.status != "pending"] +
    ["Something else"])

if choice == "Something else":
    doc_type = "other"
    st.text_input("Briefly describe the document", key="other_desc")
else:
    item = labels[choice].item
    doc_type = item.doc_types[0] if item.doc_types else "other"
    if item.help:
        st.info(item.help)

files = st.file_uploader("Choose file(s)", accept_multiple_files=True)
note = st.text_input("Note for your preparer (optional)")
if st.button("Upload", type="primary", disabled=not files):
    ok = 0
    for f in files:
        try:
            documents_repo.save_document(
                scope, ay=CURRENT_AY, doc_type=doc_type, filename=f.name,
                data=f.getvalue(),
                note=(note or st.session_state.get("other_desc") or None))
            ok += 1
        except ValueError as exc:
            st.error(f"{f.name}: {exc}")
    if ok:
        st.success(f"Uploaded {ok} file(s). Thank you!")
        st.rerun()

st.subheader("Already uploaded")
docs = documents_repo.list_documents(scope, ay=CURRENT_AY)
if not docs:
    st.caption("Nothing yet.")
for meta in docs:
    st.markdown(f"📄 **{meta.orig_name}** · {meta.size_bytes:,} bytes · "
                f"{meta.uploaded_at}")
