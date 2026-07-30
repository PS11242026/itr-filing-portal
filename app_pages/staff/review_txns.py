"""Transaction review: run analysis, confirm/override, raise queries."""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import datetime as dt

import pandas as pd
import streamlit as st

from itrapp import state
from itrapp.widgets import assessee
from itrcore.analysis import classify, gate
from itrcore.constants import fy_for_ay
from itrcore.db.repos import importers as importers_repo
from itrcore.db.repos import queries as queries_repo
from itrcore.db.repos import transactions as txn_repo
from itrcore.export.format import inr

st.title("Review transactions")
scope = state.require_active_scope(ctx)
ay = state.ay_picker()
assessee.header(scope, ay)

counts = txn_repo.open_counts(scope, ay)
if counts["total"] == 0:
    st.info("No transactions imported yet — commit bank statements on the "
            "**Data import** page first.")
    st.stop()

report = gate.readiness(scope, ay)
c1, c2 = st.columns([3, 1])
with c1:
    if report.ok:
        st.success("Everything classified and answered — ready for "
                   "computation & filing.")
    else:
        with st.expander(f"⛔ {len(report.blockers)} blocker(s) before "
                         "finalization", expanded=False):
            for b in report.blockers:
                st.markdown(f"- {b}")
with c2:
    if st.button("🔍 Run analysis", type="primary", width="stretch"):
        rows = txn_repo.rows_for_analysis(scope, ay)
        employers = [f["employer_name"]
                     for f in importers_repo.list_form16(scope, ay)]
        fy_end = dt.date.fromisoformat(fy_for_ay(ay)[1])
        outcome = classify.analyze(rows, employer_names=employers,
                                   fy_end=fy_end)
        stats = txn_repo.apply_analysis(scope, ay, outcome)
        st.success(f"Analysed {stats['total']} · auto-categorised "
                   f"{stats['auto_categorized']} · flagged "
                   f"{stats['flagged']} · queries raised "
                   f"{stats['queries_raised']}")
        st.rerun()

st.subheader("Category summary")
summary = txn_repo.summary_by_label(scope, ay)
if summary:
    st.dataframe(pd.DataFrame([{
        "category": s["label"], "count": s["n"],
        "amount": inr((s["total"] or 0) // 100),
        "awaiting confirm": s["pending"]} for s in summary]),
        hide_index=True, width="stretch")
    cc1, cc2 = st.columns(2)
    with cc1:
        if st.button("✅ Accept ALL auto-suggestions as final"):
            n = txn_repo.confirm_auto(scope, ay)
            st.success(f"Confirmed {n} transactions.")
            st.rerun()
    with cc2:
        cat_options = sorted({s["label"] for s in summary
                              if s["label"] not in ("unclassified",)})
        pick = st.selectbox("…or accept one category", ["—"] + cat_options)
        if pick != "—" and st.button("Accept category"):
            n = txn_repo.confirm_auto(scope, ay, category=pick)
            st.success(f"Confirmed {n}.")
            st.rerun()

st.subheader("Transactions")
show = st.radio("Show", ["needs attention", "all"], horizontal=True)
rows = txn_repo.list_txns(scope, ay, only_open=(show == "needs attention"))
if not rows:
    st.caption("Nothing to show.")
else:
    df = pd.DataFrame([{
        "id": r["id"], "date": r["txn_date"],
        "account": f"{r['bank_name']} ··{r['last4']}",
        "narration": r["narration"][:70],
        "amount": r["amount_paise"] / 100,
        "suggested": r["auto_category"] or "",
        "final": r["terminal_label"] or "",
        "flag": r["flag_reason"] or "", "status": r["status"],
        "pick": False} for r in rows])
    edited = st.data_editor(
        df, hide_index=True, width="stretch", height=420,
        disabled=[c for c in df.columns if c != "pick"],
        column_config={"pick": st.column_config.CheckboxColumn("select")})
    picked = [int(r["id"]) for _, r in edited.iterrows() if r["pick"]]
    b1, b2, b3 = st.columns([2, 1, 2])
    with b1:
        label = st.selectbox("Final category for selected",
                             txn_repo.TERMINAL_LABELS)
    with b2:
        st.write("")
        if st.button("Apply", disabled=not picked):
            n = txn_repo.set_terminal(scope, picked, label)
            st.success(f"Classified {n} transaction(s).")
            st.rerun()
    with b3:
        question = st.text_input("…or ask the client about the selected "
                                 "transaction", key="manual_q")
        if st.button("Raise query", disabled=not (picked and question)):
            for tid in picked:
                queries_repo.raise_query(scope, ay=ay, question=question,
                                         transaction_id=tid)
            st.success("Query sent to the client's portal.")
            st.rerun()
