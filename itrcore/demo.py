"""Public demo mode (e.g. a Streamlit Community Cloud deployment).

Enabled by ITR_PORTAL_DEMO=1. In demo mode the portal:
  * keeps its database and key file in a throwaway temp directory (unless
    ITR_PORTAL_DATA_DIR / ITR_PORTAL_KEY_FILE are set explicitly),
  * seeds the fictional dataset from scripts/seed_demo.py on first boot,
  * uses MockITDPortalClient instead of the live income-tax portal and
    refuses browser-driven filing.

Nothing here imports streamlit (itrcore rule).
"""

import importlib.util
import os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Accounts created by scripts/seed_demo.py: (username, password, role, blurb).
DEMO_ACCOUNTS = (
    ("partner", "partner123", "Partner",
     "Firm administrator: every client, filing and the audit trail"),
    ("staff1", "staff1234", "Staff",
     "Preparer: workpapers, reconciliation and computation"),
    ("client_ramesh", "client123", "Client",
     "Taxpayer self-service: upload documents, answer queries"),
)


def is_demo() -> bool:
    return os.environ.get("ITR_PORTAL_DEMO", "").strip().lower() in {
        "1", "true", "yes", "on"}


def forbid_in_demo(action: str) -> None:
    """Shared demo accounts must stay usable by every visitor."""
    if is_demo():
        raise PermissionError(f"{action} is disabled in the public demo.")


def prepare_environment() -> None:
    """Point data + keys at a temp dir. Call before any database access."""
    if not is_demo():
        return
    base = Path(tempfile.gettempdir()) / "itr-portal-demo"
    os.environ.setdefault("ITR_PORTAL_DATA_DIR", str(base / "data"))
    os.environ.setdefault("ITR_PORTAL_KEY_FILE", str(base / "keys.json"))


def seed_if_empty() -> bool:
    """Seed the fictional demo dataset once. Returns True if it seeded."""
    from itrcore.db.repos import users

    if users.user_count():
        return False
    spec = importlib.util.spec_from_file_location(
        "itr_seed_demo", PROJECT_ROOT / "scripts" / "seed_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
    return True
