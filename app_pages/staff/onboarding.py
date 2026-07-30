"""Onboard a new client: taxpayer record + optional client login."""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import streamlit as st

from itrapp import auth
from itrapp.widgets import income_profile
from itrcore.db.repos import taxpayers as taxpayers_repo
from itrcore.db.repos import users as users_repo
from itrcore.validation import pan_holder_type, validate_pan

st.title("Onboarding")
st.caption("Create the taxpayer record; the income profile drives the "
           "required-documents checklist.")

with st.form("onboard"):
    full_name = st.text_input("Full name *")
    pan = st.text_input("PAN *", max_chars=10,
                        help="AAAPL1234C — 4th character P for individuals")
    aadhaar = st.text_input("Aadhaar (optional)", max_chars=14)
    dob = st.text_input("Date of birth (YYYY-MM-DD)")
    col1, col2 = st.columns(2)
    with col1:
        email = st.text_input("Email")
    with col2:
        mobile = st.text_input("Mobile")
    address = st.text_area("Address")
    resident = st.selectbox("Residential status",
                            ("Resident", "RNOR", "Non-Resident"))

    st.markdown("**Income profile — heads & subheads**")
    st.caption("Click an overhead (e.g. *Profits & gains of business or "
               "profession*) to reveal the subheads clubbed under it. The "
               "selections drive the document checklist and highlight the "
               "relevant computation tabs.")
    profile = income_profile.render(
        {"has_salary": True, "wants_old_regime": True},
        key_prefix="onboard_")

    st.markdown("**Client portal login** (optional)")
    make_login = st.checkbox("Create a client login for uploads & queries",
                             value=True)
    username = st.text_input("Client username (defaults to PAN)")

    submitted = st.form_submit_button("Create client", type="primary")

if submitted:
    try:
        canonical_pan = validate_pan(pan)
        if pan_holder_type(canonical_pan) != "Individual":
            st.warning("PAN 4th character is not 'P' — this portal computes "
                       "individual returns only. Proceeding anyway.")
        tid = taxpayers_repo.create_taxpayer(
            ctx.principal, full_name=full_name, pan=canonical_pan,
            aadhaar=aadhaar or None, dob=dob or None, email=email or None,
            mobile=mobile or None, address=address or None,
            resident_status=resident, profile=profile)
    except ValueError as exc:
        st.error(str(exc))
    else:
        st.success(f"Client created (id {tid}).")
        st.session_state[auth.ACTIVE_TAXPAYER_KEY] = tid
        if make_login:
            login_name = (username or canonical_pan).strip()
            one_time = auth.generate_password()
            try:
                users_repo.create_user(ctx.principal, login_name, one_time,
                                       "Client", taxpayer_id=tid,
                                       must_change_pw=True)
            except (ValueError, PermissionError) as exc:
                st.error(f"Login not created: {exc}")
            else:
                st.info(
                    f"Client login **{login_name}** created.\n\n"
                    f"One-time password (share securely, they must change "
                    f"it on first sign-in):\n\n### `{one_time}`")
