"""Role-filtered navigation.

page_specs() is pure and unit-tested: role → sections → (file, title, icon,
default). Forbidden pages are simply never registered, which makes their
URLs 404 — never rely on visibility="hidden" for access control.
"""

from pathlib import Path

import streamlit as st

from itrapp.auth import AuthContext

PAGES_DIR = Path(__file__).resolve().parents[1] / "app_pages"

_CLIENT_PAGES = [
    ("client/home.py", "Home", "🏠", True),
    ("client/upload.py", "Upload documents", "📤", False),
    ("client/my_queries.py", "My queries", "❓", False),
    ("client/my_return.py", "My return", "📄", False),
]

_STAFF_SECTIONS = [
    ("Clients", [
        ("staff/dashboard.py", "Dashboard", "📊", True),
        ("staff/taxpayers.py", "Clients", "👥", False),
        ("staff/onboarding.py", "Onboarding", "🆕", False),
        ("staff/documents.py", "Documents & checklist", "🗂️", False),
    ]),
    ("Workpapers", [
        ("staff/statements.py", "Bank statements", "🏦", False),
        ("staff/review_txns.py", "Review transactions", "🔎", False),
        ("staff/queries.py", "Client queries", "❓", False),
        ("staff/computation.py", "Computation", "🧮", False),
        ("staff/reconciliation.py", "Reconciliation", "⚖️", False),
    ]),
    ("Filing", [
        ("staff/filing.py", "ITR generation & filing", "📨", False),
    ]),
]

_ADMIN_SECTION = ("Admin", [
    ("staff/admin.py", "Users & audit", "🔐", False),
])


def page_specs(role: str) -> dict[str, list[tuple[str, str, str, bool]]]:
    """Pure mapping used by build() and by the role-separation unit test."""
    if role == "Client":
        return {"My tax return": list(_CLIENT_PAGES)}
    sections = {name: list(pages) for name, pages in _STAFF_SECTIONS}
    if role == "Partner":
        sections[_ADMIN_SECTION[0]] = list(_ADMIN_SECTION[1])
    return sections


def build(ctx: AuthContext) -> dict[str, list]:
    specs = page_specs(ctx.principal.role)
    out: dict[str, list] = {}
    for section, pages in specs.items():
        out[section] = [
            st.Page(str(PAGES_DIR / rel), title=title, icon=icon,
                    default=default)
            for rel, title, icon, default in pages
        ]
    return out
