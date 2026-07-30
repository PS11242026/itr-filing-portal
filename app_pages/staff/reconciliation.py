"""26AS / AIS reconciliation with mandatory resolutions."""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import streamlit as st

from itrapp import state
from itrapp.widgets import assessee
from itrcore.db.repos import computations as comp_repo
from itrcore.db.repos import importers as importers_repo
from itrcore.db.repos import queries as queries_repo
from itrcore.export.format import inr
from itrcore.recon import service as recon_service
from itrcore.recon import source_recon

st.title("Reconciliation")
scope = state.require_active_scope(ctx)
ay = state.ay_picker()
assessee.header(scope, ay)

recon = recon_service.status(scope, ay)
if not recon["inputs_present"]:
    st.info("Save computation inputs first (Computation page) — "
            "reconciliation compares them against Form 26AS and AIS.")
    st.stop()

STATUS_ICON = {"matched": "✅", "mismatch": "🟠", "ais_only": "🔴",
               "claim_exceeds_26as": "⛔", "unclaimed_26as": "🟠"}

st.subheader("TDS vs Form 26AS")
if not recon["tds"]:
    st.caption("No TDS on either side (import 26AS on the Data import "
               "page if the client has TDS).")
for item, resolution in recon["tds"]:
    icon = "✅" if resolution else STATUS_ICON[item.status]
    with st.container(border=True):
        st.markdown(
            f"{icon} **{item.tan}** {item.deductor} — claimed "
            f"**{inr(item.claimed_inr)}** vs 26AS **{inr(item.as26_inr)}** "
            f"({item.status.replace('_', ' ')})"
            + (f" · resolved: {resolution['resolution']}"
               f" — {resolution['remark'] or ''}" if resolution else ""))
        if item.status != "matched" and not resolution:
            c1, c2, c3 = st.columns([2, 1, 1])
            remark = c1.text_input("Remark", key=f"tds_r_{item.key}")
            if c2.button("Justify", key=f"tds_j_{item.key}"):
                try:
                    recon_service.resolve(scope, ay, kind="tds",
                                          item_key=item.key,
                                          resolution="justified",
                                          remark=remark)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            if c3.button("Mark edited", key=f"tds_e_{item.key}",
                         help="Use after fixing the computation inputs"):
                recon_service.resolve(scope, ay, kind="tds",
                                      item_key=item.key,
                                      resolution="edited",
                                      remark=remark or "inputs updated")
                st.rerun()
        elif resolution and st.button("Reopen", key=f"tds_o_{item.key}"):
            recon_service.clear_resolution(scope, ay, "tds", item.key)
            st.rerun()

as26_rows = importers_repo.list_tds(scope, ay, source="26as")
if as26_rows:
    if st.button("⚡ Adopt ALL 26AS TDS rows into computation inputs"):
        inputs = comp_repo.load_inputs(scope, ay) or {}
        inputs["tds"] = [{
            "tan": r["tan"] or "", "deductor": r["deductor_name"] or "",
            "section": r["section"] or "",
            "amount": r["tds_paise"] // 100} for r in as26_rows]
        comp_repo.save_inputs(scope, ay, inputs)
        st.success("Computation inputs now claim exactly the 26AS TDS. "
                   "Recompute on the Computation page.")
        st.rerun()

st.subheader("AIS vs books")
if not recon["ais"]:
    st.caption("No AIS data imported yet.")
for item, resolution in recon["ais"]:
    icon = "✅" if resolution else STATUS_ICON[item.status]
    with st.container(border=True):
        st.markdown(
            f"{icon} **{item.label}** — AIS **{inr(item.ais_inr)}** vs "
            f"books **{inr(item.books_inr)}**"
            + (f" · resolved: {resolution['resolution']}"
               f" — {resolution['remark'] or ''}" if resolution else ""))
        if item.status != "matched" and not resolution:
            c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
            remark = c1.text_input("Remark", key=f"ais_r_{item.key}")
            applicable = item.category in ("interest_savings",
                                           "interest_deposits", "dividend")
            if applicable and c2.button("Accept AIS figure",
                                        key=f"ais_a_{item.key}"):
                inputs = comp_repo.load_inputs(scope, ay) or {}
                os_ = inputs.setdefault("other_sources", {})
                field = {"interest_savings": "savings_interest",
                         "interest_deposits": "fd_interest",
                         "dividend": "dividend"}[item.category]
                os_[field] = item.ais_inr
                comp_repo.save_inputs(scope, ay, inputs)
                recon_service.resolve(scope, ay, kind="ais",
                                      item_key=item.key,
                                      resolution="accepted",
                                      remark=f"books updated to AIS "
                                             f"{inr(item.ais_inr)}")
                st.rerun()
            if c3.button("Justify", key=f"ais_j_{item.key}"):
                try:
                    recon_service.resolve(scope, ay, kind="ais",
                                          item_key=item.key,
                                          resolution="justified",
                                          remark=remark)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            if c4.button("Mark edited", key=f"ais_e_{item.key}"):
                recon_service.resolve(scope, ay, kind="ais",
                                      item_key=item.key,
                                      resolution="edited",
                                      remark=remark or "inputs updated")
                st.rerun()
        elif resolution and st.button("Reopen", key=f"ais_o_{item.key}"):
            recon_service.clear_resolution(scope, ay, "ais", item.key)
            st.rerun()

st.subheader("AIS / TIS vs Form 26AS")
st.caption("Cross-checks the two ITD sources against each other and 26AS; "
           "raise any material difference as a client query.")
src_items = source_recon.compare(scope, ay)
if not src_items:
    st.caption("Import AIS / TIS / 26AS on the Data import page first.")
_SRC_ICON = {"matched": "✅", "mismatch": "🟠", "one_sided": "▫️"}
for item in src_items:
    with st.container(border=True):
        st.markdown(
            f"{_SRC_ICON[item.status]} **{item.label}** — AIS "
            f"**{inr(item.ais_inr)}** · TIS **{inr(item.tis_inr)}** · 26AS "
            f"**{inr(item.as26_inr)}**")
        if item.status == "mismatch" and st.button(
                "Raise as client query", key=f"srcq_{item.key}"):
            queries_repo.raise_query(scope, ay=ay,
                                     question=item.query_text(),
                                     topic="source_recon")
            st.success("Query raised — see the Client queries page.")

st.divider()
blockers = recon_service.blockers(scope, ay)
if blockers:
    st.error("**Unresolved reconciliation items** (these block filing):")
    for b in blockers:
        st.markdown(f"- {b}")
else:
    st.success("Reconciliation clean — every difference matched, "
               "justified or corrected.")
