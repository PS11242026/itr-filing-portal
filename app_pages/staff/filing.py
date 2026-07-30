"""ITR generation & filing tracker.

Flow: pick a saved computation → eligibility → verification/bank details →
generate + 3-layer validation → export JSON to the outbox → upload manually
on incometax.gov.in → record ack number → record e-verification.
"""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import datetime as dt
import json

import streamlit as st

from itrapp import state
from itrapp.widgets import assessee
from itrcore.db.repos import computations as comp_repo
from itrcore.db.repos import filings as filings_repo
from itrcore.db.repos import portal_credentials as creds_repo
from itrcore.db.repos import taxpayers as taxpayers_repo
from itrcore.ingest import itd_portal
from itrcore.itr import (business_rules, eligibility, itr1, itr2, itr3, itr4,
                         schema_registry)
from itrcore.itr import validate as itr_validate
from itrcore.tax.ayconfig import load as load_cfg

st.title("ITR generation & filing")
scope = state.require_active_scope(ctx)
ay = state.ay_picker()
assessee.header(scope, ay)
tp = taxpayers_repo.get_taxpayer(scope)

from itrcore.authz import has_role

snaps = comp_repo.list_snapshots(scope, ay)
if not snaps:
    # The session may hold a computed-but-never-snapshotted result — offer
    # to save it right here instead of dead-ending.
    cached = st.session_state.get(f"comp_result_{scope.taxpayer_id}_{ay}")
    if cached:
        st.info("This session has a **computed result that was never "
                "saved as a snapshot** — filing freezes on saved, "
                "finalised computations. Save it now to proceed.")
        if st.button("📌 Save the computed result as snapshot v1",
                     type="primary"):
            meta = cached["meta"]
            comp_repo.save_snapshot(
                scope, ay, cached["inputs"], cached["result"],
                engine_version=meta["engine_version"],
                config_hash=meta["config_hash"],
                inputs_digest=meta["inputs_digest"])
            st.rerun()
    else:
        st.info("No saved computations for this client/AY yet. On the "
                "**Computation** page: **🧮 Compute → 📌 Save as "
                "computation snapshot** — then return here to finalise "
                "and file.")
    st.stop()

labels = {f"v{s['version_no']} · {s['status']} · {s['created_at']}":
          s["version_no"] for s in snaps}
chosen = st.selectbox("Computation to file", list(labels))
version = labels[chosen]
snap = comp_repo.get_snapshot(scope, ay, version)
inputs_dict = json.loads(snap["inputs_json"])
result_dict = json.loads(snap["result_json"])

sel = eligibility.select_form(inputs_dict=inputs_dict,
                              result_dict=result_dict, profile=tp.profile)
if sel.form == "ITR-1":
    st.success("Eligible for **ITR-1 (Sahaj)**")
else:
    st.warning(f"**{sel.form} applies** — " + "; ".join(sel.reasons))
    if sel.form in ("ITR-3", "ITR-4"):
        st.caption("ITR-3/4 JSON is a portal-derived reduced structure — "
                   "always validate in the ITD offline utility before "
                   "uploading.")

if snap["status"] != "final":
    st.warning(f"**v{version} is not finalised** — the return is generated "
               "only from a FINAL computation (the freeze reconciliation "
               "signs off on).")
    if has_role(ctx.principal, "Manager"):
        if st.button(f"✅ Mark v{version} final now", type="primary"):
            try:
                comp_repo.finalize(scope, ay, version)
            except PermissionError as exc:
                st.error(str(exc))
            else:
                st.rerun()
    else:
        st.caption("A Manager or Partner must mark it final — ask them, or "
                   "do it from the Computation page's Saved computations "
                   "list.")
    st.stop()

from itrcore.analysis import gate as gate_mod
from itrcore.db import auditlog

report = gate_mod.readiness(scope, ay)
override_ok = True
if not report.ok:
    st.warning("**Readiness blockers** — resolve these before filing:")
    for b in report.blockers:
        st.markdown(f"- {b}")
    override_ok = st.checkbox(
        "Proceed anyway (recorded in the audit log — e.g. no bank "
        "statements were required for this client)")

regime = st.radio(
    "Regime for the return", ["new", "old"], horizontal=True,
    index=0 if result_dict["recommended"] == "new" else 1,
    help=f"Engine recommends **{result_dict['recommended']}** "
         f"(saves ₹{result_dict['saving']:,})")

due = dt.date.fromisoformat(load_cfg(ay).raw["due_date"])
today = dt.date.today()
st.caption(f"Due date u/s 139(1): {due:%d-%m-%Y} — filing "
           + ("on time." if today <= due else
              "**after the due date** (belated u/s 139(4); 234A/234F "
              "apply)."))

with st.form("filing_meta"):
    st.markdown("**Verification & refund details**")
    c1, c2 = st.columns(2)
    with c1:
        father = st.text_input("Father's name (verification)")
        place = st.text_input("Place of verification")
        city = st.text_input("City", tp.address or "")
        pincode = st.text_input("PIN code")
    with c2:
        bank_name = st.text_input("Bank name (refund account)")
        ifsc = st.text_input("IFSC")
        account_no = st.text_input("Account number")
        state_code = st.text_input("State code (e.g. 27 Maharashtra)")
    generate = st.form_submit_button(f"Generate {sel.form} JSON",
                                     type="primary")

if generate and not override_ok:
    st.error("Resolve the readiness blockers above (or tick the override) "
             "before generating.")
elif generate:
    if not report.ok:
        auditlog.log(ctx.principal.username, "READINESS_OVERRIDE",
                     entity="filing", taxpayer_id=scope.taxpayer_id,
                     details={"ay": ay, "blockers": list(report.blockers)})
    bundle = schema_registry.get(sel.form, ay)
    filing_meta = {
        "father_name": father, "place": place, "city": city,
        "pincode": pincode, "state_code": state_code,
        "bank_name": bank_name, "ifsc": ifsc.strip().upper(),
        "account_no": account_no.strip(),
        "filing_date": today.isoformat(),
        "return_sec": 11 if today <= due else 12,
    }
    taxpayer_dict = {"pan": tp.pan, "full_name": tp.full_name,
                     "dob": tp.dob, "email": tp.email,
                     "mobile": tp.mobile, "address": tp.address}
    builder = {"ITR-1": itr1, "ITR-2": itr2, "ITR-3": itr3,
               "ITR-4": itr4}[sel.form]
    doc = builder.build(taxpayer=taxpayer_dict, inputs_dict=inputs_dict,
                        result_dict=result_dict, regime=regime,
                        filing_meta=filing_meta, bundle=bundle)

    schema_errors = itr_validate.validate_schema(doc, bundle)
    biz_errors = business_rules.check(doc, expected_pan=tp.pan,
                                      regime_result=result_dict[regime])
    rt_errors = business_rules.round_trip_check(doc, result_dict[regime])

    if schema_errors or biz_errors or rt_errors:
        st.error("Validation failed — fix before export:")
        for e in schema_errors:
            st.markdown(f"- schema: `{e}`")
        for e in biz_errors:
            st.markdown(f"- rule: {e}")
        for e in rt_errors:
            st.markdown(f"- {e}")
    else:
        fid, path = filings_repo.create_filing(
            scope, ay=ay, form=sel.form,
            filing_type="original" if today <= due else "belated",
            computation_id=snap["id"], json_dict=doc,
            schema_version=bundle.version)
        st.success(f"All validations passed. Filing #{fid} saved to the "
                   f"outbox:\n\n`{path}`")
        st.download_button(
            f"⬇️ Download {sel.form} JSON",
            json.dumps(doc, ensure_ascii=False, indent=1),
            file_name=path.split("\\")[-1], mime="application/json")
        st.info("**Next (manual, on incometax.gov.in):** e-File → Income "
                "Tax Return → upload this JSON via the offline-utility "
                "flow → verify in the utility → submit → e-verify (Aadhaar "
                "OTP/net-banking) → record the acknowledgement number "
                "below. Always let the official utility validate before "
                "upload.")

st.divider()
st.subheader("Filing tracker")
rows = filings_repo.list_filings(scope, ay)
if not rows:
    st.caption("Nothing generated yet.")
for row in rows:
    with st.container(border=True):
        st.markdown(
            f"**#{row['id']} {row['form']}** ({row['filing_type']}) · "
            f"status **{row['status']}** · schema {row['schema_version']} · "
            f"{row['created_at']}"
            + (f" · ack **{row['ack_no']}**" if row["ack_no"] else ""))
        if row["status"] == "generated":
            if row["json_path"]:
                try:
                    with open(row["json_path"], "rb") as fh:
                        json_bytes = fh.read()
                except OSError:
                    json_bytes = None
                if json_bytes:
                    st.download_button(
                        "⬇️ JSON (for manual filing on incometax.gov.in)",
                        json_bytes,
                        file_name=row["json_path"].replace("\\", "/")
                        .rsplit("/", 1)[-1],
                        mime="application/json", key=f"dljson_{row['id']}")
            # -- browser-driven upload: the tool logs in (human does
            # CAPTCHA/OTP), navigates File-ITR and attaches this JSON;
            # the practitioner reviews, submits and e-verifies.
            if creds_repo.has_credentials(scope) and row["json_path"]:
                if st.button("🚀 Upload on ITD portal (browser-driven)",
                             key=f"itdup_{row['id']}", type="primary"):
                    password = creds_repo.get_password(scope)
                    with st.spinner("Driving the portal — complete the "
                                    "CAPTCHA/OTP in the Chrome window…"):
                        try:
                            msgs = itd_portal.SeleniumFilingClient().upload(
                                pan=tp.pan, password=password or "",
                                ay=ay, json_path=row["json_path"])
                        except itd_portal.PortalError as exc:
                            st.error(str(exc))
                        else:
                            auditlog.log(
                                ctx.principal.username, "ITR_PORTAL_UPLOAD",
                                entity="filing", entity_id=row["id"],
                                taxpayer_id=scope.taxpayer_id,
                                details={"ay": ay, "form": row["form"]})
                            for m in msgs:
                                st.caption(f"ℹ️ {m}")
                            st.info("Once submitted and e-verified in the "
                                    "browser, record the acknowledgement "
                                    "number below.")
            else:
                st.caption("Browser-driven upload needs the client's "
                           "e-filing password saved on the **Data import** "
                           "page; the JSON download above always works for "
                           "manual filing.")
            c1, c2, c3 = st.columns([2, 2, 1])
            ack = c1.text_input("Acknowledgement no.",
                                key=f"ack_{row['id']}")
            filed = c2.date_input("Filed on", value=today,
                                  key=f"fd_{row['id']}")
            if c3.button("Mark uploaded", key=f"up_{row['id']}"):
                try:
                    filings_repo.update_status(
                        scope, row["id"], "uploaded", ack_no=ack,
                        filed_on=filed.isoformat())
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        elif row["status"] == "uploaded":
            filed_on = row["filed_on"]
            if filed_on:
                deadline = (dt.date.fromisoformat(filed_on)
                            + dt.timedelta(days=30))
                remaining = (deadline - today).days
                if remaining >= 0:
                    st.warning(f"e-verify within **{remaining} days** "
                               f"(by {deadline:%d-%m-%Y}) or the return "
                               "is treated as not filed.")
                else:
                    st.error("e-verification window has LAPSED — file a "
                             "condonation request or re-file.")
            c1, c2 = st.columns([2, 1])
            ev_date = c1.date_input("e-verified on", value=today,
                                    key=f"ev_{row['id']}")
            if c2.button("Mark e-verified", key=f"evb_{row['id']}"):
                filings_repo.update_status(scope, row["id"], "everified",
                                           everified_on=ev_date.isoformat())
                st.rerun()
        elif row["status"] == "everified":
            if st.button("Mark processed (intimation 143(1) received)",
                         key=f"pr_{row['id']}"):
                filings_repo.update_status(scope, row["id"], "processed")
                st.rerun()
