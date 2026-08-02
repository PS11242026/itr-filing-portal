"""Excel computation sheet (XlsxWriter, in-memory) — client-ready layout.

A Summary sheet (old vs new regime) plus one Schedule sheet per regime with
head-wise, section-referenced lines. Brand-neutral styling; figures in whole
rupees. Includes the business/PGBP head.
"""

import io

import xlsxwriter

# Excel silently collapses the naive '#,##,##0' to WESTERN grouping
# (53,27,790 renders as 5,327,790 — a practitioner reads that as 5.3
# million). True Indian lakh grouping needs the conditional idiom below;
# the escaped commas are literal separators at the Indian positions.
INR_FMT = ('[>=100000]#\\,##\\,##0;'
           '[<=-100000][Red]-#\\,##\\,##0;#,##0')
_ACCENT = "#1A5276"
_HEADS = ("salary", "house_property", "business", "speculation",
          "capital_gains", "other_sources")
_HEAD_TITLES = {
    "salary": "Salary",
    "house_property": "Income from house property",
    "business": "Profits & gains of business or profession",
    "speculation": "Speculation business (Sec 43(5))",
    "capital_gains": "Capital gains",
    "other_sources": "Income from other sources",
}
_COMP_HEAD_TITLES = {
    "salary": "SALARIES",
    "house_property": "INCOME FROM HOUSE PROPERTY",
    "business": "PROFITS & GAINS OF BUSINESS OR PROFESSION",
    "speculation": "SPECULATION BUSINESS",
    "capital_gains": "CAPITAL GAINS",
    "other_sources": "INCOME FROM OTHER SOURCES",
}


_LOSS_LABELS = {
    "house_property": "House Property Loss",
    "business": "Ordinary Business Loss",
    "speculation": "Speculation Business Loss",
    "stcl": "Short Term Capital Loss",
    "ltcl": "Long Term Capital Loss",
}


def _head_amount(head: dict) -> int:
    """Head totals live under `amount` on a HeadResult but under `head_total`
    on the capital-gains result — reading only `amount` is what printed
    CAPITAL GAINS as zero on every statement."""
    if "head_total" in head:
        return int(head.get("head_total") or 0)
    return int(head.get("amount") or 0)


def _computation_sheet(wb, result: dict, *, regime_key: str,
                       taxpayer_name: str, pan_masked: str, ay: str,
                       taxpayer: dict, inputs: dict, f_title, f_sub,
                       f_head, f_cell, f_num, f_total_lbl, f_total,
                       f_group, f_note) -> None:
    """The practitioner-format statement: assessee block → COMPUTATION OF
    TOTAL INCOME (head-wise with line detail and staggered totals) →
    COMPUTATION OF TAX ON TOTAL INCOME (slab band ladder + special-rate
    subheads) → TDS section-wise → payable rounded u/s 288B."""
    import datetime as _dt

    regime = result[regime_key]
    ws = wb.add_worksheet("Computation")
    ws.hide_gridlines(2)
    ws.set_column(0, 0, 62)
    ws.set_column(1, 3, 16)
    ws.fit_to_pages(1, 0)

    f_label = wb.add_format({"bold": True})
    f_center = wb.add_format({"bold": True, "align": "center",
                              "underline": True})

    r = 0
    ws.write(r, 0, f"Computation of Income & Tax — AY {ay}", f_title)
    r += 1
    header_rows = [("NAME OF ASSESSEE", taxpayer_name),
                   ("PAN", pan_masked)]
    if taxpayer.get("address"):
        header_rows.append(("ADDRESS", taxpayer["address"]))
    header_rows.append(("STATUS", "INDIVIDUAL"))
    if taxpayer.get("dob"):
        header_rows.append(("DATE OF BIRTH", taxpayer["dob"]))
    if taxpayer.get("resident_status"):
        header_rows.append(("RESIDENTIAL STATUS",
                            taxpayer["resident_status"].upper()))
    if taxpayer.get("email"):
        header_rows.append(("EMAIL ADDRESS", taxpayer["email"]))
    fy = f"{int(ay[:4]) - 1} - {ay[:4]}"
    header_rows += [
        ("ASSESSMENT YEAR", ay.replace("-", " - ")),
        ("FINANCIAL YEAR", fy),
        ("OPTED FOR TAXATION U/S 115BAC",
         "YES" if regime_key == "new" else "NO"),
        ("COMPUTATION DATE",
         _dt.datetime.now().strftime("%d-%m-%Y %I:%M %p")),
    ]
    for label, value in header_rows:
        ws.write(r, 0, label, f_label)
        ws.write(r, 1, f":  {value}")
        r += 1
    r += 1

    ws.merge_range(r, 0, r, 3, "COMPUTATION OF TOTAL INCOME", f_center)
    r += 1
    for head_key in _HEADS:
        head = regime["heads"].get(head_key) or {}
        head_lines = head.get("lines", [])
        amount = _head_amount(head)
        if not head_lines and not amount:
            continue
        ws.write(r, 0, _COMP_HEAD_TITLES[head_key], f_group)
        ws.write(r, 1, "", f_group)
        ws.write(r, 2, "", f_group)
        ws.write_number(r, 3, amount, f_total)
        r += 1
        for ln in head_lines:
            ws.write(r, 0, ln["label"], f_cell)
            ws.write(r, 1, ln["section"], f_cell)
            ws.write_number(r, 2, ln["amount"], f_num)
            r += 1
    setoff_lines = regime.get("setoff_lines", [])
    if setoff_lines:
        ws.write(r, 0, "SET OFF & CARRY FORWARD OF LOSSES (SEC 70–74)",
                 f_group)
        ws.write(r, 1, "", f_group)
        ws.write(r, 2, "", f_group)
        ws.write(r, 3, "", f_group)
        r += 1
        for ln in setoff_lines:
            ws.write(r, 0, ln["label"], f_cell)
            ws.write(r, 1, ln["section"], f_cell)
            ws.write_number(r, 2, ln["amount"], f_num)
            r += 1
    ws.write(r, 0, "GROSS TOTAL INCOME", f_total_lbl)
    ws.write_number(r, 3, regime["gti"], f_total)
    r += 1
    for ln in regime.get("deduction_lines", []):
        ws.write(r, 0, ln["label"], f_cell)
        ws.write(r, 1, ln["section"], f_cell)
        ws.write_number(r, 2, ln["amount"], f_num)
        r += 1
    if regime.get("deductions_total"):
        ws.write(r, 0, "LESS: DEDUCTIONS UNDER CHAPTER VI-A", f_cell)
        ws.write_number(r, 3, -regime["deductions_total"], f_num)
        r += 1
    ws.write(r, 0, "TOTAL INCOME (ROUNDED OFF U/S 288A)", f_total_lbl)
    ws.write_number(r, 3, regime["total_income"], f_total)
    r += 1
    if regime.get("exempt_lines"):
        ws.write(r, 0, "EXEMPT INCOME — SCHEDULE EI (not part of total "
                       "income)", f_group)
        r += 1
        for ln in regime["exempt_lines"]:
            ws.write(r, 0, ln["label"], f_cell)
            ws.write(r, 1, ln["section"], f_cell)
            ws.write_number(r, 2, ln["amount"], f_num)
            r += 1
    r += 1

    ws.merge_range(r, 0, r, 3, "COMPUTATION OF TAX ON TOTAL INCOME",
                   f_center)
    r += 1
    # --- the slab ladder: every band as its own subhead line ---
    for ln in regime.get("slab_lines", []):
        ws.write(r, 0, ln["label"], f_cell)
        if ln["amount"] == 0 and "@" not in ln["label"]:
            ws.write(r, 1, "NIL", f_cell)
        else:
            ws.write_number(r, 1, ln["amount"], f_num)
        r += 1
    ws.write(r, 0, f"TAX ON ₹{regime['normal_income']:,} "
                   "(AT SLAB RATES)", f_total_lbl)
    ws.write_number(r, 2, regime["slab_tax"], f_total)
    r += 1
    for ln in regime.get("special_tax_lines", []):
        ws.write(r, 0, ln["label"], f_cell)
        ws.write(r, 1, ln["section"], f_cell)
        ws.write_number(r, 2, ln["amount"], f_num)
        r += 1
    if regime.get("rebate_87a"):
        ws.write(r, 0, "LESS: REBATE U/S 87A", f_cell)
        ws.write_number(r, 2, -regime["rebate_87a"], f_num)
        r += 1
    surcharge_net = regime["surcharge"] - regime["marginal_relief"]
    if surcharge_net:
        ws.write(r, 0, "ADD: SURCHARGE (NET OF MARGINAL RELIEF)", f_cell)
        ws.write_number(r, 2, surcharge_net, f_num)
        r += 1
    ws.write(r, 0, "ADD: HEALTH AND EDUCATION CESS", f_cell)
    ws.write_number(r, 2, regime["cess"], f_num)
    r += 1
    ws.write(r, 0, "TOTAL TAX LIABILITY", f_total_lbl)
    ws.write_number(r, 2, regime["tax_liability"], f_total)
    r += 1

    # --- TDS section-wise (needs the computation inputs) ---
    tds_rows = (inputs or {}).get("tds", [])
    credit_adjustments = [a for a in (inputs or {}).get("adjustments", [])
                          if a.get("target") in ("tds", "tcs")]
    if tds_rows or credit_adjustments:
        ws.write(r, 0, "LESS: TAX DEDUCTED / COLLECTED AT SOURCE", f_group)
        r += 1
        by_section: dict[str, list] = {}
        for t in tds_rows:
            by_section.setdefault(str(t.get("section") or "—"),
                                  []).append(t)
        for section in sorted(by_section):
            rows = by_section[section]
            names = ", ".join(sorted({str(t.get("deductor") or t.get("tan")
                                          or "deductor")[:28]
                                      for t in rows})[:3])
            ws.write(r, 0, f"SECTION {section}: {names}", f_cell)
            ws.write_number(r, 1, sum(int(t.get("amount", 0) or 0)
                                      for t in rows), f_num)
            r += 1
        for a in credit_adjustments:
            kind = "TCS" if a.get("target") == "tcs" else "TDS"
            ws.write(r, 0, f"ADJUSTMENT ({kind}): "
                           f"{a.get('label') or 'manual'}", f_cell)
            ws.write_number(r, 1, int(a.get("amount", 0) or 0), f_num)
            r += 1
        ws.write(r, 0, "TOTAL TDS/TCS", f_cell)
        ws.write_number(r, 2, regime["tds_total"], f_num)
        r += 1
    elif regime.get("tds_total"):
        ws.write(r, 0, "LESS: TAX DEDUCTED AT SOURCE", f_cell)
        ws.write_number(r, 2, regime["tds_total"], f_num)
        r += 1
    challans = (inputs or {}).get("challans", [])

    def _challan_block(kind: str, heading: str, total: int) -> None:
        nonlocal r
        if not total:
            return
        rows = [c for c in challans if (c.get("kind") or "advance") == kind]
        ws.write(r, 0, heading, f_group)
        r += 1
        for c in rows:
            paid_on = str(c.get("paid_on") or "")
            if len(paid_on) == 10:                      # ISO → dd/mm/yyyy
                paid_on = f"{paid_on[8:]}/{paid_on[5:7]}/{paid_on[:4]}"
            ref = " - ".join(x for x in (c.get("reference"), paid_on) if x)
            ws.write(r, 0, ref or "Challan", f_cell)
            ws.write_number(r, 1, int(c.get("amount", 0) or 0), f_num)
            r += 1
        ws.write(r, 0, "TOTAL", f_cell)
        ws.write_number(r, 2, total, f_num)
        r += 1

    _challan_block("advance", "LESS: ADVANCE TAX",
                   regime["advance_tax_paid"])
    for label, amount in (
            ("ADD: INTEREST U/S 234A", regime["interest_234a"]),
            ("ADD: INTEREST U/S 234B", regime["interest_234b"]),
            ("ADD: INTEREST U/S 234C", regime["interest_234c"]),
            ("ADD: LATE FEE U/S 234F", regime["fee_234f"])):
        if amount:
            ws.write(r, 0, label, f_cell)
            ws.write_number(r, 2, amount, f_num)
            r += 1
    # Self-assessment tax sits BELOW the 288B-rounded payable, because it is
    # paid against that rounded figure — exactly how the challan was raised.
    if regime["self_assessment_paid"]:
        ws.write(r, 0, "TAX PAYABLE (ROUNDED OFF U/S 288B)", f_total_lbl)
        ws.write_number(r, 2, regime["payable_or_refund"]
                        + regime["self_assessment_paid"], f_total)
        r += 1
        _challan_block("self_assessment", "LESS: SELF ASSESSMENT TAX U/S 140A",
                       regime["self_assessment_paid"])
    payable = regime["payable_or_refund"]
    ws.write(r, 0, "TAX PAYABLE (ROUNDED OFF U/S 288B)" if payable >= 0
             else "REFUND DUE (ROUNDED OFF U/S 288B)", f_total_lbl)
    ws.write_number(r, 2, abs(payable), f_total)
    r += 2

    # --- LOSSES TABLE: what came in, what was absorbed, what goes forward ---
    loss_table = regime.get("loss_table", [])
    if loss_table:
        ws.merge_range(r, 0, r, 3, "LOSSES TABLE", f_center)
        r += 1
        ws.write_row(r, 0, ["A.Y. / Head", "Brought forward", "Set off",
                            "Carried forward"], f_head)
        r += 1
        for row in loss_table:
            kind = row.get("kind")
            ws.write(r, 0, f"{row.get('ay') or '—'} · "
                           f"{_LOSS_LABELS.get(kind, kind)}", f_cell)
            ws.write_number(r, 1, row.get("brought_forward", 0), f_num)
            ws.write_number(r, 2, row.get("set_off", 0), f_num)
            ws.write_number(r, 3, row.get("carried_forward", 0), f_num)
            r += 1
            if row.get("lapsed"):
                ws.write(r, 0, "   lapsed — carry-forward window closed",
                         f_cell)
                ws.write_number(r, 3, -row["lapsed"], f_num)
                r += 1
        r += 1

    # --- capital-gains rate reconciliation (20% indexed vs 12.5% flat) ---
    cg_reco = (regime["heads"].get("capital_gains") or {}).get("reco_lines",
                                                               [])
    if cg_reco:
        ws.merge_range(r, 0, r, 3, "LONG-TERM CAPITAL GAIN — RATE OPTION "
                       "RECONCILIATION", f_center)
        r += 1
        ws.write_row(r, 0, ["Particulars", "Section", "Tax"], f_head)
        r += 1
        for ln in cg_reco:
            ws.write(r, 0, ln["label"], f_cell)
            ws.write(r, 1, ln["section"], f_cell)
            ws.write_number(r, 2, ln["amount"], f_num)
            r += 1
        r += 1

    ws.write(r, 0, f"Regime: {regime_key.upper()} · figures in ₹ · "
                   "prepared for review — validate in the ITD utility "
                   "before filing.", f_note)


def build(*, taxpayer_name: str, pan_masked: str, ay: str,
          result: dict, taxpayer: dict | None = None,
          inputs: dict | None = None) -> bytes:
    """``taxpayer`` (dob/email/address/resident_status) enriches the header
    block and ``inputs`` enables section-wise TDS — both optional so older
    callers keep working."""
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    f_title = wb.add_format({"bold": True, "font_size": 15,
                             "font_color": _ACCENT})
    f_sub = wb.add_format({"font_size": 10, "font_color": "#555555"})
    f_reco = wb.add_format({"bold": True, "font_color": "#1E8449"})
    f_head = wb.add_format({"bold": True, "bg_color": _ACCENT,
                            "font_color": "white", "border": 1,
                            "align": "center"})
    f_cell = wb.add_format({"border": 1})
    f_num = wb.add_format({"border": 1, "num_format": INR_FMT})
    f_total_lbl = wb.add_format({"border": 1, "bold": True,
                                 "bg_color": "#EAF2F8"})
    f_total = wb.add_format({"border": 1, "bold": True, "num_format": INR_FMT,
                             "bg_color": "#EAF2F8"})
    f_group = wb.add_format({"bold": True, "bg_color": "#F4F6F7",
                             "border": 1})
    f_note = wb.add_format({"font_size": 9, "italic": True,
                            "font_color": "#777777"})

    old, new = result["old"], result["new"]

    # ---- Computation sheet (practitioner format: income by head, then
    # tax by slab band + special-rate subheads, TDS section-wise) ----
    _computation_sheet(
        wb, result, regime_key=result.get("recommended", "new"),
        taxpayer_name=taxpayer_name, pan_masked=pan_masked, ay=ay,
        taxpayer=taxpayer or {}, inputs=inputs or {},
        f_title=f_title, f_sub=f_sub, f_head=f_head, f_cell=f_cell,
        f_num=f_num, f_total_lbl=f_total_lbl, f_total=f_total,
        f_group=f_group, f_note=f_note)

    # ---- Summary sheet ----
    ws = wb.add_worksheet("Summary")
    ws.hide_gridlines(2)
    ws.set_column(0, 0, 48)
    ws.set_column(1, 2, 18)
    ws.set_landscape()
    ws.fit_to_pages(1, 1)
    ws.write(0, 0, f"Computation of Income & Tax — AY {ay}", f_title)
    ws.write(1, 0, f"{taxpayer_name}    ·    PAN {pan_masked}", f_sub)
    ws.write(2, 0, f"Recommended regime: {result['recommended'].upper()}  "
                   f"(saves ₹{result.get('saving', 0):,})", f_reco)
    ws.write_row(4, 0, ["Particulars", "Old regime", "New regime"], f_head)
    rows = [
        ("Gross total income", old["gti"], new["gti"], False),
        ("Chapter VI-A deductions", -old["deductions_total"],
         -new["deductions_total"], False),
        ("Total income (288A)", old["total_income"], new["total_income"],
         True),
        ("Exempt income — Schedule EI (not included above)",
         old.get("exempt_total", 0), new.get("exempt_total", 0), False),
        ("Tax at slab rates", old["slab_tax"], new["slab_tax"], False),
        ("Tax on special-rate income", old["special_tax"],
         new["special_tax"], False),
        ("Rebate u/s 87A", -old["rebate_87a"], -new["rebate_87a"], False),
        ("Surcharge (net of marginal relief)",
         old["surcharge"] - old["marginal_relief"],
         new["surcharge"] - new["marginal_relief"], False),
        ("Health & education cess", old["cess"], new["cess"], False),
        ("Total tax liability", old["tax_liability"], new["tax_liability"],
         True),
        ("TDS", -old["tds_total"], -new["tds_total"], False),
        ("Advance / self-assessment tax",
         -(old["advance_tax_paid"] + old["self_assessment_paid"]),
         -(new["advance_tax_paid"] + new["self_assessment_paid"]), False),
        ("Interest u/s 234A/B/C",
         old["interest_234a"] + old["interest_234b"] + old["interest_234c"],
         new["interest_234a"] + new["interest_234b"] + new["interest_234c"],
         False),
        ("Late fee u/s 234F", old["fee_234f"], new["fee_234f"], False),
        ("Net payable / (refund)", old["payable_or_refund"],
         new["payable_or_refund"], True),
    ]
    r = 5
    for label, a, b, is_total in rows:
        ws.write(r, 0, label, f_total_lbl if is_total else f_cell)
        ws.write_number(r, 1, a, f_total if is_total else f_num)
        ws.write_number(r, 2, b, f_total if is_total else f_num)
        r += 1
    ws.freeze_panes(5, 0)
    ws.write(r + 1, 0, "Figures in ₹. Prepared for review — validate against "
                       "the ITD offline utility before filing.", f_note)

    # ---- Schedule sheets (head-wise, per regime) ----
    for regime_key in ("new", "old"):
        regime = result[regime_key]
        sh = wb.add_worksheet(f"Schedule ({regime_key})")
        sh.hide_gridlines(2)
        sh.set_column(0, 0, 62)
        sh.set_column(1, 1, 12)
        sh.set_column(2, 2, 16)
        sh.set_landscape()
        sh.fit_to_pages(1, 1)
        sh.write_row(0, 0, ["Particulars", "Section", "Amount"], f_head)
        sh.freeze_panes(1, 0)
        rr = 1
        for head_key in _HEADS:
            head = regime["heads"].get(head_key) or {}
            head_lines = head.get("lines", [])
            if not head_lines:
                continue
            sh.merge_range(rr, 0, rr, 2, _HEAD_TITLES[head_key], f_group)
            rr += 1
            for ln in head_lines:
                sh.write(rr, 0, ln["label"], f_cell)
                sh.write(rr, 1, ln["section"], f_cell)
                sh.write_number(rr, 2, ln["amount"], f_num)
                rr += 1
        other = (list(regime.get("setoff_lines", []))
                 + list(regime.get("deduction_lines", []))
                 + list(regime.get("special_tax_lines", []))
                 + list(regime.get("lines", [])))
        if other:
            sh.merge_range(rr, 0, rr, 2, "Deductions, special-rate tax & "
                           "adjustments", f_group)
            rr += 1
            for ln in other:
                sh.write(rr, 0, ln["label"], f_cell)
                sh.write(rr, 1, ln["section"], f_cell)
                sh.write_number(rr, 2, ln["amount"], f_num)
                rr += 1
        exempt_lines = regime.get("exempt_lines", [])
        if exempt_lines:
            sh.merge_range(rr, 0, rr, 2, "Exempt income — Schedule EI "
                           "(reported; not part of total income)", f_group)
            rr += 1
            for ln in exempt_lines:
                sh.write(rr, 0, ln["label"], f_cell)
                sh.write(rr, 1, ln["section"], f_cell)
                sh.write_number(rr, 2, ln["amount"], f_num)
                rr += 1

        # ---- Computation of tax: slab bands + special-rate subheads ----
        sh.merge_range(rr, 0, rr, 2, "Computation of tax — slab rates & "
                       "special rates (Income-tax Act, 1961)", f_group)
        rr += 1
        for ln in regime.get("slab_lines", []):
            sh.write(rr, 0, ln["label"], f_cell)
            sh.write(rr, 1, ln["section"], f_cell)
            sh.write_number(rr, 2, ln["amount"], f_num)
            rr += 1
        sh.write(rr, 0, "Tax at slab rates on normal income", f_total_lbl)
        sh.write(rr, 1, "", f_total_lbl)
        sh.write_number(rr, 2, regime["slab_tax"], f_total)
        rr += 1
        for ln in regime.get("special_tax_lines", []):
            sh.write(rr, 0, ln["label"], f_cell)
            sh.write(rr, 1, ln["section"], f_cell)
            sh.write_number(rr, 2, ln["amount"], f_num)
            rr += 1
        tail = [
            ("Tax on special-rate income", regime["special_tax"], False),
            ("Less: rebate u/s 87A", -regime["rebate_87a"], False),
            ("Surcharge (net of marginal relief)",
             regime["surcharge"] - regime["marginal_relief"], False),
            ("Health & education cess", regime["cess"], False),
            ("Total tax liability", regime["tax_liability"], True),
        ]
        for label, amount, is_total in tail:
            sh.write(rr, 0, label, f_total_lbl if is_total else f_cell)
            sh.write(rr, 1, "", f_total_lbl if is_total else f_cell)
            sh.write_number(rr, 2, amount, f_total if is_total else f_num)
            rr += 1

    wb.close()
    return buf.getvalue()
