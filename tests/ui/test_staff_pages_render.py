"""AppTest smoke: staff pages render for a logged-in staff session with data
imported. Exercises the assessee header, the auto-fill panel and the AIS/TIS
upload UI — the app-boot test only reaches the login screen, so without this
a page-body error slips through pytest.
"""

from pathlib import Path

import pytest

from itrapp.auth import ACTIVE_TAXPAYER_KEY, AuthContext, SESSION_KEY
from itrcore.authz import mint_scope
from itrcore.constants import CURRENT_AY
from itrcore.db.repos import importers
from itrcore.ingest import tis as tis_mod

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Every staff page carrying the assessee context header.
STAFF_PAGES = ["dashboard.py", "statements.py", "review_txns.py",
               "queries.py", "computation.py", "reconciliation.py",
               "documents.py", "filing.py", "onboarding.py", "taxpayers.py"]

_TIS_JSON = (b'{"tisPartBDetails":[{"infoCategory":"Interest from savings '
             b'bank","processedValue":12000,"derivedValue":15000}]}')


def _seed(partner, taxpayer_id):
    scope = mint_scope(partner, taxpayer_id)
    importers.save_form16(scope, CURRENT_AY, employer_name="Acme Ltd",
                          employer_tan="MUMA12345B",
                          gross_salary_paise=1500000_00,
                          exempt_allowances_paise=50000_00,
                          prof_tax_paise=2500_00, tds_paise=120000_00)
    importers.import_ais(scope, CURRENT_AY, [
        {"category": "interest_savings", "description": "SB",
         "amount_paise": 12000_00}], "ais.json")
    importers.import_tis(scope, CURRENT_AY,
                         tis_mod.parse_tis_json(_TIS_JSON), "tis.json")


def _render(page: str, partner, taxpayer_id):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(
        str(PROJECT_ROOT / "app_pages" / "staff" / page), default_timeout=60)
    at.session_state[SESSION_KEY] = AuthContext(principal=partner)
    at.session_state[ACTIVE_TAXPAYER_KEY] = taxpayer_id
    at.run()
    assert not at.exception, at.exception


@pytest.mark.parametrize("page", STAFF_PAGES)
def test_staff_page_renders_with_header(page, partner, taxpayer_id):
    _seed(partner, taxpayer_id)
    _render(page, partner, taxpayer_id)
