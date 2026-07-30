"""Data-editor ⇄ records plumbing shared by the Computation page.

``seed`` builds the DataFrame a ``st.data_editor`` starts from; ``records``
turns the edited DataFrame back into the plain dicts the computation
stores. Pure pandas — no Streamlit — so the NaT/NaN edge cases stay unit-
testable: a date column's empty cells become pandas ``NaT``, which is an
``isinstance(datetime)`` whose ``isoformat()`` is the literal string
'NaT' — it must collapse to the column default (None), never reach serde.
"""

import datetime as dt

import pandas as pd


def records(df: pd.DataFrame, defaults: dict) -> list[dict]:
    out = []
    for _, row in df.iterrows():
        rec = {}
        empty = True
        for col, default in defaults.items():
            v = row.get(col)
            # pd.isna covers None, float NaN AND pandas NaT (which would
            # otherwise isoformat() to the string 'NaT').
            if v is None or (not isinstance(v, (list, tuple, dict))
                             and pd.isna(v)) or v == "":
                rec[col] = default
                continue
            if isinstance(default, int) and not isinstance(v, bool):
                try:
                    rec[col] = int(v)
                except (TypeError, ValueError):
                    rec[col] = default
            elif isinstance(v, (dt.date, dt.datetime)):
                rec[col] = (v.date() if isinstance(v, dt.datetime)
                            else v).isoformat()
            else:
                rec[col] = v
            if rec[col] not in (default, None):
                empty = False
        if not empty:
            out.append(rec)
    return out


def seed(rows: list[dict], columns: dict) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype="object")
                             for c in columns})
    df = pd.DataFrame(rows)
    for c in columns:
        if c not in df.columns:
            df[c] = None
    for c, kind in columns.items():
        if kind == "date":
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    return df[list(columns)]
