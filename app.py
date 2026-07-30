"""ITR Filing Portal — entrypoint.

Runs before every page on every rerun: migrations (once per process),
authentication gate, forced password change, then role-filtered navigation.
"""

import streamlit as st

from itrcore.version import APP_NAME
from itrapp import auth, nav

st.set_page_config(page_title=APP_NAME, page_icon="🧾", layout="wide")


@st.cache_resource
def _boot() -> bool:
    from itrcore.db import migrate
    migrate.upgrade()
    return True


_boot()

ctx = auth.current()
if ctx is None:
    auth.render_login()
    st.stop()

if ctx.must_change_pw:
    auth.render_password_change(ctx)
    st.stop()

pg = st.navigation(nav.build(ctx))
auth.render_sidebar_chrome(ctx)
pg.run()
