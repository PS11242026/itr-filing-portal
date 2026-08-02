"""Data import hub: bank statements (parse → stage → commit), AIS, 26AS,
prefill and Form 16 quick entry."""

from itrapp import guard

ctx = guard.page(roles={"staff"})


import pandas as pd
import streamlit as st

from itrapp import state
from itrapp.widgets import assessee
from itrcore.db.repos import bank_accounts as accounts_repo
from itrcore.db.repos import importers as importers_repo
from itrcore.db.repos import portal_credentials as creds_repo
from itrcore.db.repos import statements as statements_repo
from itrcore.db.repos import taxpayers as taxpayers_repo
from itrcore.export import ais_excel, form26as_pdf_export
from itrcore.export.format import inr
from itrcore.ingest import ais as ais_mod
from itrcore.ingest import ais_crypto
from itrcore.ingest import banks, bank_pdf, form26as, form26as_pdf, prefill
from itrcore.ingest import probe, tabular
from itrcore.ingest import itd_portal
from itrcore.ingest.base import ColumnMap, RawTxn

st.title("Data import")
scope = state.require_active_scope(ctx)
ay = state.ay_picker()
assessee.header(scope, ay)

# ---------------------------------------------------------------- accounts
with st.expander("Bank accounts", expanded=False):
    for acct in accounts_repo.list_accounts(scope):
        st.markdown(f"🏦 **{acct['bank_name']}** ··{acct['last4']} · "
                    f"{acct['account_type']} · share "
                    f"{acct['ownership_share']:.0%}")
    with st.form("add_account", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        bank_name = c1.text_input("Bank")
        account_no = c2.text_input("Account number")
        ifsc = c3.text_input("IFSC (optional)")
        c4, c5 = st.columns(2)
        acc_type = c4.selectbox("Type", ["Savings", "Current", "NRO", "NRE"])
        share = c5.number_input("Ownership share %", 1.0, 100.0, 100.0)
        if st.form_submit_button("Add account"):
            try:
                accounts_repo.add_account(
                    scope, bank_name=bank_name, account_no=account_no,
                    ifsc=ifsc or None, account_type=acc_type,
                    ownership_share=share / 100)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()

accounts = accounts_repo.list_accounts(scope)

# ------------------------------------------------------- statement upload
st.subheader("Bank statement upload")
if not accounts:
    st.info("Add the client's bank account(s) above first.")
else:
    acct_labels = {f"{a['bank_name']} ··{a['last4']}": a["id"]
                   for a in accounts}
    up_col1, up_col2 = st.columns([2, 2])
    account_label = up_col1.selectbox("Account", list(acct_labels))
    upload = up_col2.file_uploader("Statement file (CSV / XLSX / PDF)",
                                   key=f"stmt_up_{scope.taxpayer_id}")
    pdf_password = st.text_input(
        "PDF password (if protected — often PAN, or PAN+DOB)",
        type="password") if upload and upload.name.lower().endswith(".pdf") \
        else None

    if upload is not None and st.button("Parse statement", type="primary"):
        data = upload.getvalue()
        p = probe.make_probe(upload.name, data)
        try:
            extra = tuple(banks.map_from_saved(m["map_json"])
                          for m in importers_repo.saved_maps(ctx.principal))
            if p.kind == "pdf":
                result = bank_pdf.parse_pdf(p, pdf_password, extra)
            else:
                result = banks.best_parse(p, extra)
        except ValueError as exc:
            st.error(str(exc))
            result = None
        if result is not None:
            st.session_state["parse_state"] = {
                "probe_name": upload.name, "data": data, "kind": p.kind,
                "result": result, "account_id": acct_labels[account_label]}

parse_state = st.session_state.get("parse_state")
if parse_state:
    result = parse_state["result"]
    st.markdown(f"Adapter **{result.adapter_id}** · confidence "
                f"**{result.confidence:.0%}** · {len(result.txns)} "
                f"transactions parsed")
    for issue in result.issues[:5]:
        st.caption(f"⚠️ {issue}")

    if result.txns and result.confidence >= 0.8:
        preview = pd.DataFrame([{
            "date": t.txn_date, "narration": t.narration[:60],
            "amount": t.amount_paise / 100,
            "balance": (t.balance_paise / 100
                        if t.balance_paise is not None else None)}
            for t in result.txns[:15]])
        st.dataframe(preview, hide_index=True, width="stretch")
        if st.button("Stage these transactions"):
            stmt_id = statements_repo.create_staged(
                scope, account_id=parse_state["account_id"], ay=ay,
                doc_id=None, adapter_id=result.adapter_id,
                txns=list(result.txns), issues=list(result.issues))
            st.success(f"Staged statement #{stmt_id} — review and commit "
                       "below.")
            st.session_state.pop("parse_state", None)
            st.rerun()
    else:
        st.warning("Low confidence — map the columns manually.")
        with st.expander("Manual column mapper", expanded=True):
            p = probe.make_probe(parse_state["probe_name"],
                                 parse_state["data"])
            if p.kind == "pdf":
                frame = bank_pdf.frame_from_pdf(
                    bank_pdf.decrypt_if_needed(p.data, pdf_password))
            else:
                frame = tabular.load_frame(p)
            st.dataframe(frame.head(12), width="stretch")
            header_row = st.number_input("Header row index (0-based)",
                                         0, min(30, len(frame) - 1), 0)
            headers = [f"{i}: {frame.iloc[int(header_row), i]}"
                       for i in range(len(frame.columns))]
            cols = st.columns(4)
            roles = {}
            role_defs = [("date", True), ("narration", True),
                         ("debit", False), ("credit", False),
                         ("amount", False), ("drcr", False),
                         ("balance", False), ("ref", False)]
            for i, (role, required) in enumerate(role_defs):
                with cols[i % 4]:
                    pick = st.selectbox(
                        f"{role}{' *' if required else ''}",
                        ["—"] + headers, key=f"map_{role}")
                    if pick != "—":
                        roles[role] = int(pick.split(":")[0])
            style = st.radio("Amount style",
                             ["separate debit/credit", "single signed",
                              "single + Dr/Cr column"], horizontal=True)
            bank_hint = st.text_input("Bank name (for saving this map)")
            if st.button("Parse with this mapping"):
                cmap = ColumnMap(
                    id=f"custom_{bank_hint or 'bank'}".lower(),
                    bank_hint=bank_hint or "custom",
                    header_synonyms={},
                    amount_style={"separate debit/credit": "separate",
                                  "single signed": "single_signed",
                                  "single + Dr/Cr column": "single_drcr"
                                  }[style])
                custom = tabular.parse_with_map(
                    frame, cmap, header_row=int(header_row), mapping=roles)
                if not custom.txns:
                    st.error("Nothing parsed — check the role assignments.")
                else:
                    sig = banks.header_signature(frame, int(header_row))
                    syns = {role: [str(frame.iloc[int(header_row), c])]
                            for role, c in roles.items()}
                    importers_repo.save_map(
                        ctx.principal, header_signature=sig,
                        bank_hint=bank_hint or "custom",
                        map_json=banks.map_to_json(ColumnMap(
                            id=cmap.id, bank_hint=cmap.bank_hint,
                            header_synonyms=syns,
                            amount_style=cmap.amount_style)))
                    st.session_state["parse_state"]["result"] = custom
                    st.success(f"Parsed {len(custom.txns)} rows; map saved "
                               "for next time.")
                    st.rerun()

# ------------------------------------------------------ staged statements
staged = [s for s in statements_repo.list_statements(scope, ay)
          if s["status"] == "staged"]
if staged:
    st.subheader("Staged statements — review & commit")
for s in staged:
    with st.container(border=True):
        st.markdown(f"**#{s['id']}** {s['bank_name']} ··{s['last4']} · "
                    f"adapter {s['adapter_id']}")
        txns = statements_repo.staged_txns(scope, s["id"])
        edit_df = pd.DataFrame([{
            "date": t.txn_date, "narration": t.narration,
            "amount_inr": t.amount_paise / 100,
            "balance_inr": (t.balance_paise / 100
                            if t.balance_paise is not None else None),
            "ref": t.ref} for t in txns])
        edited = st.data_editor(edit_df, num_rows="dynamic",
                                key=f"stage_ed_{s['id']}",
                                width="stretch", height=280)
        c1, c2 = st.columns(2)
        if c1.button("Save edits", key=f"save_st_{s['id']}"):
            new_txns = []
            for i, row in edited.iterrows():
                if pd.isna(row["date"]) or pd.isna(row["amount_inr"]):
                    continue
                d = row["date"]
                d = d.date() if isinstance(d, pd.Timestamp) else d
                new_txns.append(RawTxn(
                    txn_date=d,
                    narration=str(row["narration"] or "").upper(),
                    amount_paise=int(round(float(row["amount_inr"]) * 100)),
                    balance_paise=(int(round(float(row["balance_inr"])
                                             * 100))
                                   if pd.notna(row["balance_inr"])
                                   else None),
                    ref=(str(row["ref"]) if pd.notna(row["ref"])
                         else None),
                    row_no=i + 1))
            statements_repo.replace_staged(scope, s["id"], new_txns)
            st.success("Edits saved.")
            st.rerun()
        if c2.button("Commit to ledger", key=f"commit_{s['id']}",
                     type="primary"):
            stats = statements_repo.commit(scope, s["id"])
            st.success(f"Committed: {stats['inserted']} inserted, "
                       f"{stats['duplicates_skipped']} duplicates skipped.")
            st.rerun()

# ---------------------------------------------------------------- coverage
st.subheader("Statement coverage (FY)")
for c in statements_repo.coverage(scope, ay):
    if not c["has_data"]:
        st.markdown(f"🔴 {c['label']} — nothing imported")
    elif c["gaps"]:
        st.markdown(f"🟠 {c['label']} — missing months: "
                    f"{', '.join(c['gaps'])}")
    else:
        st.markdown(f"✅ {c['label']} — {c['months_covered']} months, "
                    "no internal gaps")

st.divider()
# ------------------------------------------------- pull from ITD portal
with st.container(border=True):
    st.subheader("⬇️ Pull from ITD (AIS · TIS · 26AS)")
    tp_itd = taxpayers_repo.get_taxpayer(scope)
    st.caption(
        "Opens a Chrome window with the e-filing user ID "
        f"(**{tp_itd.pan_masked}**) and the stored password **pre-filled**. "
        "You do the CAPTCHA + OTP in that window — nothing else. The tool "
        "then downloads AIS, TIS and Form 26AS automatically, decrypts them "
        "and runs them through the same importers a file upload uses. The raw "
        "AIS JSON / 26AS PDF are kept under `~/ITR-Portal/pull/downloads`. "
        "The password is Fernet-encrypted at rest and every use is "
        "audit-logged; the tool never solves a CAPTCHA or reads an OTP.")
    if not tp_itd.dob:
        st.warning("⚠️ No date of birth on this client's record — the "
                   "pulled AIS is keyed to PAN + DOB and stays PARKED "
                   "until the DOB exists (26AS/TIS import fine). Save it "
                   "once here; any parked AIS then imports itself.")
        dcol1, dcol2 = st.columns([2, 1])
        _dob_new = dcol1.text_input("Client date of birth (dd/mm/yyyy)",
                                    key=f"pulldob_{scope.taxpayer_id}")
        if dcol2.button("Save DOB to client record",
                        key=f"pulldob_save_{scope.taxpayer_id}"):
            _iso = ais_crypto.iso_dob(_dob_new)
            if _iso:
                taxpayers_repo.update_contact(scope, dob=_iso)
                st.rerun()
            else:
                st.error("Could not read that date — use dd/mm/yyyy.")
    has_creds = creds_repo.has_credentials(scope)
    with st.form("itd_creds", clear_on_submit=True):
        st.text_input("e-Filing user ID (PAN)", value=tp_itd.pan_masked,
                      disabled=True)
        pw = st.text_input("e-Filing password", type="password")
        fc1, fc2 = st.columns(2)
        save_pw = fc1.form_submit_button("Save / update password",
                                         width="stretch")
        clear_pw = fc2.form_submit_button("Remove stored password",
                                          width="stretch")
    if save_pw:
        try:
            creds_repo.save_credentials(scope, password=pw)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success("Password stored (encrypted).")
            st.rerun()
    if clear_pw:
        creds_repo.delete_credentials(scope)
        st.success("Stored password removed.")
        st.rerun()

    if has_creds:
        st.markdown("Password on file ✅")
        st.caption("A Chrome window will open. Finish the CAPTCHA + OTP "
                   "there; keep it open — the tool takes over once you reach "
                   "the dashboard. This can take a minute.")
        if st.button("Pull AIS / TIS / 26AS from ITD", type="primary"):
            password = creds_repo.get_password(scope)
            with st.spinner("Driving the portal — complete the CAPTCHA/OTP in "
                            "the browser window…"):
                try:
                    out = itd_portal.pull_and_import(
                        scope, ay, itd_portal.default_client(),
                        pan=tp_itd.pan, password=password or "")
                except (itd_portal.PortalError, ValueError) as exc:
                    st.error(str(exc))
                    out = None
            if out is not None:
                c = out["counts"]
                st.success(
                    f"Imported {c.get('ais', 0)} AIS, {c.get('tis', 0)} TIS "
                    f"and {c.get('as26', 0)} Form 26AS rows. Go to "
                    "**Computation → Auto-fill** to build the draft.")
                if not c.get("as26"):
                    st.warning(
                        "Form 26AS did not come through on this pull — the "
                        "step trace below shows how far it got. Fallback "
                        "that works today: download the Annual Tax "
                        "Statement PDF from TRACES yourself and import it "
                        "in the **Form 26AS** section further down this "
                        "page — parsing, the TDS feed into Computation "
                        "auto-fill and the formatted PDF export are the "
                        "same either way.")
                for m in out["messages"]:
                    st.caption(f"ℹ️ {m}")
    else:
        st.info("Save the client's e-filing password above to enable the "
                "pull.")

    st.divider()
    with st.expander("Reset e-filing password (client relays the OTP)"):
        st.caption("Opens Chrome on the portal's **Forgot Password** flow "
                   "with the client's user ID pre-filled. You complete it "
                   "there — the OTP goes to the client's registered / "
                   "Aadhaar-linked mobile and the client relays it to you; "
                   "you set the new password ON THE PORTAL, then record it "
                   "here so the vault stays current. The tool never reads "
                   "OTPs or solves CAPTCHAs.")
        reset_key = f"itd_reset_stage_{scope.taxpayer_id}"
        if st.session_state.get(reset_key) != "otp":
            if st.button("Open Forgot-Password in Chrome "
                         "(user ID pre-filled)"):
                try:
                    out = itd_portal.reset_password(
                        scope, itd_portal.default_client(), pan=tp_itd.pan)
                except itd_portal.PortalError as exc:
                    st.error(str(exc))
                else:
                    if out.status == "otp_sent":
                        st.session_state[reset_key] = "otp"
                        st.session_state[reset_key + "_msg"] = out.message
                        st.rerun()
                    else:
                        st.error(out.message)
        else:
            st.info(st.session_state.get(reset_key + "_msg", ""))
            new_pw = st.text_input(
                "New e-filing password (exactly as set on the portal)",
                type="password", key=f"itd_newpw_{scope.taxpayer_id}")
            rc1, rc2 = st.columns(2)
            if rc1.button("Save new password to the vault",
                          type="primary"):
                try:
                    out = itd_portal.reset_password(
                        scope, itd_portal.default_client(), pan=tp_itd.pan,
                        new_password=new_pw or None,
                        otp="completed-on-portal")
                except itd_portal.PortalError as exc:
                    st.error(str(exc))
                else:
                    if out.status == "done":
                        st.session_state.pop(reset_key, None)
                        st.success(out.message + " Stored (encrypted).")
                        st.rerun()
                    else:
                        st.error(out.message)
            if rc2.button("Cancel"):
                st.session_state.pop(reset_key, None)
                st.rerun()

# -------------------------------------------------- AIS / TIS (decrypt)
st.divider()
st.subheader("📑 AIS / TIS — full classified pull (Genius-style)")
tp = taxpayers_repo.get_taxpayer(scope)
st.caption(
    "Upload the **AIS JSON** you downloaded from the compliance portal. The "
    "portal serves it ENCRYPTED (keyed to the client's PAN + date of birth); "
    "the tool decrypts it locally, then classifies every information block "
    "into its part (B1 TDS/TCS, B2 SFT, B3 payment of tax, B4 demand & "
    "refund, B7 other) with the transaction-level detail — and derives the "
    "TIS category summary. No figure is fetched from the live portal here.")

_ais_flash = st.session_state.pop(f"ais_flash_{scope.taxpayer_id}", None)
if _ais_flash:
    st.success(_ais_flash)
if not tp.dob:
    st.warning("⚠️ No date of birth on this client's record. The ITD keys "
               "the AIS file to PAN + DOB, and DOB also drives the senior-"
               "citizen slab rates. Type it once below — a successful "
               "decrypt saves it to the client record automatically.")
ac1, ac2 = st.columns([2, 1])
ais_up = ac1.file_uploader("AIS JSON (encrypted export)", type=["json"],
                           key=f"ais_{scope.taxpayer_id}")
dob_in = ac2.text_input("Client DOB (dd/mm/yyyy)", value=tp.dob or "",
                        key=f"aisdob_{scope.taxpayer_id}",
                        help="Needed to decrypt. Prefilled from the client "
                             "record; correct it here if decryption fails.")
if ais_up and st.button("Decrypt & import AIS / TIS", type="primary"):
    _dob_try = (dob_in or "").strip() or tp.dob
    if ais_crypto.looks_encrypted(ais_up.getvalue()) and not _dob_try:
        st.error("This AIS file is encrypted and the client record has no "
                 "date of birth — type the DOB (dd/mm/yyyy) in the box "
                 "above and click again. It is saved to the record after "
                 "the first successful decrypt.")
    else:
        try:
            doc = ais_mod.parse_ais_document(ais_up.getvalue(), pan=tp.pan,
                                             dob=_dob_try)
        except ValueError as exc:
            st.error(str(exc))
        else:
            entries = ais_mod.parse_ais_json(ais_up.getvalue(), pan=tp.pan,
                                             dob=_dob_try)
            importers_repo.import_ais(scope, ay, entries, ais_up.name)
            importers_repo.save_ais_document(
                scope, ay, ais_mod.document_to_dict(doc), ais_up.name)
            importers_repo.import_tis(scope, ay, ais_mod.tis_entries(doc),
                                      ais_up.name)
            importers_repo.clear_pending_ais(scope, ay)   # superseded
            flash = (f"Decrypted & classified {len(doc.rows)} AIS "
                     f"information blocks for {doc.name}. Go to "
                     "**Computation** — the draft auto-fills on load.")
            # The AIS partA carries the ITD's own DOB — learn it once so
            # decryption (and the age-based slabs) never ask again.
            _learned = ais_crypto.iso_dob(doc.dob or _dob_try)
            if _learned and not tp.dob:
                taxpayers_repo.update_contact(scope, dob=_learned)
                flash += (f" 📌 Date of birth {_learned} saved to the "
                          "client record (from the AIS).")
            st.session_state[f"ais_flash_{scope.taxpayer_id}"] = flash
            st.rerun()

# Parked AIS (a pull that arrived before the DOB was on record) imports
# itself the moment it becomes decryptable — also recovers retained pull
# files from before parking existed.
_pending_res = itd_portal.import_pending_ais(scope, ay)
if _pending_res:
    st.info("⚡ " + " ".join(_pending_res["messages"]))

# Self-healing: whenever the parser's classification/de-dup rules have
# improved since this AIS was imported, re-derive the stored rows from the
# saved document automatically — no re-upload needed.
if importers_repo.refresh_ais_derived(scope, ay):
    st.info("♻️ AIS-derived figures were re-derived with the latest "
            "classification rules — the totals below (and Computation "
            "auto-fill) now reflect them.")

_ais_saved = importers_repo.get_ais_document(scope, ay)
if _ais_saved:
    doc = ais_mod.document_from_dict(_ais_saved["doc"])
    st.download_button(
        "⬇️ Download AIS / TIS workbook (Excel)",
        data=ais_excel.build(doc, ay=ay),
        file_name=f"{doc.pan}_{ay}_AIS_TIS.xlsx",
        mime="application/vnd.openxmlformats-officedocument."
             "spreadsheetml.sheet")
    st.markdown("**Taxpayer Information Summary (TIS)**")
    st.dataframe(pd.DataFrame(
        [{"Sr": s["sr"], "Information category": s["category"],
          "Processed value": inr(s["processed_paise"] // 100),
          "Derived value": inr(s["derived_paise"] // 100)}
         for s in doc.tis_summary()]), hide_index=True, width="stretch")
    # Part B3 is challans, not information blocks — the only pulled source
    # for advance / self-assessment tax now that the TRACES 26AS carries
    # neither. Shown with the AY each one discharges, because that is what
    # decides which computation may credit it.
    if doc.tax_payments:
        with st.expander(f"Part B3 · Payment of taxes  "
                         f"({len(doc.tax_payments)} challans)", expanded=True):
            st.dataframe(pd.DataFrame(
                [{"F.Y.": p.fy, "Credited to A.Y.": p.ay,
                  "Minor head": p.minor_head,
                  "Amount": inr(p.amount_paise // 100),
                  "BSR code": p.bsr_code, "Deposited": p.deposit_date,
                  "Challan no.": p.challan_serial}
                 for p in doc.tax_payments]),
                hide_index=True, width="stretch")
            st.caption(
                "A challan is reported in the AIS of the year it was PAID "
                "but discharges the year it RELATES to, so only the rows "
                "whose **Credited to A.Y.** matches the year you are filing "
                "are picked up by the computation. Self-assessment tax paid "
                "after 31 March appears only in the FOLLOWING year's AIS — "
                "pull that one and it flows through automatically.")
    _part_meta = [("B1", "Part B1 · TDS / TCS"), ("B2", "Part B2 · SFT"),
                  ("B4", "Part B4 · Demand & refund"),
                  ("B7", "Part B7 · Other information")]
    for pid, label in _part_meta:
        blocks = doc.rows_for_part(pid)
        if not blocks:
            continue
        with st.expander(f"{label}  ({len(blocks)} blocks)"):
            for b in blocks:
                st.markdown(
                    f"**{b.category}** · `{b.info_code}` · {b.source} — "
                    f"**{inr(b.amount_paise // 100)}** ({b.count} txns)")
                if b.detail.labels:
                    st.dataframe(pd.DataFrame(list(b.detail.rows),
                                              columns=list(b.detail.labels)),
                                 hide_index=True, width="stretch")

# ------------------------------------------------------- Form 26AS
st.divider()
st.subheader("🧾 Form 26AS")
f26c1, f26c2 = st.columns([2, 1])
up26 = f26c1.file_uploader(
    "26AS — Annual Tax Statement PDF, or TRACES text/CSV",
    type=["pdf", "txt", "csv"], key=f"f26_{scope.taxpayer_id}")
if up26 and f26c2.button("Import 26AS", type="primary"):
    try:
        if up26.name.lower().endswith(".pdf"):
            parsed = form26as_pdf.parse_26as_pdf(up26.getvalue())
            importers_repo.save_26as_document(
                scope, ay,
                form26as_pdf.document_to_dict(parsed["document"]), up26.name)
        else:
            parsed = form26as.parse_26as_text(up26.getvalue())
    except ValueError as exc:
        st.error(str(exc))
    else:
        n = importers_repo.import_26as(scope, ay, parsed["entries"])
        msg = (f"Imported {n} TDS rows "
               f"({len(parsed.get('skipped', []))} skipped).")
        if parsed.get("challans"):
            c = importers_repo.import_challans(scope, ay, parsed["challans"])
            msg += (f" Plus {c} advance/self-assessment challan(s) from "
                    "PART-C — hit **Auto-fill** on the Computation page to "
                    "credit them.")
        st.success(msg)
        st.rerun()

_26_saved = importers_repo.get_26as_document(scope, ay)
if _26_saved:
    doc26 = form26as_pdf.document_from_dict(_26_saved["doc"])
    st.download_button(
        "⬇️ Download formatted 26AS (PDF)",
        data=form26as_pdf_export.build(doc26, ay=ay),
        file_name=f"{doc26.pan}_{ay}_26AS.pdf", mime="application/pdf")
    for part in doc26.parts:
        if not part.deductors:
            continue
        with st.expander(f"{part.title}  ({len(part.deductors)} deductors)"):
            st.dataframe(pd.DataFrame([{
                "Deductor": d.name, "TAN": d.tan,
                "Amount paid": d.total_paid,
                "Tax deducted": d.total_tax_deducted,
                "TDS deposited": d.total_tds_deposited,
                "Txns": len(d.transactions)} for d in part.deductors]),
                hide_index=True, width="stretch")

tds_rows = importers_repo.list_tds(scope, ay, source="26as")
if tds_rows:
    st.caption("TDS entries feeding the computation:")
    st.dataframe(pd.DataFrame([{
        "TAN": r["tan"], "deductor": r["deductor_name"],
        "section": r["section"], "TDS": inr(r["tds_paise"] // 100)}
        for r in tds_rows]), hide_index=True, width="stretch")

c_pre, c_f16 = st.columns(2)
with c_pre:
    st.subheader("Prefill JSON (reference)")
    pre_up = st.file_uploader("Prefill JSON", type=["json"],
                              key=f"pre_{scope.taxpayer_id}")
    if pre_up and st.button("Parse prefill"):
        try:
            info = prefill.parse_prefill_json(pre_up.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.json({k: v for k, v in info.items() if v})

with c_f16:
    st.subheader("Form 16 quick entry")
    with st.form("form16_entry", clear_on_submit=True):
        emp = st.text_input("Employer name")
        tan = st.text_input("TAN")
        gross = st.number_input("Gross salary (17)", 0, step=1000)
        exempt = st.number_input("Exempt allowances (10)", 0, step=1000)
        ptax = st.number_input("Professional tax", 0, step=100)
        tds = st.number_input("TDS (192)", 0, step=1000)
        if st.form_submit_button("Save Form 16") and emp:
            importers_repo.save_form16(
                scope, ay, employer_name=emp, employer_tan=tan,
                gross_salary_paise=int(gross) * 100,
                exempt_allowances_paise=int(exempt) * 100,
                prof_tax_paise=int(ptax) * 100,
                tds_paise=int(tds) * 100)
            st.success("Form 16 saved — use it on the Computation page.")
    for f in importers_repo.list_form16(scope, ay):
        st.caption(f"📄 {f['employer_name']} · gross "
                   f"{inr(f['gross_salary_paise'] // 100)} · TDS "
                   f"{inr(f['tds_paise'] // 100)}")
