"""Streamlit AppTest smoke: the app boots to the bootstrap/login screen."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_app_boots_to_first_run_screen(fresh_db):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"),
                          default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    # First run → Partner bootstrap form is shown
    assert any("First run" in str(block.value) for block in at.info), \
        "expected the first-run bootstrap notice"
