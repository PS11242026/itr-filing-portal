"""Assessee (client) context header.

A compact strip shown at the top of staff pages so the active taxpayer, PAN
and assessment year are always in view — the way desktop tax software keeps
the assessee pinned. Displays the masked PAN only (never triggers a reveal).
"""

import datetime as dt

import streamlit as st

from itrcore.authz import TaxpayerScope
from itrcore.db.repos import taxpayers as taxpayers_repo
from itrcore.models import Taxpayer


def _age(dob: str | None) -> str:
    if not dob:
        return "—"
    try:
        born = dt.date.fromisoformat(dob)
    except ValueError:
        return "—"
    today = dt.date.today()
    years = today.year - born.year - (
        (today.month, today.day) < (born.month, born.day))
    return str(years)


def header(scope: TaxpayerScope, ay: str | None = None, *,
           tp: Taxpayer | None = None) -> None:
    """Render the assessee context strip. Pass ``tp`` to reuse an already
    loaded record and avoid a second decrypt."""
    tp = tp or taxpayers_repo.get_taxpayer(scope)
    with st.container(border=True):
        cols = st.columns([3, 2, 2, 2])
        cols[0].markdown(
            f":material/account_circle: **{tp.full_name}**  \n"
            f"PAN `{tp.pan_masked}`")
        cols[1].markdown(
            f"Residential status  \n**{tp.resident_status or '—'}**")
        cols[2].markdown(
            f"DOB · age  \n**{tp.dob or '—'} · {_age(tp.dob)}**")
        if ay:
            cols[3].markdown(f"Assessment year  \n**{ay}**")
        else:
            cols[3].markdown(
                f"Record  \n**{(tp.status or 'active').capitalize()}**")
