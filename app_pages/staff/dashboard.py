"""Staff dashboard: firm workload at a glance."""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import pandas as pd
import streamlit as st

from itrapp import state
from itrapp.widgets import assessee
from itrcore.db import auditlog, connection

st.title("Dashboard")
_scope = state.active_scope(ctx)
if _scope is not None:
    assessee.header(_scope)


def _count(sql: str) -> int:
    row = connection.query_one(sql)
    return int(row["c"]) if row else 0


n_clients = _count("SELECT COUNT(*) AS c FROM taxpayers WHERE status='active'")
if n_clients == 0:
    st.info("No clients yet — onboard your first one to get started.",
            icon=":material/rocket_launch:")

# ---------------------------------------------------------------- workload
kpis = [
    (":material/groups:", "Active clients", n_clients),
    (":material/help:", "Open queries",
     _count("SELECT COUNT(*) AS c FROM explanation_queries "
            "WHERE status IN ('open','answered')")),
    (":material/receipt_long:", "Unresolved transactions",
     _count("SELECT COUNT(*) AS c FROM transactions "
            "WHERE terminal_label IS NULL")),
    (":material/description:", "Returns generated",
     _count("SELECT COUNT(*) AS c FROM filings")),
]
for col, (icon, label, value) in zip(st.columns(len(kpis)), kpis):
    with col, st.container(border=True):
        st.markdown(f"{icon} &nbsp;{label}")
        st.markdown(f"## {value:,}")

# ---------------------------------------------------------- returns pipeline
pipeline = {r["status"]: int(r["c"]) for r in connection.query(
    "SELECT status, COUNT(*) AS c FROM filings GROUP BY status")}
with st.container(border=True):
    st.markdown(":material/timeline: &nbsp;**Returns pipeline**")
    stages = [("generated", "Generated"), ("uploaded", "Uploaded"),
              ("everified", "e-Verified"), ("processed", "Processed")]
    for pc, (key, label) in zip(st.columns(len(stages)), stages):
        pc.metric(label, pipeline.get(key, 0))

# ------------------------------------------------------------ recent activity
st.subheader("Recent activity")
rows = auditlog.recent(limit=30)
if rows:
    df = pd.DataFrame([dict(r) for r in rows])[
        ["at", "username", "action", "entity", "entity_id"]].rename(
        columns={"at": "When", "username": "User", "action": "Action",
                 "entity": "Entity", "entity_id": "Ref"})
    st.dataframe(df, hide_index=True, width="stretch")
else:
    st.caption("No activity yet.")
