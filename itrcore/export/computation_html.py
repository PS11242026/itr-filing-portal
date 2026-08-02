"""Self-contained HTML computation sheet (also the print/preview view)."""

from html import escape

from itrcore.export.format import inr

_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:2rem auto;max-width:900px;
     color:#1a1a1a}
h1{font-size:1.4rem;border-bottom:2px solid #1a5276;padding-bottom:.4rem}
h2{font-size:1.1rem;margin-top:1.6rem;color:#1a5276}
table{border-collapse:collapse;width:100%;margin:.6rem 0}
th,td{border:1px solid #cbd5e0;padding:.35rem .6rem;font-size:.92rem}
th{background:#eef3f7;text-align:left}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr.total td{font-weight:700;background:#f7fafc}
.badge{display:inline-block;background:#1a5276;color:#fff;padding:.15rem .6rem;
       border-radius:4px;font-size:.85rem}
.disclaimer{margin-top:2rem;font-size:.8rem;color:#555;border-top:1px solid
            #cbd5e0;padding-top:.6rem}
"""


def _row(label: str, a: int | None, b: int | None,
         total: bool = False) -> str:
    cls = ' class="total"' if total else ""
    fa = inr(a) if a is not None else "—"
    fb = inr(b) if b is not None else "—"
    return (f"<tr{cls}><td>{escape(label)}</td>"
            f'<td class="num">{fa}</td><td class="num">{fb}</td></tr>')


def _schedule_table(regime: dict) -> str:
    rows = []
    for head_key in ("salary", "house_property", "business", "speculation",
                     "capital_gains", "other_sources"):
        head = regime["heads"].get(head_key)
        if not head:
            continue
        lines = head.get("lines", [])
        if not lines:
            continue
        rows.append(f'<tr><th colspan="3">'
                    f'{escape(head_key.replace("_", " ").title())}</th></tr>')
        for ln in lines:
            rows.append(
                f"<tr><td>{escape(ln['label'])}</td>"
                f"<td>{escape(ln['section'])}</td>"
                f'<td class="num">{inr(ln["amount"])}</td></tr>')
    if regime.get("setoff_lines"):
        rows.append('<tr><th colspan="3">Set off &amp; carry forward of '
                    'losses (Sec 70–74)</th></tr>')
        for ln in regime["setoff_lines"]:
            rows.append(f"<tr><td>{escape(ln['label'])}</td>"
                        f"<td>{escape(ln['section'])}</td>"
                        f'<td class="num">{inr(ln["amount"])}</td></tr>')
    for ln in regime.get("deduction_lines", []):
        rows.append(f"<tr><td>{escape(ln['label'])}</td>"
                    f"<td>{escape(ln['section'])}</td>"
                    f'<td class="num">{inr(ln["amount"])}</td></tr>')
    for ln in regime.get("special_tax_lines", []) + list(
            regime.get("lines", [])):
        rows.append(f"<tr><td>{escape(ln['label'])}</td>"
                    f"<td>{escape(ln['section'])}</td>"
                    f'<td class="num">{inr(ln["amount"])}</td></tr>')
    if regime.get("exempt_lines"):
        rows.append('<tr><th colspan="3">Exempt income — Schedule EI '
                    "(reported; not part of total income)</th></tr>")
        for ln in regime["exempt_lines"]:
            rows.append(f"<tr><td>{escape(ln['label'])}</td>"
                        f"<td>{escape(ln['section'])}</td>"
                        f'<td class="num">{inr(ln["amount"])}</td></tr>')
    rows.append('<tr><th colspan="3">Computation of tax — slab rates '
                "&amp; special rates</th></tr>")
    for ln in regime.get("slab_lines", []):
        rows.append(f"<tr><td>{escape(ln['label'])}</td>"
                    f"<td>{escape(ln['section'])}</td>"
                    f'<td class="num">{inr(ln["amount"])}</td></tr>')
    for label, amount, total in (
            ("Tax at slab rates on normal income", regime["slab_tax"],
             True),
            ("Tax on special-rate income", regime["special_tax"], False),
            ("Less: rebate u/s 87A", -regime["rebate_87a"], False),
            ("Surcharge (net of marginal relief)",
             regime["surcharge"] - regime["marginal_relief"], False),
            ("Health & education cess", regime["cess"], False),
            ("Total tax liability", regime["tax_liability"], True)):
        cls = ' class="total"' if total else ""
        rows.append(f"<tr{cls}><td>{escape(label)}</td><td></td>"
                    f'<td class="num">{inr(amount)}</td></tr>')
    return ("<table><tr><th>Particulars</th><th>Section</th>"
            "<th>Amount</th></tr>" + "".join(rows) + "</table>")


def render(*, taxpayer_name: str, pan_masked: str, ay: str,
           result: dict) -> str:
    old, new = result["old"], result["new"]
    rec = result["recommended"]
    body = [
        f"<h1>Computation of Total Income &amp; Tax — AY {escape(ay)}</h1>",
        f"<p><b>{escape(taxpayer_name)}</b> · PAN {escape(pan_masked)} · "
        f'Recommended regime: <span class="badge">{rec.upper()}</span> '
        f"(saves {inr(result['saving'])})</p>",
        "<h2>Summary</h2>",
        "<table><tr><th>Particulars</th><th>Old regime</th>"
        "<th>New regime (115BAC)</th></tr>",
        _row("Gross total income", old["gti"], new["gti"]),
        _row("Chapter VI-A deductions", -old["deductions_total"],
             -new["deductions_total"]),
        _row("Total income (288A)", old["total_income"],
             new["total_income"], total=True),
        _row("Exempt income — Schedule EI (not included above)",
             old.get("exempt_total", 0), new.get("exempt_total", 0)),
        _row("Tax at slab rates", old["slab_tax"], new["slab_tax"]),
        _row("Tax on special-rate income", old["special_tax"],
             new["special_tax"]),
        _row("Less: rebate u/s 87A", -old["rebate_87a"],
             -new["rebate_87a"]),
        _row("Surcharge (net of marginal relief)",
             old["surcharge"] - old["marginal_relief"],
             new["surcharge"] - new["marginal_relief"]),
        _row("Health & education cess (4%)", old["cess"], new["cess"]),
        _row("Total tax liability", old["tax_liability"],
             new["tax_liability"], total=True),
        _row("TDS", -old["tds_total"], -new["tds_total"]),
        _row("Advance tax / self-assessment",
             -(old["advance_tax_paid"] + old["self_assessment_paid"]),
             -(new["advance_tax_paid"] + new["self_assessment_paid"])),
        _row("Interest 234A/234B/234C",
             old["interest_234a"] + old["interest_234b"]
             + old["interest_234c"],
             new["interest_234a"] + new["interest_234b"]
             + new["interest_234c"]),
        _row("Late fee 234F", old["fee_234f"], new["fee_234f"]),
        _row("Net payable / (refund) (288B)", old["payable_or_refund"],
             new["payable_or_refund"], total=True),
        "</table>",
        f"<h2>Detailed schedule — {rec} regime</h2>",
        _schedule_table(result[rec]),
        '<p class="disclaimer">Prepared with the firm\'s ITR portal '
        f"(engine {escape(result['engine_version'])}, config "
        f"{escape(result['config_hash'])}). This computation is subject to "
        "review and sign-off by a Chartered Accountant before filing. "
        "Figures are in whole rupees.</p>",
    ]
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Computation {escape(taxpayer_name)} AY {escape(ay)}"
            f"</title><style>{_CSS}</style></head><body>"
            + "".join(body) + "</body></html>")
