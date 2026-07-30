"""Architecture enforcement: cheap tests keeping the security model
honest."""

import inspect
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Repo functions that legitimately take neither scope nor principal.
_SIGNATURE_WHITELIST = {
    "authenticate", "user_count", "hash_password", "verify_password",
    "load_template", "applicable_items", "txn_from_json",
}


def test_itrcore_never_imports_streamlit():
    """Import every itrcore module in a clean interpreter; streamlit must
    not be pulled in transitively."""
    code = (
        "import pkgutil, sys, itrcore\n"
        "for m in pkgutil.walk_packages(itrcore.__path__, 'itrcore.'):\n"
        "    __import__(m.name)\n"
        "assert 'streamlit' not in sys.modules, 'itrcore imported streamlit'\n"
        "print('clean')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "clean" in proc.stdout


def test_repo_functions_take_scope_or_principal():
    import importlib
    import pkgutil

    import itrcore.db.repos as repos_pkg

    bad = []
    for mod_info in pkgutil.iter_modules(repos_pkg.__path__):
        mod = importlib.import_module(f"itrcore.db.repos.{mod_info.name}")
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != mod.__name__:
                continue
            if name in _SIGNATURE_WHITELIST:
                continue
            params = list(inspect.signature(fn).parameters)
            if not params or params[0] not in ("scope", "principal"):
                bad.append(f"{mod.__name__}.{name}({params})")
    assert not bad, ("repo functions must take scope/principal first: "
                     + ", ".join(bad))


def test_every_page_calls_guard_before_streamlit():
    pages = list((PROJECT_ROOT / "app_pages").rglob("*.py"))
    assert pages, "no pages found"
    for page in pages:
        text = page.read_text(encoding="utf-8")
        guard_at = text.find("guard.page(")
        st_at = text.find("import streamlit")
        assert guard_at != -1, f"{page.name}: missing guard.page()"
        assert st_at == -1 or guard_at < st_at, (
            f"{page.name}: guard.page() must run before streamlit is used")


def test_client_navigation_never_reaches_staff_pages():
    sys.path.insert(0, str(PROJECT_ROOT))
    from itrapp.nav import page_specs

    client = [p for pages in page_specs("Client").values() for p in pages]
    assert client, "client nav is empty"
    for rel, *_ in client:
        assert rel.startswith("client/"), f"client nav leaks {rel}"

    for role in ("Staff", "Manager", "Partner"):
        staff = [p for pages in page_specs(role).values() for p in pages]
        for rel, *_ in staff:
            assert rel.startswith("staff/"), f"{role} nav leaks {rel}"

    partner_paths = {p[0] for pages in page_specs("Partner").values()
                     for p in pages}
    staff_paths = {p[0] for pages in page_specs("Staff").values()
                   for p in pages}
    assert "staff/admin.py" in partner_paths
    assert "staff/admin.py" not in staff_paths
