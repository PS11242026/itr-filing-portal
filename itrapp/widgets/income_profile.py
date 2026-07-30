"""Income profile: the five heads of income (overheads) with their
subheads, rendered as click-to-expand groups — clicking an overhead like
'Profits & gains of business or profession' reveals the subheads clubbed
under it (44AD / 44ADA / 44AE / regular books).

Flag KEYS are stable and shared with the document checklist and the
computation-tab highlighting — only the presentation is grouped here.
"""

import streamlit as st

PROFILE_GROUPS: tuple = (
    ("💰 Salaries", (
        ("has_salary", "Salary / pension income"),
    )),
    ("🏠 Income from house property", (
        ("has_let_out_property", "Let-out house property"),
        ("has_home_loan", "Home loan (interest u/s 24(b))"),
    )),
    ("💼 Profits & gains of business or profession", (
        ("has_business_44ad", "Business — presumptive u/s 44AD"),
        ("has_profession_44ada", "Profession — presumptive u/s 44ADA"),
        ("has_goods_carriage_44ae", "Goods carriage u/s 44AE"),
        ("has_business_books",
         "Business/profession — regular books of accounts (BS & P&L "
         "workbook import + reconciliation)"),
    )),
    ("📈 Capital gains", (
        ("has_property_sale", "Sold immovable property this year"),
        ("has_securities_cg", "Sold shares / mutual funds"),
    )),
    ("🏦 Income from other sources", (
        ("has_interest_income", "Interest income (savings / FD)"),
        ("has_dividend_income", "Dividend income"),
        ("has_gifts", "Received gifts above ₹50,000"),
    )),
    ("⚙️ Filing flags", (
        ("wants_old_regime", "Evaluate old regime (collect VI-A proofs)"),
        ("is_director", "Director in a company (ITR-2)"),
        ("has_unlisted_shares", "Holds unlisted equity shares (ITR-2)"),
        ("has_foreign_assets", "Foreign assets / income (ITR-2)"),
    )),
)

ALL_FLAGS: tuple = tuple(flag for _, flags in PROFILE_GROUPS
                         for flag, _ in flags)


def render(profile: dict, *, key_prefix: str = "") -> dict:
    """Overhead expanders with subhead checkboxes; returns the new profile
    dict. Overheads with saved selections open pre-expanded, the rest
    reveal their subheads on click."""
    new_profile: dict = {}
    for title, flags in PROFILE_GROUPS:
        selected = sum(1 for flag, _ in flags if profile.get(flag))
        label = f"{title} · {selected} selected" if selected else title
        with st.expander(label, expanded=bool(selected)):
            for flag, text in flags:
                new_profile[flag] = st.checkbox(
                    text, value=bool(profile.get(flag)),
                    key=f"{key_prefix}{flag}")
    return new_profile
