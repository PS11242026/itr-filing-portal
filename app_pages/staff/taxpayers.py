"""Client (taxpayer) list, full detail view, and post-onboarding editing."""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import streamlit as st

from itrapp import auth
from itrapp.widgets import income_profile, pii
from itrcore.authz import has_role
from itrcore.db.repos import taxpayers as taxpayers_repo
from itrcore.db.repos import users as users_repo

st.title("Clients")

flash = st.session_state.pop("clients_flash", None)
if flash:
    st.success(flash)

show_archived = st.toggle("Show deactivated clients", value=False,
                          key="clients_show_archived")
summaries = taxpayers_repo.list_taxpayers(ctx.principal,
                                          include_archived=show_archived)
if not summaries:
    st.info("No clients yet — onboard the first one from the "
            "**Onboarding** page." if not show_archived
            else "No clients (active or deactivated) on file.")
    st.stop()

labels = {}
for s in summaries:
    suffix = " · DEACTIVATED" if s.status != "active" else ""
    labels[f"{s.full_name} ({s.pan_masked}){suffix}"] = s.id

# Default to the client already active in the sidebar so clicking the
# Clients tab lands on the client you are working on.
ids = list(labels.values())
active_id = st.session_state.get(auth.ACTIVE_TAXPAYER_KEY)
chosen = st.selectbox("Open client", list(labels),
                      index=ids.index(active_id) if active_id in ids else 0)
tid = labels[chosen]
if tid != active_id and st.button("Set as active client (sidebar)"):
    st.session_state[auth.ACTIVE_TAXPAYER_KEY] = tid
    st.rerun()

scope = ctx.scope_for(tid)
tp = taxpayers_repo.get_taxpayer(scope)
is_manager = has_role(ctx.principal, "Manager")
is_partner = has_role(ctx.principal, "Partner")

# ------------------------------------------------------------ client card --
st.subheader(tp.full_name)
if tp.status != "active":
    st.warning("This client is **deactivated** — hidden from the sidebar "
               "and client lists. Data is retained; reactivate below.")

pii.masked_value("PAN", tp.pan_masked, tp.pan, scope, "pan")

# Aadhaar: presence is shown without decrypting; an explicit reveal decrypts
# once (audit-logged) and keeps the value only in this session.
aadhaar_on_file = taxpayers_repo.has_aadhaar(scope)
aadhaar_key = f"aadhaar_plain_{tid}"
acol1, acol2 = st.columns([3, 1])
with acol1:
    if st.session_state.get(aadhaar_key):
        st.text_input("Aadhaar", st.session_state[aadhaar_key],
                      disabled=True, key=f"{aadhaar_key}_display")
    else:
        st.text_input("Aadhaar",
                      "•••• •••• ••••" if aadhaar_on_file else "not on file",
                      disabled=True, key=f"{aadhaar_key}_masked")
with acol2:
    st.write("")
    if st.session_state.get(aadhaar_key):
        if st.button("Hide", key=f"{aadhaar_key}_hide"):
            st.session_state.pop(aadhaar_key, None)
            st.rerun()
    elif aadhaar_on_file:
        if st.button("Reveal", key=f"{aadhaar_key}_btn"):
            st.session_state[aadhaar_key] = taxpayers_repo.get_aadhaar(scope)
            st.rerun()

info1, info2, info3 = st.columns(3)
info1.markdown(f"Date of birth  \n**{tp.dob or '—'}**")
info1.markdown(f"Residential status  \n**{tp.resident_status or '—'}**")
info2.markdown(f"Email  \n**{tp.email or '—'}**")
info2.markdown(f"Mobile  \n**{tp.mobile or '—'}**")
info3.markdown(f"Address  \n**{tp.address or '—'}**")
info3.markdown(f"Record status  \n**{(tp.status or 'active').title()}**")

# Client portal login status (created at onboarding, optional).
login = users_repo.find_client_login(ctx.principal, tid)
if login is None:
    st.caption("Client portal login: none")
else:
    state = "active" if login["active"] else "disabled"
    lcol1, lcol2 = st.columns([3, 1])
    lcol1.caption(f"Client portal login: **{login['username']}** · {state}")
    if is_partner:
        with lcol2:
            if login["active"]:
                if st.button("Disable login", key=f"login_off_{tid}"):
                    try:
                        users_repo.set_active(ctx.principal, login["id"],
                                              False)
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["clients_flash"] = (
                            f"Login {login['username']} disabled.")
                        st.rerun()
            else:
                if st.button("Enable login", key=f"login_on_{tid}"):
                    try:
                        users_repo.set_active(ctx.principal, login["id"],
                                              True)
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["clients_flash"] = (
                            f"Login {login['username']} enabled.")
                        st.rerun()

# --------------------------------------------------------------- editing --
with st.expander("Edit contact details"):
    with st.form(f"edit_contact_{tid}"):
        full_name = st.text_input("Full name", tp.full_name)
        dob = st.text_input("Date of birth (YYYY-MM-DD)", tp.dob or "")
        email = st.text_input("Email", tp.email or "")
        mobile = st.text_input("Mobile", tp.mobile or "")
        address = st.text_area("Address", tp.address or "")
        _STATUSES = ("Resident", "RNOR", "Non-Resident")
        resident = st.selectbox(
            "Residential status", _STATUSES,
            index=_STATUSES.index(tp.resident_status)
            if tp.resident_status in _STATUSES else 0)
        if st.form_submit_button("Save"):
            try:
                taxpayers_repo.update_contact(
                    scope, full_name=full_name, dob=dob or None,
                    email=email or None, mobile=mobile or None,
                    address=address or None, resident_status=resident)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["clients_flash"] = "Contact details saved."
                st.rerun()

if is_manager:
    with st.expander("Edit PAN / Aadhaar (identity)"):
        st.caption("For correcting onboarding typos. Values are re-encrypted "
                   "and the search index is rebuilt; the change is "
                   "audit-logged (masked).")
        with st.form(f"edit_identity_{tid}"):
            new_pan = st.text_input(
                "PAN", value="", max_chars=10, key=f"identity_pan_{tid}",
                placeholder=f"currently {tp.pan_masked} — type the full "
                            "PAN to change it")
            new_aadhaar = st.text_input(
                "Aadhaar", value="", max_chars=14,
                key=f"identity_aadhaar_{tid}",
                placeholder="on file — type to replace" if aadhaar_on_file
                else "not on file — type to add")
            remove_aadhaar = st.checkbox("Remove Aadhaar from the record")
            if st.form_submit_button("Update identity"):
                pan_arg = new_pan.strip().upper() or None
                if pan_arg == tp.pan:
                    pan_arg = None                    # unchanged
                aadhaar_arg = new_aadhaar.strip() or None
                if not (pan_arg or aadhaar_arg or remove_aadhaar):
                    st.info("Nothing to update.")
                else:
                    try:
                        taxpayers_repo.update_identity(
                            scope, pan=pan_arg, aadhaar=aadhaar_arg,
                            clear_aadhaar=remove_aadhaar)
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop(aadhaar_key, None)
                        st.session_state["clients_flash"] = (
                            "Identity updated.")
                        st.rerun()

st.markdown("#### Income profile — heads & subheads")
st.caption("Click an overhead to reveal the subheads of income clubbed "
           "under it (e.g. **Profits & gains of business or profession** "
           "→ 44AD / 44ADA / 44AE / regular books). Selections drive the "
           "document checklist and the computation-tab highlights.")
with st.form(f"edit_profile_{tid}"):
    new_profile = income_profile.render(dict(tp.profile),
                                        key_prefix=f"prof_{tid}_")
    if st.form_submit_button("Save profile"):
        taxpayers_repo.update_profile(scope, new_profile)
        st.session_state["clients_flash"] = ("Income profile saved — "
                                             "checklist updated.")
        st.rerun()

# ---------------------------------------------------------- status change --
if is_manager:
    st.divider()
    if tp.status == "active":
        with st.expander("Deactivate this client"):
            st.caption("Deactivating hides the client from the sidebar and "
                       "lists. Nothing is deleted — computations, documents "
                       "and history are retained, and the client can be "
                       "reactivated here any time (turn on *Show "
                       "deactivated clients* above to find them).")
            sure = st.checkbox(f"Yes, deactivate **{tp.full_name}**",
                               key=f"deact_sure_{tid}")
            if st.button("Deactivate client", type="primary",
                         disabled=not sure, key=f"deact_btn_{tid}"):
                try:
                    taxpayers_repo.set_status(scope, "archived")
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    if st.session_state.get(auth.ACTIVE_TAXPAYER_KEY) == tid:
                        st.session_state.pop(auth.ACTIVE_TAXPAYER_KEY, None)
                    st.session_state["clients_flash"] = (
                        f"{tp.full_name} deactivated. Turn on *Show "
                        "deactivated clients* to reactivate later.")
                    st.rerun()
    else:
        if st.button("Reactivate client", type="primary",
                     key=f"react_btn_{tid}"):
            try:
                taxpayers_repo.set_status(scope, "active")
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.session_state["clients_flash"] = (
                    f"{tp.full_name} reactivated.")
                st.rerun()
