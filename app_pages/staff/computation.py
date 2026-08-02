"""Dual-regime tax computation: manual inputs → engine → snapshot + sheet.

Bank-statement-driven prefill of these inputs arrives with M5; this page is
already fully usable for regime advice and return preparation.
"""

from itrapp import guard

ctx = guard.page(roles={"staff"})

import dataclasses
import datetime as dt

import pandas as pd
import streamlit as st

from itrapp import state
from itrapp.widgets import assessee
from itrcore.analysis import autofill, recon_gaps
from itrcore.db.repos import computations as comp_repo
from itrcore.db.repos import importers as importers_repo
from itrcore.db.repos import taxpayers as taxpayers_repo
from itrcore.export import (computation_html, computation_pdf,
                            computation_xlsx)
from itrcore.export.format import inr
from itrcore.ingest import ais as ais_mod
from itrcore.ingest import books_excel, cg_excel
from itrcore.tax import serde
from itrcore.tax.ayconfig import load as load_cfg
from itrcore.tax.engine import compute_assessment
from itrcore.tax.exempt import EXEMPT_SECTIONS

st.title("Computation")
scope = state.require_active_scope(ctx)
ay = state.ay_picker()
tp = taxpayers_repo.get_taxpayer(scope)
assessee.header(scope, ay, tp=tp)
key = f"{scope.taxpayer_id}_{ay}"

# Data-editor session keys cleared whenever the seed changes (save /
# compute / auto-fill), so stale deltas never replay onto shifted rows.
_EDITOR_PREFIXES = ("sal", "hp", "biz", "regbiz", "cg", "ded", "tds", "ch",
                    "bf",
                    "club", "ei", "adjsal", "adjhp", "adjbiz", "adjos",
                    "adjtds")

stored = comp_repo.load_inputs(scope, ay) or {}

# ------------- automatic sync from imports (self-healing, merge-safe) -------
# Once per session per client/AY, on a FRESH page visit (no live editor
# state to clobber): re-derive stale AIS rows from the saved document, then
# assemble the import-backed draft onto the stored inputs and save it when
# it changes anything. Manual rows and add/less adjustment lines survive by
# construction; deliberate deviations from AIS belong in adjustment lines.
if not st.session_state.get(f"autosync_done_{key}"):
    st.session_state[f"autosync_done_{key}"] = True
    _notes = []
    from itrcore.ingest import itd_portal as _itd
    _pending_res = _itd.import_pending_ais(scope, ay)
    if _pending_res:
        _notes.append("⚡ " + " ".join(_pending_res["messages"]))
    if importers_repo.refresh_ais_derived(scope, ay):
        _notes.append(
            "♻️ AIS-derived figures were re-derived with the latest "
            "classification rules.")
    if _notes:
        st.session_state[f"autosync_note_{key}"] = "  \n".join(_notes)
    _has_imports = bool(importers_repo.list_form16(scope, ay)
                        or importers_repo.list_tds(scope, ay, source="26as")
                        or importers_repo.list_ais(scope, ay))
    _editors_live = any(f"{p}_{key}" in st.session_state
                        for p in _EDITOR_PREFIXES)
    if _has_imports and not _editors_live:
        _auto = autofill.assemble(scope, ay, base=stored)
        if _auto.inputs != stored:
            comp_repo.save_inputs(scope, ay, _auto.inputs)
            stored = _auto.inputs
            st.session_state[f"autofill_res_{key}"] = {
                "sources": [dataclasses.asdict(s) for s in _auto.sources],
                "follow_ups": list(_auto.follow_ups),
                "filled": list(_auto.filled_heads), "auto": True}

_autosync_note = st.session_state.pop(f"autosync_note_{key}", None)
if _autosync_note:
    st.info(_autosync_note)

# ------------------------------------------- income profile (from onboarding)
_PROFILE_HEADS = [
    ("has_salary", "Salary"), ("has_let_out_property", "House property"),
    ("has_home_loan", "House property"),
    ("has_business_44ad", "Business 44AD"),
    ("has_profession_44ada", "Profession 44ADA"),
    ("has_goods_carriage_44ae", "Goods carriage 44AE"),
    ("has_business_books", "Business (books)"),
    ("has_property_sale", "Capital gains"),
    ("has_securities_cg", "Capital gains"),
    ("has_interest_income", "Other sources"),
    ("has_dividend_income", "Other sources"),
    ("has_gifts", "Other sources")]
_heads_flagged = list(dict.fromkeys(
    label for flag, label in _PROFILE_HEADS if tp.profile.get(flag)))
if _heads_flagged:
    st.caption("Income profile: **" + "** · **".join(_heads_flagged)
               + "** — fill the matching tabs below.")
_biz_flags = ("has_business_44ad", "has_profession_44ada",
              "has_goods_carriage_44ae", "has_business_books")
if any(tp.profile.get(f) for f in _biz_flags) and not stored.get("businesses"):
    st.info("This client's profile includes business/professional income — "
            "add it in the **Business** tab.")

# ---------------------------------------------------- auto-fill from imports
with st.container(border=True):
    st.markdown("**⚡ Auto-fill from imported data**")
    st.caption(
        "Assembles a draft from Form 16 (salary — else derived employer-"
        "wise from 26AS section-192 'amount paid', else the AIS "
        "aggregate), Form 26AS (TDS/TCS), AIS/TIS (interest, dividend, "
        "family pension and the AIS other-income categories — interest "
        "from others/I-T refund, winnings, annuities …) and last year's "
        "final computation (brought-forward losses). Anything you have "
        "typed in the tabs below is MERGED, never discarded: imports "
        "override only their own figures, every kept manual entry is "
        "listed, and your manual add/less adjustment lines always ride "
        "through untouched. Review, reconcile and edit below before "
        "computing — nothing is filed.")
    if st.button("Auto-fill this computation", key=f"autofill_btn_{key}"):
        # Deferred: the actual assemble runs at the bottom of this script,
        # AFTER the editors have rendered, so the CURRENT editor contents
        # (including rows not yet saved) form the merge base.
        st.session_state[f"autofill_req_{key}"] = True
    summary = st.session_state.get(f"autofill_res_{key}")
    if summary:
        if summary["filled"]:
            _how = ("Auto-synced from imports on page load — filled "
                    if summary.get("auto") else "Filled ")
            st.success(_how + ", ".join(summary["filled"])
                       + " — review the tabs below, then Compute.")
        else:
            st.info("Nothing to auto-fill yet — import Form 16 / 26AS / AIS "
                    "on the Data import page first. Manual entries below "
                    "are untouched.")
        if summary["sources"]:
            st.markdown("**Draft classified by head** (engine computes the "
                        "exact head-wise income when you hit Compute):")
            _HEAD_ORDER = ["Salary", "House property", "Business",
                           "Capital gains", "Other sources", "Clubbing",
                           "Exempt income", "Deductions", "TDS",
                           "Brought-forward loss"]
            _groups: dict[str, list] = {}
            for s in summary["sources"]:
                _groups.setdefault(s["head"], []).append(s)
            for head in (_HEAD_ORDER
                         + [h for h in _groups if h not in _HEAD_ORDER]):
                rows = _groups.get(head)
                if not rows:
                    continue
                total = sum(r["amount_inr"] for r in rows)
                st.markdown(f"**{head}** · {len(rows)} item(s) · "
                            f"{inr(total)}")
                st.dataframe(pd.DataFrame([
                    {"Particulars": r["detail"],
                     "Amount": inr(r["amount_inr"]),
                     "Source": r["origin"]} for r in rows]),
                    hide_index=True, width="stretch")
        for note in summary["follow_ups"]:
            st.caption(f"⚠️ {note}")


# Editor ⇄ records plumbing lives in itrapp.editors (pure pandas, unit-
# tested — incl. the NaT-to-default collapse for empty date cells).
from itrapp.editors import records as _records
from itrapp.editors import seed as _seed


# Subhead pickers for the adjustment editors (label shown ↔ engine target).
_OS_ADJ_TARGETS = {
    "other_sources.savings_interest": "Savings interest",
    "other_sources.fd_interest": "FD/deposit interest",
    "other_sources.dividend": "Dividend",
    "other_sources.family_pension": "Family pension",
    "other_sources.taxable_gifts": "Taxable gifts 56(2)(x)",
    "other_sources.other": "Other income",
}
_CREDIT_ADJ_TARGETS = {"tds": "TDS credit", "tcs": "TCS credit"}


def _adjustments_editor(suffix: str, targets, caption: str) -> list[dict]:
    """One Genius-style manual add/less editor. ``targets`` is either a
    single engine target (head-level editors) or a {target: label} dict
    that adds a subhead picker. Returns adjustment rows for _collect()."""
    fixed = isinstance(targets, str)
    stored_rows = [a for a in stored.get("adjustments", [])
                   if ((a.get("target") == targets) if fixed
                       else a.get("target") in targets)]
    with st.expander(f"➕➖ Manual add/less adjustments "
                     f"({len(stored_rows)})", expanded=bool(stored_rows)):
        st.caption(caption + " Positive amounts add, negative subtract; "
                   "every row appears as its own line in the computation "
                   "and survives Auto-fill.")
        if fixed:
            df = st.data_editor(
                _seed([{"label": a.get("label", ""),
                        "amount": a.get("amount", 0)}
                       for a in stored_rows],
                      {"label": "text", "amount": "int"}),
                num_rows="dynamic", key=f"adj{suffix}_{key}",
                column_config={
                    "label": st.column_config.TextColumn(
                        "Particulars", width="large"),
                    "amount": st.column_config.NumberColumn(
                        "Add (+) / Less (−)", step=1),
                })
            return [{"target": targets, "label": r["label"],
                     "amount": r["amount"]}
                    for r in _records(df, {"label": "", "amount": 0})
                    if r["amount"]]
        target_of = {v: k for k, v in targets.items()}
        df = st.data_editor(
            _seed([{"subhead": targets.get(a.get("target"), ""),
                    "label": a.get("label", ""),
                    "amount": a.get("amount", 0)}
                   for a in stored_rows],
                  {"subhead": "text", "label": "text", "amount": "int"}),
            num_rows="dynamic", key=f"adj{suffix}_{key}",
            column_config={
                "subhead": st.column_config.SelectboxColumn(
                    "Subhead", options=list(targets.values())),
                "label": st.column_config.TextColumn("Particulars",
                                                     width="large"),
                "amount": st.column_config.NumberColumn(
                    "Add (+) / Less (−)", step=1),
            })
        return [{"target": target_of[r["subhead"]], "label": r["label"],
                 "amount": r["amount"]}
                for r in _records(df, {"subhead": "", "label": "",
                                       "amount": 0})
                if r["amount"] and r["subhead"] in target_of]


_BOOK_ACCOUNTS = (
    ("balance_sheet", "Balance Sheet",
     (("sources", "Sources of funds"), ("application", "Application of "
                                        "funds"))),
    ("manufacturing", "Manufacturing account",
     (("credits", "Credits"), ("debits", "Debits"))),
    ("trading", "Trading account",
     (("credits", "Credits"), ("debits", "Debits"))),
    ("pnl", "Profit & Loss account",
     (("credits", "Credits"), ("debits", "Debits"))),
)


def _books_editor(biz_name: str, parsed: dict) -> None:
    """Type or correct the balance sheet / manufacturing / trading / P&L
    line items directly. The import gets you 90% of the way; this is the
    other 10% — and the only way in for a client who has no Genius workbook
    to upload. Saving re-derives the account chain, so the reconciliation
    against the business row stays honest."""
    edited: dict[str, dict] = {}
    changed = False
    for account, title, sides in _BOOK_ACCOUNTS:
        acct = parsed.get(account) or {}
        st.markdown(f"*{title}*")
        cols = st.columns(len(sides))
        acct_out: dict[str, list] = {}
        for col, (side_key, side_title) in zip(cols, sides):
            with col:
                df = st.data_editor(
                    _seed(acct.get(side_key, []),
                          {"label": "text", "amount": "int"}),
                    num_rows="dynamic",
                    key=f"books_{key}_{biz_name}_{account}_{side_key}",
                    column_config={
                        "label": st.column_config.TextColumn(side_title,
                                                             width="large"),
                        "amount": st.column_config.NumberColumn("Amount",
                                                                step=1),
                    })
                rows = [r for r in _records(df, {"label": "", "amount": 0})
                        if r["label"] or r["amount"]]
                acct_out[side_key] = rows
                if rows != list(acct.get(side_key, [])):
                    changed = True
        edited[account] = acct_out
    if changed and st.button("💾 Save books", key=f"books_save_{key}_"
                                                  f"{biz_name}"):
        merged = dict(stored)
        books = dict(merged.get("books", {}))
        books[biz_name] = {**parsed, **edited}
        merged["books"] = books
        comp_repo.save_inputs(scope, ay, merged)
        st.rerun()


st.caption(
    "✏️ Every line item below is editable: click any cell to change it, "
    "tick the checkbox at a row's left edge and press **Delete** (or the "
    "🗑 icon) to remove it — auto-filled rows included — and use the ➕ "
    "blank row at the bottom to add. **Save inputs** persists your edits; "
    "Auto-fill merges around them.")
tabs = st.tabs(["Salary", "House property", "Business", "Capital gains",
                "Other sources", "Deductions", "TDS · TCS & taxes paid",
                "Clubbing", "Exempt income", "Settings"])

# Exempt-income sections are stored as bare codes ("10(1)") but edited as
# "code — label" strings so the dropdown is self-explanatory.
_EI_OPTIONS = {code: f"{code} — {label}"
               for code, label in EXEMPT_SECTIONS.items()}

with tabs[0]:
    sal_df = st.data_editor(
        _seed(stored.get("salaries", []),
              {"employer_name": "text", "employer_tan": "text",
               "gross_salary": "int", "exempt_allowances": "int",
               "prof_tax": "int", "entertainment_allowance": "int"}),
        num_rows="dynamic", key=f"sal_{key}",
        column_config={
            "employer_name": st.column_config.TextColumn(
                "Employer", help="Left blank → named 'Employer N' so the "
                                 "row still counts"),
            "employer_tan": st.column_config.TextColumn("TAN"),
            "gross_salary": st.column_config.NumberColumn(
                "Gross salary (17)", min_value=0, step=1),
            "exempt_allowances": st.column_config.NumberColumn(
                "Exempt allowances (10)", min_value=0, step=1),
            "prof_tax": st.column_config.NumberColumn(
                "Professional tax", min_value=0, step=1),
            "entertainment_allowance": st.column_config.NumberColumn(
                "Entertainment allow. 16(ii)", min_value=0, step=1),
        })
    adj_sal = _adjustments_editor(
        "sal", "salary",
        "Add/less against the computed SALARY head (e.g. arrears not in "
        "Form 16, or a reduction pending a revised Form 16).")

with tabs[1]:
    hp_df = st.data_editor(
        _seed(stored.get("house_properties", []),
              {"kind": "text", "address": "text",
               "annual_rent_received": "int", "municipal_taxes_paid": "int",
               "interest_24b": "int",
               "pre_construction_interest_total": "int",
               "co_owner_share_pct": "float",
               "arrears_rent_received": "int"}),
        num_rows="dynamic", key=f"hp_{key}",
        column_config={
            "kind": st.column_config.SelectboxColumn(
                "Type", options=["let_out", "self_occupied"]),
            "address": st.column_config.TextColumn("Address"),
            "annual_rent_received": st.column_config.NumberColumn(
                "Rent received (year)", min_value=0, step=1),
            "municipal_taxes_paid": st.column_config.NumberColumn(
                "Municipal taxes", min_value=0, step=1),
            "interest_24b": st.column_config.NumberColumn(
                "Interest 24(b)", min_value=0, step=1),
            "pre_construction_interest_total": st.column_config.NumberColumn(
                "Pre-construction interest (total, 1/5 claimed)",
                min_value=0, step=1),
            "co_owner_share_pct": st.column_config.NumberColumn(
                "Owner share %", min_value=0.0, max_value=100.0, step=0.5),
            "arrears_rent_received": st.column_config.NumberColumn(
                "Arrears/unrealised rent (25A)", min_value=0, step=1),
        })
    adj_hp = _adjustments_editor(
        "hp", "house_property",
        "Add/less against the HOUSE PROPERTY head total (applied before "
        "the ₹2,00,000 loss set-off cap).")

with tabs[2]:
    st.caption("Presumptive schemes only (44AD business · 44ADA profession · "
               "44AE goods carriage). Presumptive income is deemed net of all "
               "expenses/depreciation; enter a higher figure only if actually "
               "declared. Regular books (P&L, depreciation) arrive next.")
    biz_df = st.data_editor(
        _seed(stored.get("businesses", []),
              {"section": "text", "name": "text",
               "turnover_digital": "int", "turnover_cash": "int",
               "gross_receipts": "int", "heavy_ton_months": "int",
               "other_vehicle_months": "int", "declared_income": "int"}),
        num_rows="dynamic", key=f"biz_{key}",
        column_config={
            "section": st.column_config.SelectboxColumn(
                "Section", options=["44AD", "44ADA", "44AE"]),
            "name": st.column_config.TextColumn("Business / profession"),
            "turnover_digital": st.column_config.NumberColumn(
                "44AD digital turnover", min_value=0, step=1),
            "turnover_cash": st.column_config.NumberColumn(
                "44AD cash turnover", min_value=0, step=1),
            "gross_receipts": st.column_config.NumberColumn(
                "44ADA gross receipts", min_value=0, step=1),
            "heavy_ton_months": st.column_config.NumberColumn(
                "44AE heavy ton-months", min_value=0, step=1,
                help="Σ over >12MT vehicles of (tonnage × months held)"),
            "other_vehicle_months": st.column_config.NumberColumn(
                "44AE other vehicle-months", min_value=0, step=1),
            "declared_income": st.column_config.NumberColumn(
                "Higher declared income (optional)", min_value=0, step=1),
        })
    st.caption("Regular books (non-presumptive) — net profit per P&L, "
               "adjusted book→tax. Enter depreciation both as charged in "
               "books (added back) and as allowable u/s 32 (deducted).")

    with st.expander("📚 Regular books of accounts (Genius BS & P&L "
                     "workbook) — import & reconcile"):
        st.caption(
            "Two ways in. **Import** the filled Genius-format BS and P&L "
            "workbook (the statutory ITR-3 Part A structure: Balance Sheet "
            "sources/application, Manufacturing, Trading and P&L heads with "
            "their subheads) — or **start blank** and type the line items "
            "here, for a client who has no such workbook. Either way the "
            "tool re-derives cost of goods produced → gross profit → **net "
            "profit per books** from the entry lines, and the business row's "
            "income below must reconcile with it — any difference is flagged "
            "before Compute. Everything stays editable afterwards.")
        _biz_names = [b.get("name") or f"Business {i + 1}"
                      for i, b in enumerate(
                          stored.get("regular_businesses", []))]
        if not _biz_names:
            st.info("Add a regular-books business row below first, then "
                    "import its workbook here.")
        else:
            books_biz = st.selectbox("Books belong to", _biz_names,
                                     key=f"books_biz_{key}")
            books_up = st.file_uploader(
                "Genius BS & P&L workbook (.xlsx)", type=["xlsx"],
                key=f"books_up_{key}")
            if books_biz not in (stored.get("books", {}) or {}) and \
                    st.button("➕ Start blank books for this business",
                              key=f"books_blank_{key}"):
                merged = dict(stored)
                books = dict(merged.get("books", {}))
                books[books_biz] = {
                    account: {side: [] for side, _ in sides}
                    for account, _, sides in _BOOK_ACCOUNTS}
                merged["books"] = books
                comp_repo.save_inputs(scope, ay, merged)
                st.rerun()
            if books_up is not None and st.button(
                    "Import books & reconcile", type="primary",
                    key=f"books_import_{key}"):
                try:
                    parsed = books_excel.parse_books_excel(
                        books_up.getvalue())
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    merged = dict(stored)
                    books = dict(merged.get("books", {}))
                    books[books_biz] = parsed
                    merged["books"] = books
                    comp_repo.save_inputs(scope, ay, merged)
                    st.rerun()
        for biz_name, parsed in (stored.get("books", {}) or {}).items():
            summary_b = books_excel.summarise(parsed)
            st.markdown(f"**{biz_name} — books on file**")
            bc = st.columns(4)
            bc[0].metric("Cost of goods produced",
                         inr(summary_b.cost_of_goods_produced))
            bc[1].metric("Gross profit", inr(summary_b.gross_profit))
            bc[2].metric("Net profit per books",
                         inr(summary_b.net_profit))
            bc[3].metric("BS sources − application",
                         inr(summary_b.bs_gap))
            if summary_b.bs_gap:
                st.warning("Balance Sheet does not balance — sources "
                           f"{inr(summary_b.bs_sources)} vs application "
                           f"{inr(summary_b.bs_application)}.")
            entered = next(
                (int(b.get("net_profit_before_tax", 0) or 0)
                 for i, b in enumerate(stored.get("regular_businesses",
                                                  []))
                 if (b.get("name") or f"Business {i + 1}") == biz_name),
                None)
            if entered is None:
                st.warning(f"No business row named **{biz_name}** below — "
                           "rename the row or re-import under the right "
                           "name.")
            elif entered == summary_b.net_profit:
                st.success(f"Reconciled ✓ — business income {inr(entered)} "
                           "ties to the books' net profit.")
            else:
                st.error(f"NOT reconciled — business row shows "
                         f"{inr(entered)} but the books compute "
                         f"{inr(summary_b.net_profit)}.")
                if st.button("Adopt books net profit into the business "
                             "row", key=f"books_adopt_{key}_{biz_name}"):
                    merged = dict(stored)
                    rows = [dict(b) for b in
                            merged.get("regular_businesses", [])]
                    for i, b in enumerate(rows):
                        if (b.get("name") or f"Business {i + 1}") \
                                == biz_name:
                            b["net_profit_before_tax"] = \
                                summary_b.net_profit
                    merged["regular_businesses"] = rows
                    comp_repo.save_inputs(scope, ay, merged)
                    st.session_state.pop(f"regbiz_{key}", None)
                    st.rerun()
            _books_editor(biz_name, parsed)

    st.caption(
        "Tick **Speculative** for intra-day equity and anything else that is "
        "a speculation business u/s 43(5). It is kept in its own bucket "
        "because a speculation LOSS can be set off only against speculation "
        "PROFITS (Sec 73) — never against salary, interest or capital gains "
        "— and carries forward for four years, not eight.")
    regbiz_df = st.data_editor(
        _seed(stored.get("regular_businesses", []),
              {"name": "text", "is_speculative": "bool", "turnover": "int",
               "net_profit_before_tax": "int",
               "depreciation_books": "int", "depreciation_it": "int",
               "disallowances": "int", "other_additions": "int",
               "other_deductions": "int"}),
        num_rows="dynamic", key=f"regbiz_{key}",
        column_config={
            "name": st.column_config.TextColumn("Business / profession"),
            "is_speculative": st.column_config.CheckboxColumn(
                "Speculative?",
                help="Sec 43(5) — intra-day equity and similar. Ring-fenced "
                     "for set-off and carry-forward."),
            "turnover": st.column_config.NumberColumn(
                "Turnover", min_value=0, step=1,
                help="Shown in the speculative-activity P&L statement."),
            "net_profit_before_tax": st.column_config.NumberColumn(
                "Net profit per P&L", step=1),
            "depreciation_books": st.column_config.NumberColumn(
                "Add: depreciation (books)", min_value=0, step=1),
            "depreciation_it": st.column_config.NumberColumn(
                "Less: depreciation u/s 32", min_value=0, step=1),
            "disallowances": st.column_config.NumberColumn(
                "Add: disallowances (40/40A/43B)", min_value=0, step=1),
            "other_additions": st.column_config.NumberColumn(
                "Add: other", min_value=0, step=1),
            "other_deductions": st.column_config.NumberColumn(
                "Less: other", min_value=0, step=1),
        })
    adj_biz = _adjustments_editor(
        "biz", "business",
        "Add/less against the BUSINESS/PROFESSION head total (presumptive "
        "and regular-books rows combined).")

with tabs[3]:
    st.caption("Sec 50C is applied automatically when stamp-duty value "
               "exceeds 110% of consideration. For property acquired before "
               "23-07-2024 the engine picks the lower of 12.5% unindexed "
               "vs 20% indexed.")

    with st.expander("📥 Import capital gains from Excel (Genius Share "
                     "Detail template)"):
        st.caption(
            "Fill the **Genius-format Share Detail workbook** — the same 19 "
            "columns and dropdowns as the Genius capital-gain template "
            "(Listed/Unlisted, Category, Long/Short-Term, Sale Amount, "
            "dates, Purchase Cost, 55(2)(ac) FMV, STT, 94(7)/(8) and the "
            "per-unit 112A columns). On import every row is classified into "
            "short-term / long-term and its Sec 111A/112/112A bucket from "
            "the dates, and the sheet's own Long/Short-Term column is "
            "cross-checked. A filled copy of the original Genius .xls "
            "imports identically; the older standardized template still "
            "works.")
        st.download_button(
            "⬇️ Download blank template (Excel)",
            data=cg_excel.build_template(),
            file_name="capital_gains_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument."
                 "spreadsheetml.sheet",
            key=f"cg_tpl_{key}")
        _ais_doc_saved = importers_repo.get_ais_document(scope, ay)
        _cg_prefill = (cg_excel.prefill_from_ais(
            ais_mod.document_from_dict(_ais_doc_saved["doc"]))
            if _ais_doc_saved else [])
        if not _ais_doc_saved:
            st.caption("ℹ️ No AIS imported for this client/AY yet — import "
                       "it on the **Data import** page and the template "
                       "offers itself pre-filled with the AIS sale "
                       "transactions.")
        elif not _cg_prefill:
            st.caption("ℹ️ This client's AIS reports **no securities "
                       "sales** for this AY — nothing to pre-fill (their "
                       "TIS shows no 'Sale of securities' category "
                       "either). Use the blank template above for any "
                       "off-AIS transfers from the broker statement.")
        if _cg_prefill:
            st.download_button(
                f"⚡ Download template PRE-FILLED from AIS "
                f"({len(_cg_prefill)} sale transactions)",
                data=cg_excel.build_template(prefill=_cg_prefill),
                file_name=f"capital_gains_from_AIS_{tp.pan_masked}_{ay}"
                          ".xlsx",
                mime="application/vnd.openxmlformats-officedocument."
                     "spreadsheetml.sheet",
                type="primary", key=f"cg_tpl_ais_{key}")
            st.caption(
                "Every sale leg from the AIS SFT detail is already in the "
                "sheet — security, category, Long/Short-Term, quantity, "
                "sale price/consideration, STT and the depository-reported "
                "cost of acquisition. **Complete the Date of Purchase "
                "column** (the AIS does not carry it), verify costs "
                "against the broker statement, then import the file "
                "below.")
        cg_up = st.file_uploader("Filled template", type=["xlsx", "xls"],
                                 key=f"cg_up_{key}")
        cg_mode = st.radio(
            "On import", ["Append to the rows below",
                          "Replace the rows below"],
            horizontal=True, key=f"cg_mode_{key}")
        if cg_up is not None and st.button("Import & classify",
                                           type="primary",
                                           key=f"cg_import_{key}"):
            try:
                parsed = cg_excel.parse_cg_excel(cg_up.getvalue(), ay=ay)
            except ValueError as exc:
                st.error(str(exc))
            else:
                new_rows = parsed["rows"]
                if not new_rows:
                    st.warning("No usable rows found in the file.")
                else:
                    existing = (list(stored.get("capital_gains", []))
                                if cg_mode.startswith("Append") else [])
                    merged = dict(stored)
                    merged["capital_gains"] = existing + new_rows
                    comp_repo.save_inputs(scope, ay, merged)
                    st.session_state.pop(f"cg_{key}", None)
                    st.session_state[f"cg_import_res_{key}"] = {
                        "classified": cg_excel.classify_rows(new_rows, ay),
                        "errors": parsed["errors"]}
                    st.rerun()
        cg_res = st.session_state.get(f"cg_import_res_{key}")
        if cg_res:
            st.success(f"Imported {len(cg_res['classified'])} row(s) — "
                       "classified below; review the table, then Compute.")
            st.dataframe(pd.DataFrame([{
                "Asset": cg_excel.ASSET_LABELS.get(r["asset_class"],
                                                   r["asset_class"]),
                "Description": r.get("description") or "—",
                "Sold": r["transfer_date"],
                "Consideration": inr(r["full_value_consideration"]),
                "Term": r["term"], "Bucket": r["bucket"]}
                for r in cg_res["classified"]]),
                hide_index=True, width="stretch")
            for e in cg_res["errors"]:
                st.caption(f"⚠️ {e}")

    cg_df = st.data_editor(
        _seed(stored.get("capital_gains", []),
              {"asset_class": "text", "description": "text",
               "acquisition_date": "date", "transfer_date": "date",
               "term_override": "text",
               "full_value_consideration": "int", "stamp_duty_value": "int",
               "cost_of_acquisition": "int", "cost_of_improvement": "int",
               "transfer_expenses": "int", "fmv_31jan2018": "int",
               "exemption_claimed": "int", "exemption_section": "text",
               "buyer_name": "text", "buyer_pan": "text",
               "buyer_address": "text"}),
        num_rows="dynamic", key=f"cg_{key}",
        column_config={
            "asset_class": st.column_config.SelectboxColumn(
                "Asset class",
                options=["listed_equity_stt", "equity_mf", "listed_other",
                         "property", "unlisted", "other"]),
            "description": st.column_config.TextColumn("Description"),
            "acquisition_date": st.column_config.DateColumn("Acquired"),
            "transfer_date": st.column_config.DateColumn("Sold"),
            "term_override": st.column_config.SelectboxColumn(
                "Term (no acq. date)", options=["", "short", "long"],
                help="Used ONLY while 'Acquired' is blank — the AIS/sheet's "
                     "own Long/Short-Term. Dates always win once entered."),
            "full_value_consideration": st.column_config.NumberColumn(
                "Consideration", min_value=0, step=1),
            "stamp_duty_value": st.column_config.NumberColumn(
                "Stamp-duty value (property)", min_value=0, step=1),
            "cost_of_acquisition": st.column_config.NumberColumn(
                "Cost", min_value=0, step=1),
            "cost_of_improvement": st.column_config.NumberColumn(
                "Improvement", min_value=0, step=1),
            "transfer_expenses": st.column_config.NumberColumn(
                "Transfer expenses", min_value=0, step=1),
            "fmv_31jan2018": st.column_config.NumberColumn(
                "FMV 31-01-2018 (112A)", min_value=0, step=1),
            "exemption_claimed": st.column_config.NumberColumn(
                "Exemption claimed", min_value=0, step=1),
            "exemption_section": st.column_config.SelectboxColumn(
                "Exemption sec", options=["", "54", "54EC", "54F"]),
            "buyer_name": st.column_config.TextColumn(
                "Buyer name",
                help="Transferee particulars — Schedule CG requires them for "
                     "immovable property, and they print on the statement."),
            "buyer_pan": st.column_config.TextColumn("Buyer PAN"),
            "buyer_address": st.column_config.TextColumn("Buyer address"),
        })
    st.caption(
        "For land or building acquired **before 23-07-2024** by a RESIDENT "
        "individual/HUF the engine computes both options in the proviso to "
        "Sec 112(1) — 12.5% without indexation and 20% with indexation — "
        "adopts the lower, and prints both sides in the *LONG-TERM CAPITAL "
        "GAIN — RATE OPTION RECONCILIATION* block of the computation sheet. "
        "A non-resident gets 12.5% only; the sheet says so explicitly.")

with tabs[4]:
    os_stored = stored.get("other_sources", {})
    c1, c2, c3 = st.columns(3)
    with c1:
        os_savings = st.number_input("Savings-account interest", 0,
                                     value=os_stored.get("savings_interest",
                                                         0), step=1000)
        os_fd = st.number_input("FD/RD/other interest", 0,
                                value=os_stored.get("fd_interest", 0),
                                step=1000)
        os_po = st.number_input(
            "Post-office savings interest (gross)", 0,
            value=os_stored.get("post_office_savings_interest", 0), step=100,
            help="Enter the GROSS figure. The ₹3,500 (₹7,000 joint) "
                 "exemption u/s 10(15)(i) is applied automatically under "
                 "both regimes — do not net it off yourself.")
        os_po_joint = st.checkbox(
            "…held jointly (₹7,000 exemption)",
            value=bool(os_stored.get("post_office_joint_account", False)))
    with c2:
        os_div = st.number_input("Dividend", 0,
                                 value=os_stored.get("dividend", 0),
                                 step=1000)
        os_fp = st.number_input("Family pension", 0,
                                value=os_stored.get("family_pension", 0),
                                step=1000)
    with c3:
        os_gifts = st.number_input(
            "Taxable gifts u/s 56(2)(x)", 0,
            value=os_stored.get("taxable_gifts", 0), step=1000,
            help="Full amount once aggregate non-relative gifts exceed "
                 "₹50,000")
        os_other = st.number_input("Other income", 0,
                                   value=os_stored.get("other", 0),
                                   step=1000)
        os_ded57 = st.number_input(
            "Less: expenses u/s 57", 0,
            value=os_stored.get("deductions_57", 0), step=1000)
    adj_os = _adjustments_editor(
        "os", _OS_ADJ_TARGETS,
        "Genius-style add/less lines inside a subhead — e.g. 'INTT. ON NSC "
        "(accrued)' +10,616 on FD/deposit interest, 'PENSION FROM LIC' "
        "+1,00,624 on Other income, or 'Exempt u/s 10(15)' −3,500 against "
        "the interest a bank reported. Family-pension lines get the 1/3rd "
        "deduction and interest lines feed 80TTA/80TTB automatically.")

with tabs[5]:
    ded_sections = sorted(
        s for s in load_cfg(ay).raw["deductions"]
        if isinstance(load_cfg(ay).raw["deductions"].get(s), dict))
    ded_df = st.data_editor(
        _seed([{"section": s, "amount": a}
               for s, a in stored.get("deductions", [])],
              {"section": "text", "amount": "int"}),
        num_rows="dynamic", key=f"ded_{key}",
        column_config={
            "section": st.column_config.SelectboxColumn(
                "Section", options=ded_sections),
            "amount": st.column_config.NumberColumn("Claimed", min_value=0,
                                                    step=1),
        })
    st.caption("Caps and the 80CCE group limit are applied by the engine; "
               "new-regime disallowals are shown on the sheet.")

with tabs[6]:
    st.markdown("**TDS / TCS** — deductor- and collector-wise. TCS rows "
                "(section 206C…) imported from 26AS sit here too; the "
                "credit maths is identical.")
    tds_df = st.data_editor(
        _seed(stored.get("tds", []),
              {"tan": "text", "deductor": "text", "section": "text",
               "amount": "int"}),
        num_rows="dynamic", key=f"tds_{key}",
        column_config={
            "tan": st.column_config.TextColumn("TAN"),
            "deductor": st.column_config.TextColumn("Deductor/Collector"),
            "section": st.column_config.TextColumn(
                "Section", help="192/194A/… for TDS; 206C(1H) etc. for "
                                "TCS"),
            "amount": st.column_config.NumberColumn("TDS/TCS",
                                                    min_value=0, step=1),
        })
    adj_tds = _adjustments_editor(
        "tds", _CREDIT_ADJ_TARGETS,
        "Add/less against the TDS or TCS credit (e.g. TCS on a car "
        "purchase not yet in 26AS, or dropping a deductor's wrong credit). "
        "Before generating the ITR, convert kept adjustments into proper "
        "TAN-wise rows above — the e-filing schedules are built from the "
        "rows.")
    st.markdown("**Advance / self-assessment tax challans**")
    st.caption(
        "Tax the assessee paid himself. **Auto-fill** populates these from "
        "the ITD pull — AIS **Part B3 (Payment of Taxes)**, plus PART-C of "
        "an older Form 26AS. Note the current TRACES 26AS no longer carries "
        "them at all: its Part VII says \"for other details refer AIS\". "
        "They are a tax CREDIT — without them the computation shows a "
        "payable the client has already settled, and charges 234B/234C "
        "interest on money paid on time.")
    st.caption(
        "⚠️ A challan is reported in the AIS of the year it was **paid** but "
        "discharges the year it **relates to**. Self-assessment tax for this "
        "AY is normally paid in July, i.e. in the NEXT financial year, so it "
        "appears only in the following year's AIS. Pull that one and it is "
        "credited here automatically; until it exists, type the challan in.")
    ch_df = st.data_editor(
        _seed(stored.get("challans", []),
              {"kind": "text", "paid_on": "date", "amount": "int",
               "reference": "text"}),
        num_rows="dynamic", key=f"ch_{key}",
        column_config={
            "kind": st.column_config.SelectboxColumn(
                "Type", options=["advance", "self_assessment"]),
            "paid_on": st.column_config.DateColumn("Paid on"),
            "amount": st.column_config.NumberColumn("Amount", min_value=0,
                                                    step=1),
            "reference": st.column_config.TextColumn(
                "BSR - challan no.",
                help="Printed on the computation, Genius-style."),
        })

with tabs[7]:
    st.caption("Income of spouse / minor child / transferred asset clubbed in "
               "the assessee's hands (Sec 60–64). Minor-child income gets the "
               "Sec 10(32) exemption automatically. v1 clubs at slab rates.")
    club_df = st.data_editor(
        _seed(stored.get("clubbing", []),
              {"section": "text", "head": "text", "amount": "int",
               "minor_child": "bool", "description": "text"}),
        num_rows="dynamic", key=f"club_{key}",
        column_config={
            "section": st.column_config.SelectboxColumn(
                "Section", options=["64(1A)", "64(1)", "64(2)", "60", "61"]),
            "head": st.column_config.SelectboxColumn(
                "Head", options=["salary", "house_property", "business",
                                 "capital_gains", "other_sources"]),
            "amount": st.column_config.NumberColumn("Amount", min_value=0,
                                                    step=1),
            "minor_child": st.column_config.CheckboxColumn("Minor child?"),
            "description": st.column_config.TextColumn("Description"),
        })

with tabs[8]:
    st.caption("Income exempt under the Act — shown separately in the "
               "computation and reported in Schedule EI of the return, "
               "never added to total income. Net agricultural income "
               "u/s 10(1) above ₹5,000 is automatically aggregated for "
               "rate purposes (partial integration) in both regimes.")
    ei_stored = [{**r, "section": _EI_OPTIONS.get(r.get("section"),
                                                  r.get("section"))}
                 for r in stored.get("exempt_incomes", [])]
    ei_df = st.data_editor(
        _seed(ei_stored,
              {"section": "text", "description": "text", "amount": "int"}),
        num_rows="dynamic", key=f"ei_{key}",
        column_config={
            "section": st.column_config.SelectboxColumn(
                "Section", options=list(_EI_OPTIONS.values()),
                width="large"),
            "description": st.column_config.TextColumn("Description"),
            "amount": st.column_config.NumberColumn(
                "Amount (net)", min_value=0, step=1),
        })

with tabs[9]:
    settings = stored
    regime_choice = st.radio(
        "Regime election", ["auto", "new", "old"], horizontal=True,
        index=["auto", "new", "old"].index(
            settings.get("regime_election", "auto")),
        help="'auto' computes both and recommends the cheaper one. Note: "
             "opting old regime is done in the ITR itself for "
             "non-business income.")
    filing_default = settings.get("filing_date")
    _due = dt.date.fromisoformat(load_cfg(ay).raw["due_date"])
    filing_date = st.date_input(
        "Date of filing (drives 234A/B/C/F)",
        value=(dt.date.fromisoformat(filing_default) if filing_default
               else min(dt.date.today(), _due)),
        help=f"If the return is already filed, enter the ACTUAL filing date "
             f"from the acknowledgement — that is what 234A/234F turn on. "
             f"For an unfiled return this defaults to the due date "
             f"({_due:%d-%m-%Y}) or today, whichever is earlier, so a "
             f"still-timely return is not charged a late fee.")
    if filing_date and filing_date > _due:
        st.warning(
            f"Filing on {filing_date:%d-%m-%Y} is after the "
            f"{_due:%d-%m-%Y} due date — 234A interest and the 234F late fee "
            "will be charged.")

    _bf = settings.get("brought_forward", {}) or {}
    st.markdown("**Brought-forward losses (Sec 71B / 72 / 73 / 74)**")
    st.caption(
        "One row per assessment year, because the carry-forward window runs "
        "from the AY the loss was determined — 8 years for house property, "
        "business and capital losses, 4 for speculation. The engine sets "
        "them off oldest-first against the income each is allowed to touch, "
        "reports what lapsed, and prints the losses table on the "
        "computation sheet.")
    _bf_seed = _bf.get("entries") or [
        {"ay": "", "kind": k, "amount": _bf.get(src, 0)}
        for k, src in (("house_property", "hp_loss"), ("business", "business"),
                       ("speculation", "speculation"), ("stcl", "stcl"),
                       ("ltcl", "ltcl")) if _bf.get(src)]
    bf_df = st.data_editor(
        _seed(_bf_seed, {"ay": "text", "kind": "text", "amount": "int"}),
        num_rows="dynamic", key=f"bf_{key}",
        column_config={
            "ay": st.column_config.TextColumn(
                "A.Y. of the loss", help="e.g. 2023-24. Leave blank only if "
                                         "the year is genuinely unknown — a "
                                         "blank row never expires."),
            "kind": st.column_config.SelectboxColumn(
                "Head", options=["house_property", "business", "speculation",
                                 "stcl", "ltcl"]),
            "amount": st.column_config.NumberColumn(
                "Unabsorbed amount", min_value=0, step=1000),
        })
    # Legacy flat totals stay zero once per-AY rows exist, so nothing is
    # counted twice.
    bf_stcl = bf_ltcl = bf_hp_loss = 0


def _collect() -> dict:
    return {
        "ay": ay,
        "personal": {"dob": tp.dob or None,
                     "resident_status": tp.resident_status},
        "salaries": [
            {**r, "employer_name": r["employer_name"]
             or f"Employer {i + 1}"}
            for i, r in enumerate(_records(sal_df, {
                "employer_name": "", "employer_tan": "", "gross_salary": 0,
                "exempt_allowances": 0, "prof_tax": 0,
                "entertainment_allowance": 0}))],
        "house_properties": _records(hp_df, {
            "kind": "let_out", "address": "", "annual_rent_received": 0,
            "municipal_taxes_paid": 0, "interest_24b": 0,
            "pre_construction_interest_total": 0,
            "co_owner_share_pct": 100.0, "arrears_rent_received": 0}),
        "capital_gains": _records(cg_df, {
            "asset_class": "other", "description": "",
            "acquisition_date": None, "transfer_date": None,
            "term_override": "",
            "full_value_consideration": 0, "stamp_duty_value": None,
            "cost_of_acquisition": 0, "cost_of_improvement": 0,
            "transfer_expenses": 0, "fmv_31jan2018": None,
            "exemption_claimed": 0, "exemption_section": ""}),
        "businesses": _records(biz_df, {
            "section": "44AD", "name": "", "turnover_digital": 0,
            "turnover_cash": 0, "gross_receipts": 0, "heavy_ton_months": 0,
            "other_vehicle_months": 0, "declared_income": 0}),
        "regular_businesses": _records(regbiz_df, {
            "name": "", "net_profit_before_tax": 0, "depreciation_books": 0,
            "depreciation_it": 0, "disallowances": 0, "other_additions": 0,
            "other_deductions": 0}),
        "other_sources": {
            "savings_interest": int(os_savings), "fd_interest": int(os_fd),
            "dividend": int(os_div), "family_pension": int(os_fp),
            "taxable_gifts": int(os_gifts), "other": int(os_other),
            "deductions_57": int(os_ded57),
            "post_office_savings_interest": int(os_po),
            "post_office_joint_account": bool(os_po_joint)},
        "clubbing": _records(club_df, {
            "section": "64(1A)", "head": "other_sources", "amount": 0,
            "minor_child": False, "description": ""}),
        "exempt_incomes": [
            {**r, "section": r["section"].split(" — ")[0] or "Other"}
            for r in _records(ei_df, {"section": "", "description": "",
                                      "amount": 0})
            if r["section"] or r["amount"]],
        "deductions": [(r["section"], r["amount"])
                       for r in _records(ded_df,
                                         {"section": "", "amount": 0})
                       if r["section"]],
        "adjustments": adj_sal + adj_hp + adj_biz + adj_os + adj_tds,
        "tds": _records(tds_df, {"tan": "", "deductor": "", "section": "",
                                 "amount": 0}),
        "challans": _records(ch_df, {"kind": "advance", "paid_on": None,
                                     "amount": 0, "reference": ""}),
        "brought_forward": {
            "hp_loss": int(bf_hp_loss), "stcl": int(bf_stcl),
            "ltcl": int(bf_ltcl),
            "entries": [r for r in _records(
                bf_df, {"ay": "", "kind": "business", "amount": 0})
                if r["amount"]]},
        "regime_election": regime_choice,
        "filing_date": filing_date.isoformat() if filing_date else None,
        # Imported books of accounts ride along untouched (the engine
        # ignores them; the recon gate ties business income to them).
        "books": stored.get("books", {}),
    }


st.divider()

# ----------------------------------------- auto-fill (deferred, merge-safe)
# Runs down here so _collect() sees the editors' CURRENT contents — manual
# rows typed but never saved are part of the merge base, not lost.
if st.session_state.pop(f"autofill_req_{key}", False):
    assembled = autofill.assemble(scope, ay, base=_collect())
    comp_repo.save_inputs(scope, ay, assembled.inputs)
    for pfx in _EDITOR_PREFIXES:
        st.session_state.pop(f"{pfx}_{key}", None)
    st.session_state[f"autofill_res_{key}"] = {
        "sources": [dataclasses.asdict(s) for s in assembled.sources],
        "follow_ups": list(assembled.follow_ups),
        "filled": list(assembled.filled_heads)}
    st.rerun()

_flash = st.session_state.pop(f"comp_flash_{key}", None)
if _flash:
    st.success(_flash)
b1, b2, _ = st.columns([1, 1, 3])
with b1:
    if st.button("💾 Save inputs", width="stretch"):
        comp_repo.save_inputs(scope, ay, _collect())
        for _pfx in _EDITOR_PREFIXES:
            st.session_state.pop(f"{_pfx}_{key}", None)
        st.session_state[f"comp_flash_{key}"] = (
            "Inputs saved — every edited/deleted line is now the working "
            "copy.")
        st.rerun()
with b2:
    run = st.button("🧮 Compute", type="primary", width="stretch")
st.caption("On Compute, the inputs are cross-checked against the imported "
           "AIS/TIS/26AS — anything reported there but missing here pops "
           "up for a decision first.")


@st.dialog("⚠️ Imported data not reflected in this computation")
def _gaps_dialog(gaps: list) -> None:
    st.markdown("These figures are present in the imported **AIS / TIS / "
                "26AS** but the inputs about to be computed do not carry "
                "them (fully):")
    for g in gaps:
        st.markdown(f"- **{g.head}** — {g.message}")
    st.caption("**Auto-fill** brings the import-backed figures in and keeps "
               "your manual rows; or edit the relevant tab yourself.")
    d1, d2 = st.columns(2)
    if d1.button("Compute anyway", type="primary",
                 key=f"gaps_anyway_{key}"):
        st.session_state[f"gaps_ack_{key}"] = True
        st.session_state[f"compute_after_ack_{key}"] = True
        st.rerun()
    if d2.button("Go back & fix", key=f"gaps_back_{key}"):
        st.rerun()


# Editing/deleting rows leaves the data_editor holding DELTAS against the
# seed. Once we save the collected inputs, those deltas must never replay
# against the new seed (rows shift → deletions cascade onto the wrong rows).
# So the Compute click SAVES + CLEARS the editors + reruns, and the actual
# computation resumes on the clean pass, collecting exactly what was saved.
if run:
    comp_repo.save_inputs(scope, ay, _collect())
    for _pfx in _EDITOR_PREFIXES:
        st.session_state.pop(f"{_pfx}_{key}", None)
    st.session_state[f"resume_compute_{key}"] = True
    st.rerun()

if st.session_state.pop(f"resume_compute_{key}", False) \
        or st.session_state.pop(f"compute_after_ack_{key}", False):
    collected = _collect()
    comp_repo.save_inputs(scope, ay, collected)
    problems = []
    if any(not r.get("transfer_date") for r in collected["capital_gains"]):
        problems.append("Every capital-gains row needs a transfer (sale) "
                        "date.")
    if any(c.get("amount") and not c.get("paid_on")
           for c in collected["challans"]):
        problems.append("Every advance/self-assessment challan needs its "
                        "payment date.")
    if any(r["amount"] and not r["section"]
           for r in _records(ded_df, {"section": "", "amount": 0})):
        problems.append("A deduction row has an amount but no section — "
                        "pick the section or clear the row.")
    if problems:
        for p in problems:
            st.error(p)
        st.stop()
    gaps = recon_gaps.find_gaps(scope, ay, collected)
    if gaps and not st.session_state.pop(f"gaps_ack_{key}", False):
        _gaps_dialog(gaps)
    else:
        st.session_state.pop(f"gaps_ack_{key}", None)
        try:
            inputs = serde.inputs_from_dict(collected)
            result = compute_assessment(inputs)
        except (ValueError, KeyError) as exc:
            st.error(f"Computation failed: {exc}")
            st.stop()
        st.session_state[f"comp_result_{key}"] = {
            "inputs": collected, "result": serde.result_to_dict(result),
            "meta": {"engine_version": result.engine_version,
                     "config_hash": result.config_hash,
                     "inputs_digest": result.inputs_digest,
                     "recommended": result.recommended}}

cached = st.session_state.get(f"comp_result_{key}")
if cached:
    res = cached["result"]
    meta = cached["meta"]
    old, new = res["old"], res["new"]
    st.subheader("Result")
    m = st.columns(4)
    m[0].metric("Total income (new)", inr(new["total_income"]))
    m[1].metric("Tax — new regime", inr(new["tax_liability"]))
    m[2].metric("Tax — old regime", inr(old["tax_liability"]))
    m[3].metric("Recommended", meta["recommended"].upper(),
                delta=f"saves {inr(res['saving'])}")

    rec = res[meta["recommended"]]
    payable = rec["payable_or_refund"]
    if payable >= 0:
        st.warning(f"Net payable ({meta['recommended']} regime): "
                   f"**{inr(payable)}** "
                   f"(incl. interest 234A {inr(rec['interest_234a'])}, "
                   f"234B {inr(rec['interest_234b'])}, "
                   f"234C {inr(rec['interest_234c'])}, "
                   f"fee 234F {inr(rec['fee_234f'])})")
    else:
        st.success(f"Refund due ({meta['recommended']} regime): "
                   f"**{inr(-payable)}**")

    with st.expander("Schedule lines — new regime"):
        for head in new["heads"].values():
            for ln in head.get("lines", []):
                st.markdown(f"`{ln['section']:>10}` {ln['label']} — "
                            f"**{inr(ln['amount'])}**")
        for ln in (list(new.get("deduction_lines", []))
                   + list(new.get("special_tax_lines", []))
                   + list(new.get("lines", []))):
            st.markdown(f"`{ln['section']:>10}` {ln['label']} — "
                        f"**{inr(ln['amount'])}**")
    with st.expander("Schedule lines — old regime"):
        for head in old["heads"].values():
            for ln in head.get("lines", []):
                st.markdown(f"`{ln['section']:>10}` {ln['label']} — "
                            f"**{inr(ln['amount'])}**")
        for ln in (list(old.get("deduction_lines", []))
                   + list(old.get("special_tax_lines", []))
                   + list(old.get("lines", []))):
            st.markdown(f"`{ln['section']:>10}` {ln['label']} — "
                        f"**{inr(ln['amount'])}**")

    if rec.get("exempt_lines"):
        with st.expander("Exempt income — Schedule EI (reported; not part "
                         "of total income)", expanded=True):
            for ln in rec["exempt_lines"]:
                st.markdown(f"`{ln['section']:>10}` {ln['label']} — "
                            f"**{inr(ln['amount'])}**")

    d1, d2, d3, d4 = st.columns(4)
    sheet_kwargs = dict(taxpayer_name=tp.full_name,
                        pan_masked=tp.pan_masked, ay=ay, result=res)
    with d1:
        if st.button("📌 Save as computation snapshot", width="stretch"):
            v = comp_repo.save_snapshot(
                scope, ay, cached["inputs"], res,
                engine_version=meta["engine_version"],
                config_hash=meta["config_hash"],
                inputs_digest=meta["inputs_digest"])
            st.success(f"Saved as version {v}. Next: **Mark final** below "
                       "(Manager), then generate the return on the **ITR "
                       "generation & filing** page.")
    with d2:
        st.download_button("⬇️ Excel sheet",
                           computation_xlsx.build(
                               **sheet_kwargs,
                               taxpayer={
                                   "dob": tp.dob, "email": tp.email,
                                   "address": tp.address,
                                   "resident_status": tp.resident_status},
                               inputs=cached["inputs"]),
                           file_name=f"computation_{tp.pan_masked}_{ay}"
                                     ".xlsx", width="stretch")
    with d3:
        st.download_button("⬇️ PDF sheet",
                           computation_pdf.build(**sheet_kwargs),
                           file_name=f"computation_{tp.pan_masked}_{ay}"
                                     ".pdf", width="stretch")
    with d4:
        st.download_button("⬇️ HTML",
                           computation_html.render(**sheet_kwargs),
                           file_name=f"computation_{tp.pan_masked}_{ay}"
                                     ".html", width="stretch")

st.divider()
snaps = comp_repo.list_snapshots(scope, ay)
if snaps:
    st.subheader("Saved computations")
    for s in snaps:
        cols = st.columns([4, 2])
        cols[0].markdown(
            f"**v{s['version_no']}** · {s['status']} · {s['created_at']} · "
            f"by {s['created_by']} · engine {s['engine_version']}")
        with cols[1]:
            if s["status"] != "final" and st.button(
                    "Mark final", key=f"fin_{s['version_no']}"):
                try:
                    comp_repo.finalize(scope, ay, s["version_no"])
                except PermissionError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
