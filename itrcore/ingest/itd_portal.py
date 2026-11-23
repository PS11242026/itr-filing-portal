"""ITD e-filing portal connector: pull AIS / TIS / 26AS with a client's
credentials (PAN + password), the way an authorised intermediary does.

Same swappable pattern as ``itrcore/efile`` (protocol + Mock + live):

- ``ITDPortalClient``  — the interface the app depends on.
- ``MockITDPortalClient`` — returns sample payloads so the whole
  pull -> parse -> import -> auto-fill -> compute workflow runs and demos
  WITHOUT touching the live portal. Every value it returns is labelled MOCK.
- ``SeleniumITDPortalClient`` — the live, browser-driven implementation. It
  launches a real Chrome on the practitioner's machine, PRE-FILLS the client's
  PAN + password from the vault, then hands the browser to a human ONLY for the
  CAPTCHA + OTP (which are, by design, never solved or intercepted). Once the
  human is past the challenge the connector drives the rest automatically:
  AIS/TIS download from the compliance portal and Form 26AS from TRACES.

The live client returns RAW payloads exactly as the portal serves them (the AIS
JSON is the ENCRYPTED envelope, the 26AS is a PDF); the existing parsers
(``ais`` / ``tis`` / ``form26as_pdf``) turn them into entries, so there is ONE
parsing path whether data arrives by upload or by pull.

Boundary held throughout: CAPTCHA and OTP are completed by a human in the live
browser. This connector never solves a CAPTCHA and never reads or intercepts an
OTP.
"""

from dataclasses import dataclass, field
from typing import Protocol

from itrcore.authz import TaxpayerScope
from itrcore.db import auditlog
from itrcore.db.repos import importers as importers_repo
from itrcore.db.repos import portal_credentials as creds_repo
from itrcore.db.repos import taxpayers as taxpayers_repo
from itrcore.ingest import ais as ais_mod
from itrcore.ingest import ais_crypto
from itrcore.ingest import form26as as form26as_mod
from itrcore.ingest import form26as_pdf as form26as_pdf_mod
from itrcore.ingest import tis as tis_mod

# ITD endpoints (public).
LOGIN_URL = "https://eportal.incometax.gov.in/iec/foservices/#/login"
DASHBOARD_HINT = "/dashboard"          # URL fragment once login succeeds
COMPLIANCE_HINT = "ais.insight.gov.in"  # AIS/TIS live on the compliance portal


class PortalError(RuntimeError):
    """Login/download failed, or the live connector could not run."""


@dataclass(frozen=True)
class PortalPull:
    """Raw payloads as served by the portal (any may be None).

    ``ais_json`` is the ENCRYPTED AIS envelope from the compliance portal (the
    live path) OR already-plain sample JSON (the mock). ``as26_pdf`` is the
    TRACES Annual Tax Statement PDF (live path); ``as26_text`` is the older
    text/CSV form (mock / manual)."""

    ais_json: bytes | None = None
    tis_json: bytes | None = None
    as26_pdf: bytes | None = None
    as26_text: bytes | None = None
    messages: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ResetOutcome:
    """A Forgot-Password step result. status is 'otp_sent' (the portal sent an
    OTP to the client's registered/Aadhaar-linked mobile - relay it back),
    'done', or 'error'. The OTP is always completed by a human."""

    status: str
    message: str = ""


class ITDPortalClient(Protocol):
    def pull(self, *, pan: str, password: str, ay: str,
             otp: str | None = None) -> PortalPull: ...

    def reset_password(self, *, pan: str, new_password: str | None = None,
                       otp: str | None = None) -> ResetOutcome: ...


# --------------------------------------------------------------- mock client
_MOCK_AIS = (
    b'{"data":['
    b'{"infoDesc":"Interest from savings bank","amount":18500},'
    b'{"infoDesc":"Interest from deposit","amount":62000},'
    b'{"infoDesc":"Dividend","amount":9400},'
    b'{"infoDesc":"Sale of securities and units","amount":350000}]}')

_MOCK_TIS = (
    b'{"tisPartBDetails":['
    b'{"infoCategory":"Interest from savings bank","processedValue":18500,'
    b'"derivedValue":18500},'
    b'{"infoCategory":"Interest from deposit","processedValue":62000,'
    b'"derivedValue":64000},'
    b'{"infoCategory":"Dividend","processedValue":9400,'
    b'"derivedValue":9400}]}')

_MOCK_26AS = (
    b"STATE BANK OF INDIA^SBIN12345A^194A^62000.00^6200.00\n"
    b"CONTRACTOR PAYER^DELC98765F^194C^200000.00^2000.00\n")


class MockITDPortalClient:
    """Sample data so the full workflow runs without the live portal."""

    def pull(self, *, pan: str, password: str, ay: str,
             otp: str | None = None) -> PortalPull:
        if not pan or not password:
            raise PortalError("PAN and password are required")
        return PortalPull(
            ais_json=_MOCK_AIS, tis_json=_MOCK_TIS, as26_text=_MOCK_26AS,
            messages=("MOCK pull - sample AIS/TIS/26AS, NOT fetched from the "
                      "live ITD portal. Use SeleniumITDPortalClient for a "
                      "real browser-driven pull.",))

    def reset_password(self, *, pan: str, new_password: str | None = None,
                       otp: str | None = None) -> ResetOutcome:
        if not pan:
            raise PortalError("PAN is required")
        if otp is None:
            return ResetOutcome("otp_sent", "MOCK - an OTP would now be sent "
                                "to the client's registered mobile. Enter it "
                                "with a new password to complete.")
        if not new_password:
            return ResetOutcome("error", "A new password is required.")
        return ResetOutcome("done", "MOCK - password reset (not on the live "
                            "portal). Stored in the vault.")


# ------------------------------------------------------- live browser driver
class PortalBrowser(Protocol):
    """The browser operations the live client needs. Abstracted so the pull
    orchestration is testable with a fake, and the real Selenium driver is one
    swappable implementation."""

    def open_and_prefill_login(self, *, pan: str, password: str) -> None: ...

    def wait_until_logged_in(self, timeout: float) -> None: ...

    def download_ais_json(self, timeout: float) -> bytes: ...

    def download_26as_pdf(self, *, ay: str, timeout: float) -> bytes: ...

    def upload_itr_json(self, *, json_path: str, ay: str,
                        timeout: float) -> list[str]: ...

    def open_forgot_password(self, *, pan: str) -> list[str]: ...

    def quit(self) -> None: ...


class SeleniumPortalBrowser:
    """Live Chrome driver for the ITD e-filing + compliance portals.

    Non-headless on purpose: the human needs to see and complete the CAPTCHA
    and OTP. We pre-fill PAN + password, then poll until the dashboard appears
    (i.e. the human cleared the challenge), then automate the downloads. Files
    land in ``download_dir``; each ``download_*`` waits for the new file and
    returns its bytes.

    Selectors were calibrated against the live portal (2026-07). The AIS/TIS
    path is confirmed working. The 26AS leg was re-calibrated from live
    diagnostic dumps (2026-07-22): the e-filing side is evidence-based
    (contains-matching, overlay-aware, loud failures with a step trace);
    the TRACES side follows the standard View-Tax-Credit flow and reports
    exactly which step failed if its markup differs.
    """

    def __init__(self, download_dir: str, *, headless: bool = False,
                 retain_dir: str | None = None) -> None:
        # A FRESH throwaway profile every pull (Selenium's default) - proven
        # reliable. ``download_dir`` is a fresh per-pull dir so "new file"
        # detection is unambiguous; files are copied to ``retain_dir`` to keep
        # the raw AIS JSON / 26AS PDF.
        self.download_dir = download_dir
        self.retain_dir = retain_dir
        self.headless = headless
        self._driver = None

    # -- lifecycle ---------------------------------------------------------
    def _ensure_driver(self):
        if self._driver is not None:
            return self._driver
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError as exc:                       # pragma: no cover
            raise PortalError(
                "the browser-driven pull needs Selenium - run "
                "`pip install selenium` (>=4.20) in the app's environment, "
                "then try again.") from exc
        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_experimental_option("prefs", {
            "download.default_directory": self.download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "profile.default_content_setting_values.automatic_downloads": 1,
        })
        # Anti-automation-detection: a headful driver with navigator.webdriver
        # set (and the 'controlled by automated software' infobar) makes the
        # ITD portal fire a JS prompt ("Please enter the key :"). These flags
        # remove that signature so login proceeds normally; the capability is a
        # safety net so any stray prompt is dismissed instead of crashing.
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.set_capability("unhandledPromptBehavior", "dismiss")
        opts.add_argument("--start-maximized")
        try:
            self._driver = webdriver.Chrome(options=opts)
        except Exception as exc:                          # pragma: no cover
            raise PortalError(
                "could not launch Chrome for the pull - make sure Google "
                f"Chrome is installed. Underlying error: {exc}") from exc
        return self._driver

    def _wait(self, timeout: float):
        from selenium.webdriver.support.ui import WebDriverWait
        return WebDriverWait(self._ensure_driver(), timeout)

    # -- login -------------------------------------------------------------
    def open_and_prefill_login(self, *, pan: str, password: str) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        d = self._ensure_driver()
        d.get(LOGIN_URL)
        self._dismiss_stray_alert()
        # Step 1: user id (PAN) -> Continue. The field is #panAdhaarUserId.
        uid = self._wait(60).until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "input#panAdhaarUserId, "
                              "input[formcontrolname='panAdhaarUserId'], "
                              "input[type='text']")))
        uid.clear()
        uid.send_keys(pan)
        self._click_button_text("Continue")
        # Step 2: password field appears -> pre-fill, leave Continue to human
        # (the secure-access checkbox + CAPTCHA/OTP are the human's step).
        pw = self._wait(60).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='password'], "
                              "input[formcontrolname='password']")))
        pw.clear()
        pw.send_keys(password)

    def wait_until_logged_in(self, timeout: float) -> None:
        """Block until the human has cleared CAPTCHA/OTP and the dashboard is
        reached. This is the only human-in-the-loop moment.

        While polling, auto-pass the "Dual Login Detected" modal (a session
        conflict when the same PAN is logged in elsewhere - clicking "Login
        Here" just continues our login, which the user authorised) and dismiss
        any stray JS alert."""
        import time
        from selenium.webdriver.common.by import By
        d = self._ensure_driver()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if DASHBOARD_HINT in (d.current_url or ""):
                    return
            except Exception:
                pass
            for el in d.find_elements(
                    By.XPATH, "//button[normalize-space()='Login Here']"):
                try:
                    if el.is_displayed():
                        el.click()
                except Exception:
                    pass
            self._dismiss_stray_alert()
            time.sleep(1)
        raise PortalError(
            "timed out waiting for login to complete - the CAPTCHA/OTP must "
            "be entered in the opened browser window within the time limit.")

    # -- AIS / TIS (compliance portal) ------------------------------------
    def download_ais_json(self, timeout: float) -> bytes:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        d = self._ensure_driver()
        before = _dir_snapshot(self.download_dir)
        try:
            # 'AIS' in the top nav opens a NEW tab on the compliance portal.
            self._click_link_text("AIS")
            self._wait(timeout).until(lambda drv: len(drv.window_handles) > 1)
            d.switch_to.window(d.window_handles[-1])
            self._wait(timeout).until(EC.url_contains(COMPLIANCE_HINT))
            # The compliance portal may show an announcement modal on load
            # whose cdk backdrop intercepts clicks - close it, wait for clear.
            self._dismiss_overlays()
            self._wait_no_backdrop(timeout=15)
            # 'Download AIS/TIS (F.Y. ...)' -> dialog with 3 Download buttons.
            self._click_button_contains("Download AIS/TIS")
            dialog = self._wait(timeout).until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, "div[role='dialog'], "
                                      "mat-dialog-container, .modal-content")))
            # AIS-JSON is the 2nd 'Download' (AIS-PDF, AIS-JSON, TIS-PDF).
            buttons = dialog.find_elements(
                By.XPATH, ".//button[normalize-space()='Download']")
            target = _json_row_button(buttons) or (
                buttons[1] if len(buttons) > 1 else None)
            if target is None:
                raise PortalError(
                    "AIS download dialog did not show the expected Download "
                    "buttons - the compliance portal markup may have changed.")
            self._robust_click(target)
            path = _wait_for_new_file(self.download_dir, before, (".json",),
                                      timeout)
        except Exception as exc:
            diag = self._dump_diagnostic("ais")
            raise PortalError(
                f"AIS/TIS download did not complete ({_short_err(exc)}). "
                f"Diagnostic saved to {diag}*.") from exc
        return self._read_retained(path)

    # -- Form 26AS (TRACES) -----------------------------------------------
    def download_26as_pdf(self, *, ay: str, timeout: float) -> bytes:
        """Drive e-File -> Income Tax Return(s) -> View Form 26AS -> TRACES
        -> View Tax Credit -> AY + HTML -> View/Download -> Export as PDF.

        Re-calibrated from live diagnostic dumps (2026-07-22): the original
        exact-text menu clicks all missed (this portal build labels the
        submenu 'Income Tax Return', singular, and lazily renders items into
        an overlay AFTER the parent opens) and were silently swallowed, so
        the flow never left the post-login page. Now every step matches by
        case-insensitive CONTAINS across anchors/buttons/inputs/menuitems,
        WAITS for the target to render, records a trace, and required steps
        raise loudly - a failure reports exactly how far it got."""
        d = self._ensure_driver()
        # Return to the e-filing tab (handle 0 = the login tab).
        d.switch_to.window(d.window_handles[0])
        before = _dir_snapshot(self.download_dir)
        trace: list[str] = []
        try:
            handles_before = set(d.window_handles)
            self._open_view_26as(trace)
            self._switch_to_traces(handles_before, trace)
            self._traces_export_pdf(ay, trace)
            path = _wait_for_new_file(self.download_dir, before, (".pdf",),
                                      min(timeout, 90))
        except Exception as exc:
            diag = self._dump_diagnostic("traces_26as")
            raise PortalError(
                "26AS auto-download did not complete "
                f"({_short_err(exc)}). Steps: "
                f"{' -> '.join(trace) or 'none'}. A diagnostic snapshot "
                f"(screenshots + HTML of each tab) was saved to {diag}* "
                "for calibration.") from exc
        return self._read_retained(path)

    def _open_view_26as(self, trace: list[str]) -> None:
        """e-filing side: reach the View Form 26AS entry, then the
        redirect-to-TRACES disclaimer.

        The 2026-07-22 diagnostic dumps proved the top-nav dropdowns are
        LAZY: not a single '26AS' node exists in the DOM until a menu is
        physically opened, and a plain click did not open them. So: (1) a
        hover-driven menu walk (hover opens both hover- and click-menus'
        CSS state; click as secondary), then (2) a static-DOM fallback via
        the footer 'Site Map' page, which lists every destination as a
        real link. Failures dump the sitemap DOM - one-shot calibration."""
        # _try_menu_path NEVER raises (stale menu re-renders are retried
        # internally) so the sitemap fallback always gets its turn.
        if self._try_menu_path(trace):
            pass
        elif not self._try_sitemap_path(trace):
            raise PortalError(
                "could not reach 'View Form 26AS' via the e-File menu or "
                "the Site Map page - the portal layout may have changed")
        # Live calibration 2026-07-23: the current build opens the TRACES
        # tab DIRECTLY - no disclaimer dialog. Kept as a cheap best-effort
        # for older builds.
        self._click_by_text(("confirm", "proceed"), timeout=2,
                            trace=trace, step="disclaimer Confirm (legacy)")

    def _try_menu_path(self, trace: list[str]) -> bool:
        """Hover-open e-File -> Income Tax Returns -> click the 26AS item.

        Confirmed by live observation (2026-07-23): the top-nav menus open
        on HOVER (Material buttons; plain clicks never rendered the lazy
        items - the root cause of every earlier failure). The real path is
        e-File -> 'Income Tax Returns' -> 'View Form 26AS, Income Tax Act
        1961' (a sibling 'View Form 168, Income Tax Act 2025' exists for
        the new Act - keep matching on '26as').

        Angular RE-RENDERS the overlay while menus open (first live pull
        died on a StaleElementReferenceException mid-walk), so the whole
        walk retries up to 3 times with fresh finds and NEVER lets an
        exception escape - the sitemap fallback must always get its turn."""
        import time
        from selenium.common.exceptions import StaleElementReferenceException
        for attempt in (1, 2, 3):
            try:
                top = self._find_clickable(
                    ("e-file",), timeout=10 if attempt == 1 else 3)
                if top is None:
                    trace.append("e-File menu: NOT FOUND")
                    return False
                self._hover(top)
                time.sleep(0.8)
                sub = self._find_clickable(("income tax return",),
                                           timeout=3)
                if sub is None:               # click-open menu variant
                    self._robust_click(top)
                    trace.append(f"e-File menu: clicked (attempt {attempt})")
                    sub = self._find_clickable(("income tax return",),
                                               timeout=5)
                else:
                    trace.append("e-File menu: opened on hover "
                                 f"(attempt {attempt})")
                if sub is not None:
                    self._hover(sub)
                    time.sleep(0.8)
                    item = self._find_clickable(("26as",), timeout=3)
                    if item is None:
                        self._robust_click(sub)
                        trace.append("Income Tax Returns submenu: clicked")
                        item = self._find_clickable(("26as",), timeout=5)
                    else:
                        trace.append("Income Tax Returns submenu: opened "
                                     "on hover")
                else:
                    trace.append("Income Tax Returns submenu: NOT FOUND")
                    item = self._find_clickable(("26as",), timeout=3)
                if item is None:
                    trace.append("View Form 26AS item: NOT FOUND "
                                 f"(attempt {attempt})")
                    continue                  # reopen menus from scratch
                self._robust_click(item)
                trace.append("View Form 26AS item: clicked")
                return True
            except StaleElementReferenceException:
                trace.append(f"menu re-rendered mid-walk (attempt "
                             f"{attempt}) - retrying")
                time.sleep(1.0)
            except Exception as exc:          # any other surprise → sitemap
                trace.append(f"menu walk error: {_short_err(exc)}")
                return False
        return False

    def _try_sitemap_path(self, trace: list[str]) -> bool:
        """Static-DOM route: footer 'Site Map' lists every portal page as a
        real link - no lazy menus involved. Click it, then the 26AS link."""
        import time
        if not self._click_by_text(("site map",), timeout=self._SHORT,
                                   trace=trace, step="footer Site Map"):
            return False
        time.sleep(2.0)                       # SPA route render
        return self._click_by_text(("26as",), timeout=15, trace=trace,
                                   step="Site Map 26AS link")

    def _hover(self, element) -> None:
        """Move the pointer onto an element (opens hover menus). Real
        pointer move first, then JS-dispatched pointer/mouse events as
        reinforcement - Angular menu triggers listen on mouseenter and a
        synthetic ActionChains move does not always fire it. Errors are
        non-fatal - the caller always has a click fallback."""
        d = self._ensure_driver()
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(d).move_to_element(element).perform()
        except Exception:
            pass
        try:
            d.execute_script(
                "for (const t of ['pointerover', 'mouseover', 'mouseenter'])"
                " arguments[0].dispatchEvent(new MouseEvent(t,"
                " {bubbles: t !== 'mouseenter', cancelable: true}));",
                element)
        except Exception:
            pass

    def _switch_to_traces(self, handles_before: set, trace: list) -> None:
        """TRACES usually opens as a new tab; some builds navigate the same
        tab. Never raises - the final download wait is the arbiter."""
        import time
        d = self._ensure_driver()
        deadline = time.time() + 30
        while time.time() < deadline:
            new = [h for h in d.window_handles if h not in handles_before]
            if new:
                d.switch_to.window(new[-1])
                trace.append("TRACES tab opened")
                return
            try:
                if "tdscpc" in (d.current_url or ""):
                    trace.append("TRACES opened in the same tab")
                    return
            except Exception:
                pass
            time.sleep(0.5)
        try:
            here = d.current_url
        except Exception:
            here = "?"
        trace.append(f"no TRACES tab after 30s (still on {here})")

    def _traces_export_pdf(self, ay: str, trace: list[str]) -> None:
        """TRACES side — calibrated LIVE on 2026-07-23 (Kishori session,
        traces61services host): the SSO lands on welcome26AS.xhtml which
        auto-forwards to view26AS.xhtml. NO agreement checkbox, NO Proceed,
        NO 'View Tax Credit' link on the current build (legacy best-effort
        kept, 2s each). The form: select#AssessmentYearDropDown (options
        exactly '2026-27' style), select#viewType (HTML/Text), then two
        <input> buttons - #btnSubmit 'View / Download' renders the
        statement inline (JSF postback) and #pdfBtn 'Export as PDF'
        (export26ASPdf()) downloads it."""
        import time
        self._tick_agreement_checkbox(trace)          # legacy builds only
        self._click_by_text(("proceed",), timeout=2, trace=trace,
                            step="TRACES Proceed (legacy)")
        self._click_by_text(("view tax credit",), timeout=2, trace=trace,
                            step="View Tax Credit link (legacy)")
        self._wait_for_view26as(trace)
        self._select_ay(ay, trace)
        if self._select_by_visible("HTML"):
            trace.append("View-as HTML selected")
        if not self._click_by_id_or_text(
                "btnSubmit", ("view / download", "view/download"),
                timeout=20, trace=trace, step="View / Download"):
            raise PortalError("the TRACES page did not offer the "
                              "'View / Download' control")
        time.sleep(2)             # JSF postback re-renders the page
        # The export control appears with the rendered statement (large
        # statements take a while).
        if not self._click_by_id_or_text(
                "pdfBtn", ("export as pdf",), timeout=60, trace=trace,
                step="Export as PDF"):
            raise PortalError("TRACES did not offer an 'Export as PDF' "
                              "control after View/Download")

    def _wait_for_view26as(self, trace: list[str]) -> None:
        """Wait out the welcome26AS.xhtml auto-forward until the statement
        form (its AY dropdown) is present. Never raises - the next step's
        own wait is the arbiter."""
        import time
        from selenium.webdriver.common.by import By
        d = self._ensure_driver()
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if d.find_elements(By.ID, "AssessmentYearDropDown"):
                    trace.append("view26AS form ready")
                    return
            except Exception:
                pass
            time.sleep(0.5)
        trace.append("AY dropdown not seen within 30s")

    def _click_by_id_or_text(self, element_id: str,
                             needles: tuple[str, ...], *, timeout: float,
                             trace: list[str], step: str) -> bool:
        """Click the live-calibrated element id; fall back to the generic
        text matcher if the id is gone (TRACES varies across host builds)."""
        import time
        from selenium.webdriver.common.by import By
        d = self._ensure_driver()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                els = [e for e in d.find_elements(By.ID, element_id)
                       if e.is_displayed()]
            except Exception:
                els = []
            if els:
                self._robust_click(els[0])
                trace.append(f"{step}: clicked #{element_id}")
                return True
            remaining = deadline - time.time()
            if remaining <= timeout / 2:      # give the text matcher a turn
                return self._click_by_text(needles, timeout=max(remaining, 1),
                                           trace=trace, step=step)
            time.sleep(0.5)
        return self._click_by_text(needles, timeout=1, trace=trace,
                                   step=step)

    def _tick_agreement_checkbox(self, trace: list[str]) -> None:
        """First TRACES visit shows a usage-agreement checkbox; tick the
        first visible unchecked box (best-effort - absent on repeats)."""
        from selenium.webdriver.common.by import By
        d = self._ensure_driver()
        for el in d.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
            try:
                if el.is_displayed() and not el.is_selected():
                    self._robust_click(el)
                    trace.append("agreement checkbox ticked")
                    return
            except Exception:
                continue

    def _select_ay(self, ay: str, trace: list[str]) -> None:
        """Pick the assessment year in whichever native <select> lists it,
        tolerating '2026-27' vs '2026-2027' option styles."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select
        if self._select_by_visible(ay):
            trace.append(f"AY {ay} selected")
            return
        start, end = ay.split("-")[0], ay.split("-")[-1]
        for el in self._ensure_driver().find_elements(By.TAG_NAME, "select"):
            try:
                sel = Select(el)
                for opt in sel.options:
                    text = (opt.text or "").strip()
                    if text.startswith(start) and text.endswith(end):
                        sel.select_by_visible_text(text)
                        trace.append(f"AY selected as '{text}'")
                        return
            except Exception:
                continue
        trace.append(f"AY dropdown not found for {ay}")

    # -- Forgot-Password (human completes; browser stays open) -------------
    def open_forgot_password(self, *, pan: str) -> list[str]:
        """Open the login page, pre-fill the user ID and click through to
        the Forgot-Password flow. The browser is handed to the human."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        d = self._ensure_driver()
        trace: list[str] = []
        d.get(LOGIN_URL)
        self._dismiss_stray_alert()
        uid = self._wait(60).until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "input#panAdhaarUserId, "
                              "input[formcontrolname='panAdhaarUserId'], "
                              "input[type='text']")))
        uid.clear()
        uid.send_keys(pan)
        trace.append("user ID pre-filled")
        self._click_by_text(("continue",), timeout=self._SHORT, trace=trace,
                            step="Continue")
        self._click_by_text(("forgot password",), timeout=10, trace=trace,
                            step="Forgot Password link")
        return trace

    # -- ITR JSON upload (File Income Tax Return flow) ---------------------
    FILE_ITR_URL = ("https://eportal.incometax.gov.in/iec/foservices/#/"
                    "dashboard/fileIncomeTaxReturn")

    def upload_itr_json(self, *, json_path: str, ay: str,
                        timeout: float) -> list[str]:
        """Drive File Income Tax Return up to ATTACHING the generated JSON:
        direct-navigate to the observed route (post-login landing page),
        pick the AY in the Material dropdown, choose Offline mode, Continue
        through the intermediate screens, and send the file to the first
        upload control that appears. STOPS there - the practitioner reviews
        every screen, presses Submit and e-verifies. Returns the trace."""
        import time
        d = self._ensure_driver()
        d.switch_to.window(d.window_handles[0])
        trace: list[str] = []
        try:
            d.get(self.FILE_ITR_URL)
            time.sleep(3)
            self._select_mat_option(ay, trace)
            self._click_by_text(("offline",), timeout=self._SHORT,
                                trace=trace, step="Offline mode")
            self._click_by_text(("continue",), timeout=self._SHORT,
                                trace=trace, step="Continue")
            time.sleep(2)
            # Screens after this vary by build (filing section / ITR type
            # pickers) - keep advancing until a file input appears.
            deadline = time.time() + min(timeout, 120)
            attached = False
            while time.time() < deadline:
                if self._attach_file(json_path, trace):
                    attached = True
                    break
                self._click_by_text(
                    ("attach file", "let's get started", "continue",
                     "proceed"), timeout=3, trace=trace,
                    step="advance screen")
                time.sleep(1)
            if not attached:
                raise PortalError("no file-upload control appeared in the "
                                  "File Income Tax Return flow")
            trace.append("JSON attached - review and submit in the browser")
        except Exception as exc:
            diag = self._dump_diagnostic("file_itr")
            raise PortalError(
                f"ITR upload did not complete ({_short_err(exc)}). Steps: "
                f"{' -> '.join(trace) or 'none'}. The browser stays open - "
                "finish manually (e-File > Income Tax Returns > File Income "
                "Tax Return > Offline > attach the downloaded JSON). "
                f"Diagnostic saved to {diag}*.") from exc
        return trace

    def _select_mat_option(self, text: str, trace: list[str]) -> bool:
        """Angular Material select: click the combobox, then the overlay
        option containing ``text`` (e.g. the assessment year)."""
        import time
        from selenium.webdriver.common.by import By
        d = self._ensure_driver()
        for sel in d.find_elements(
                By.CSS_SELECTOR, "mat-select, [role='combobox']"):
            try:
                if not sel.is_displayed():
                    continue
                self._robust_click(sel)
                time.sleep(0.8)
                opt = self._find_clickable((text.lower(),), timeout=4)
                if opt is not None:
                    self._robust_click(opt)
                    trace.append(f"AY '{text}' selected")
                    return True
            except Exception:
                continue
        trace.append(f"AY dropdown for '{text}' not found")
        return False

    def _attach_file(self, path: str, trace: list[str]) -> bool:
        """send_keys the JSON path into any file input on the page (file
        inputs are often visually hidden - no is_displayed filter)."""
        from selenium.webdriver.common.by import By
        for inp in self._ensure_driver().find_elements(
                By.CSS_SELECTOR, "input[type='file']"):
            try:
                inp.send_keys(path)
                trace.append("file input found - JSON attached")
                return True
            except Exception:
                continue
        return False

    def _read_retained(self, path: str) -> bytes:
        """Read the downloaded file's bytes and copy it into ``retain_dir`` so
        the practitioner keeps the raw AIS JSON / 26AS PDF (timestamped, so
        repeat pulls never clash)."""
        data = _read_file(path)
        if self.retain_dir:
            import os
            import shutil
            import time
            try:
                os.makedirs(self.retain_dir, exist_ok=True)
                dest = os.path.join(
                    self.retain_dir,
                    f"{time.strftime('%Y%m%d_%H%M%S')}_{os.path.basename(path)}")
                shutil.copy2(path, dest)
            except OSError:
                pass
        return data

    def quit(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            finally:
                self._driver = None

    def _dump_diagnostic(self, name: str) -> str:
        """Save a screenshot + page HTML of every open tab so an uncalibrated
        leg (26AS/TRACES) can be fixed from the real DOM. Returns the path
        stem; files are ``<stem>_tabN.png`` / ``.html`` in %TEMP%/itd_diag."""
        import os
        import tempfile
        import time
        d = self._driver
        base = os.path.join(tempfile.gettempdir(), "itd_diag")
        os.makedirs(base, exist_ok=True)
        stem = os.path.join(base, f"{name}_{time.strftime('%Y%m%d_%H%M%S')}")
        try:
            for i, handle in enumerate(d.window_handles):
                d.switch_to.window(handle)
                try:
                    d.save_screenshot(f"{stem}_tab{i}.png")
                except Exception:
                    pass
                try:
                    with open(f"{stem}_tab{i}.html", "w",
                              encoding="utf-8") as f:
                        f.write(f"<!-- url: {d.current_url} -->\n"
                                + d.page_source)
                except Exception:
                    pass
        except Exception:
            pass
        return stem

    # -- small selenium helpers -------------------------------------------
    def _dismiss_stray_alert(self) -> None:
        """Dismiss any JS prompt/alert the portal may throw (belt-and-braces;
        the anti-detection flags should stop it firing at all)."""
        from selenium.common.exceptions import NoAlertPresentException
        for _ in range(3):
            try:
                self._ensure_driver().switch_to.alert.dismiss()
            except NoAlertPresentException:
                return
            except Exception:
                return

    # Optional steps use a SHORT wait so an absent element costs a few
    # seconds, not 30 - required steps pass their own longer timeouts.
    _SHORT = 6

    def _robust_click(self, element) -> None:
        """Click that survives an overlay intercepting the hit: scroll into
        view, try a normal click, and on interception dismiss overlays and fall
        back to a JS click (which is not blocked by a covering backdrop)."""
        from selenium.common.exceptions import (
            ElementClickInterceptedException, ElementNotInteractableException)
        d = self._ensure_driver()
        try:
            d.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", element)
        except Exception:
            pass
        try:
            element.click()
            return
        except (ElementClickInterceptedException,
                ElementNotInteractableException):
            self._dismiss_overlays()
        try:
            element.click()
        except (ElementClickInterceptedException,
                ElementNotInteractableException):
            d.execute_script("arguments[0].click();", element)

    def _dismiss_overlays(self) -> None:
        """Close any open Material/cdk modal (e.g. the compliance portal's
        on-load announcement) whose backdrop would intercept clicks."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        d = self._ensure_driver()
        for xp in ("//button[@aria-label='Close']",
                   "//mat-dialog-container//button[contains(@class,'close')]",
                   "//*[contains(@class,'modal')]"
                   "//button[contains(@class,'close')]",
                   "//*[contains(@class,'close-icon') or "
                   "contains(@class,'closeIcon')]",
                   "//button[normalize-space()='Close' or "
                   "normalize-space()='OK' or normalize-space()='Ok' or "
                   "normalize-space()='Got it']"):
            for el in d.find_elements(By.XPATH, xp):
                try:
                    if el.is_displayed():
                        d.execute_script("arguments[0].click();", el)
                except Exception:
                    pass
        try:
            d.switch_to.active_element.send_keys(Keys.ESCAPE)
        except Exception:
            pass

    def _wait_no_backdrop(self, timeout: float) -> None:
        """Wait (best-effort) for any cdk overlay backdrop to disappear."""
        import time
        from selenium.webdriver.common.by import By
        d = self._ensure_driver()
        deadline = time.time() + timeout
        while time.time() < deadline:
            shown = [e for e in d.find_elements(
                By.CSS_SELECTOR, ".cdk-overlay-backdrop-showing")
                if e.is_displayed()]
            if not shown:
                return
            self._dismiss_overlays()
            time.sleep(0.5)

    def _click_button_text(self, text: str, timeout: float = 30) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        self._robust_click(self._wait(timeout).until(EC.element_to_be_clickable(
            (By.XPATH, f"//button[normalize-space()='{text}']"))))

    def _click_button_contains(self, text: str, timeout: float = 30) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        self._robust_click(self._wait(timeout).until(EC.element_to_be_clickable(
            (By.XPATH, f"//button[contains(normalize-space(),'{text}')]"))))

    def _click_link_text(self, text: str, timeout: float = 30) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        self._robust_click(self._wait(timeout).until(EC.element_to_be_clickable(
            (By.XPATH, f"//a[normalize-space()='{text}'] | "
                       f"//*[@role='link'][normalize-space()='{text}']"))))

    _ALPHA_UP = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    _ALPHA_LOW = "abcdefghijklmnopqrstuvwxyz"

    def _click_by_text(self, needles: tuple[str, ...], *, timeout: float,
                       trace: list[str], step: str) -> bool:
        """Find-and-click the most specific visible control whose text (or
        input @value) case-insensitively CONTAINS one of ``needles``; record
        the outcome in ``trace``. Returns False (never raises) when nothing
        matched, so callers decide which steps are fatal."""
        el = self._find_clickable(tuple(n.lower() for n in needles), timeout)
        if el is None:
            trace.append(f"{step}: NOT FOUND")
            return False
        try:
            label = (el.text or el.get_attribute("value") or "?").strip()
        except Exception:
            label = "?"
        self._robust_click(el)
        trace.append(f"{step}: clicked '{label[:40]}'")
        return True

    def _find_clickable(self, needles: tuple[str, ...], timeout: float):
        """Poll for a DISPLAYED clickable matching any needle. Menu items in
        the cdk overlay (rendered only after their parent opens) rank first,
        then shorter text = more specific; breadcrumb/disabled entries (the
        trap the old exact-match flow fell into) are excluded."""
        import time
        from selenium.webdriver.common.by import By
        d = self._ensure_driver()
        low = (f"translate(normalize-space(.), '{self._ALPHA_UP}', "
               f"'{self._ALPHA_LOW}')")
        low_val = (f"translate(@value, '{self._ALPHA_UP}', "
                   f"'{self._ALPHA_LOW}')")
        conds = " or ".join(f"contains({low}, '{n}')" for n in needles)
        val_conds = " or ".join(f"contains({low_val}, '{n}')"
                                for n in needles)
        skip = ("[not(ancestor::app-breadcrumb)]"
                "[not(contains(@class,'disabledRoute'))]")
        xp = " | ".join(
            [f"//{tag}[{conds}]{skip}"
             for tag in ("a", "button", "li", "*[@role='menuitem']",
                         "*[@role='button']", "*[@role='link']")]
            + [f"//input[{val_conds}]"])
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidates = []
            for el in d.find_elements(By.XPATH, xp):
                try:
                    if not el.is_displayed():
                        continue
                    text = (el.text or el.get_attribute("value") or "").strip()
                    if not text or len(text) > 80:
                        continue               # a container, not a control
                    in_menu = bool(el.find_elements(
                        By.XPATH, "ancestor::*[contains(@class,"
                                  "'cdk-overlay-container') or "
                                  "contains(@class,'dropdown-content')]"))
                    candidates.append((0 if in_menu else 1, len(text), el))
                except Exception:
                    continue
            if candidates:
                candidates.sort(key=lambda c: (c[0], c[1]))
                return candidates[0][2]
            time.sleep(0.5)
        return None

    def _select_by_visible(self, text: str) -> bool:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select
        for el in self._ensure_driver().find_elements(By.TAG_NAME, "select"):
            try:
                Select(el).select_by_visible_text(text)
                return True
            except Exception:
                continue
        return False


def _json_row_button(buttons):
    """From the dialog's Download buttons, the one whose row text mentions
    JSON."""
    from selenium.webdriver.common.by import By
    for b in buttons:
        try:
            row = b.find_element(By.XPATH, "./ancestor::*[self::div or "
                                 "self::tr][1]")
            if "json" in (row.text or "").lower():
                return b
        except Exception:
            continue
    return None


# ------------------------------------------------------- filesystem helpers
def _dir_snapshot(path: str) -> set[str]:
    import os
    try:
        return set(os.listdir(path))
    except FileNotFoundError:
        return set()


def _wait_for_new_file(path: str, before: set[str], suffixes: tuple[str, ...],
                       timeout: float) -> str:
    """Poll ``path`` for a NEW file with one of ``suffixes`` that has finished
    downloading (no Chrome .crdownload partial). Returns its full path."""
    import os
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        now = _dir_snapshot(path)
        new = [f for f in now - before
               if f.lower().endswith(suffixes)
               and not f.endswith(".crdownload")]
        # ignore names still being written (a .crdownload twin present)
        partial = {f[:-len(".crdownload")] for f in now
                   if f.endswith(".crdownload")}
        done = [f for f in new if f not in partial]
        if done:
            done.sort(key=lambda f: os.path.getmtime(os.path.join(path, f)))
            return os.path.join(path, done[-1])
        time.sleep(0.5)
    raise PortalError(
        f"timed out waiting for the {'/'.join(suffixes)} download to finish "
        "in the browser.")


def _read_file(path: str) -> bytes:
    """Read the downloaded file and LEAVE it on disk so the practitioner keeps
    the raw AIS JSON / 26AS PDF (as Genius does). The AIS JSON is the ITD's own
    encrypted envelope, so retaining it is not a plaintext-PII exposure."""
    with open(path, "rb") as fh:
        return fh.read()


def _short_err(exc: Exception) -> str:
    """First line of an exception message (selenium dumps a long stacktrace)
    so the UI shows something readable, not a wall of chromedriver frames."""
    text = str(exc).strip()
    first = text.splitlines()[0] if text else ""
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


# --------------------------------------------------------------- live client
class SeleniumITDPortalClient:
    """Live, browser-driven ITD connector. Pre-fills PAN + password; the human
    completes CAPTCHA + OTP in the opened Chrome; then AIS/TIS + 26AS download
    automatically.

    ``browser`` is injectable so the pull orchestration can be unit-tested with
    a fake; in production it is built lazily as a
    :class:`SeleniumPortalBrowser` with a fresh temp download directory."""

    def __init__(self, *, browser: PortalBrowser | None = None,
                 login_timeout: float = 300.0,
                 download_timeout: float = 120.0) -> None:
        self._browser = browser
        self.login_timeout = login_timeout
        self.download_timeout = download_timeout

    def _make_browser(self) -> PortalBrowser:
        if self._browser is not None:
            return self._browser
        import tempfile
        # A FRESH per-pull download dir keeps "new file" detection unambiguous;
        # downloaded files are copied to the retained folder the user can open.
        download_dir = tempfile.mkdtemp(prefix="itd_pull_")
        return SeleniumPortalBrowser(download_dir,
                                     retain_dir=retained_downloads_dir())

    def pull(self, *, pan: str, password: str, ay: str,
             otp: str | None = None) -> PortalPull:
        if not pan or not password:
            raise PortalError(
                "the client's PAN and stored e-filing password are required "
                "for the pull.")
        browser = self._make_browser()
        messages: list[str] = []
        ais_json: bytes | None = None
        as26_pdf: bytes | None = None
        try:
            browser.open_and_prefill_login(pan=pan, password=password)
            messages.append("Login page opened with PAN + password "
                            "pre-filled. Complete the CAPTCHA and OTP in the "
                            "browser window to continue.")
            browser.wait_until_logged_in(self.login_timeout)
            try:
                ais_json = browser.download_ais_json(self.download_timeout)
                messages.append("AIS/TIS downloaded from the compliance "
                                "portal.")
            except Exception as exc:
                messages.append("AIS/TIS download did not complete: "
                                + _short_err(exc))
            try:
                as26_pdf = browser.download_26as_pdf(
                    ay=ay, timeout=self.download_timeout)
                messages.append("Form 26AS downloaded from TRACES.")
            except Exception as exc:
                messages.append("Form 26AS download did not complete: "
                                + _short_err(exc))
        finally:
            browser.quit()
        if ais_json is None and as26_pdf is None:
            raise PortalError(
                "the pull did not retrieve any documents. " + " ".join(
                    messages))
        return PortalPull(ais_json=ais_json, as26_pdf=as26_pdf,
                          messages=tuple(messages))

    def reset_password(self, *, pan: str, new_password: str | None = None,
                       otp: str | None = None) -> ResetOutcome:
        """Two-step, human-in-the-loop reset. Step 1 (no otp): open Chrome
        on the portal's Forgot-Password flow with the user ID pre-filled and
        LEAVE IT OPEN - the practitioner completes the OTP (relayed by the
        client) and sets the new password on the portal itself. Step 2
        (otp + new_password entered in the app): nothing left to drive -
        report done so the caller stores the new password in the vault."""
        if not pan:
            raise PortalError("PAN is required")
        if otp is None:
            browser = self._make_browser()
            try:
                trace = browser.open_forgot_password(pan=pan)
            except Exception as exc:
                browser.quit()
                raise PortalError(
                    "could not open the Forgot-Password flow: "
                    + _short_err(exc)) from exc
            # Browser intentionally left open for the human.
            steps = " / ".join(trace) or "login page"
            return ResetOutcome(
                "otp_sent",
                "Chrome opened on the portal's Forgot-Password flow with "
                f"the user ID pre-filled ({steps}). Complete the OTP (the "
                "client relays it) and set the NEW password there, then "
                "enter that password here so it is stored in the vault.")
        if not new_password:
            return ResetOutcome("error", "Enter the new password exactly "
                                         "as set on the portal.")
        return ResetOutcome("done", "New e-filing password recorded.")


class SeleniumFilingClient:
    """Browser-driven 'file on the ITD portal': pre-fills login (human does
    CAPTCHA + OTP), navigates File Income Tax Return, and ATTACHES the
    generated JSON — then LEAVES THE BROWSER OPEN. The practitioner reviews
    every screen, presses Submit and completes e-verification personally;
    the tool never submits a return and never touches a verification OTP.
    The browser is closed by the tool only when login itself never
    completed."""

    def __init__(self, *, browser: PortalBrowser | None = None,
                 login_timeout: float = 300.0,
                 upload_timeout: float = 180.0) -> None:
        self._browser = browser
        self.login_timeout = login_timeout
        self.upload_timeout = upload_timeout

    def _make_browser(self) -> PortalBrowser:
        if self._browser is not None:
            return self._browser
        import tempfile
        return SeleniumPortalBrowser(tempfile.mkdtemp(prefix="itd_file_"))

    def upload(self, *, pan: str, password: str, ay: str,
               json_path: str) -> tuple[str, ...]:
        from itrcore import demo
        if demo.is_demo():
            raise PortalError("Browser-driven filing on the live ITD portal "
                              "is disabled in the public demo. Download the "
                              "generated JSON instead.")
        if not pan or not password:
            raise PortalError("the client's PAN and stored e-filing "
                              "password are required.")
        import os
        if not os.path.exists(json_path):
            raise PortalError(f"generated JSON not found at {json_path} — "
                              "regenerate the filing first.")
        browser = self._make_browser()
        messages: list[str] = []
        try:
            browser.open_and_prefill_login(pan=pan, password=password)
            messages.append("Login page opened with PAN + password "
                            "pre-filled. Complete the CAPTCHA and OTP in "
                            "the browser window.")
            browser.wait_until_logged_in(self.login_timeout)
        except Exception:
            browser.quit()                    # login never happened
            raise
        # Past login: the browser belongs to the human from here on -
        # NEVER quit it, even if navigation fails midway.
        trace = browser.upload_itr_json(json_path=json_path, ay=ay,
                                        timeout=self.upload_timeout)
        messages.extend(trace)
        messages.append(
            "The tool stops BEFORE submission. In the open browser: review "
            "each screen, Submit, e-verify (Aadhaar OTP / net-banking), "
            "then record the acknowledgement number in the tracker.")
        return tuple(messages)


def default_client() -> ITDPortalClient:
    """The client the app uses today: the LIVE, browser-driven client. It
    pre-fills PAN + password and drives the downloads; the human does only the
    CAPTCHA/OTP. Swap to :class:`MockITDPortalClient` here for an offline
    walkthrough (its data is labelled MOCK). The public demo always uses the
    mock."""
    from itrcore import demo
    if demo.is_demo():
        return MockITDPortalClient()
    return SeleniumITDPortalClient()


# ------------------------------------------------------------- orchestration
def pull_and_import(scope: TaxpayerScope, ay: str, client: ITDPortalClient,
                    *, pan: str, password: str,
                    otp: str | None = None) -> dict:
    """Pull from the portal, parse with the existing parsers, and import into
    the same tables a file upload uses - so the AIS/TIS workbook, the 26AS PDF
    and the auto-fill computation all see identical data whether it arrived by
    pull or by upload. Returns per-source counts + messages."""
    dob = _taxpayer_dob(scope)
    pull = client.pull(pan=pan, password=password, ay=ay, otp=otp)
    counts: dict[str, int] = {}
    messages = list(pull.messages)

    if pull.ais_json:
        try:
            counts.update(_import_ais(scope, ay, pull.ais_json, pan, dob,
                                      messages))
        except ValueError:
            # A missing/wrong DOB must not sink the rest of the pull (TIS
            # and 26AS import fine without it). The payload is PARKED and
            # imports itself the moment the DOB reaches the client record
            # — no re-pull, no manual re-upload.
            importers_repo.save_pending_ais(scope, ay, pull.ais_json,
                                            "ITD portal (parked)")
            messages.append(
                "AIS downloaded but could not be decrypted — the client "
                "record has no (working) date of birth. The payload has "
                "been PARKED: enter the DOB in the box on this page (or "
                "anywhere on the client record) and it imports itself "
                "on the next page load automatically.")
    if pull.tis_json:
        # The ITD's OWN TIS (their reconciled processed/derived values) —
        # authoritative over anything derived from the AIS document, and
        # never overwritten by refresh_ais_derived (marker in the label).
        counts["tis"] = importers_repo.import_tis(
            scope, ay, tis_mod.parse_tis_json(pull.tis_json),
            "ITD portal (TIS download)")
    if pull.as26_pdf:
        counts["as26"] = _import_26as_pdf(scope, ay, pull.as26_pdf, messages)
    elif pull.as26_text:
        parsed = form26as_mod.parse_26as_text(pull.as26_text)
        counts["as26"] = importers_repo.import_26as(scope, ay,
                                                    parsed["entries"])
        if parsed.get("challans"):
            counts["challans"] = importers_repo.import_challans(
                scope, ay, parsed["challans"])

    auditlog.log(scope.principal.username, "ITD_PORTAL_PULL",
                 entity="taxpayer", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "counts": counts})
    return {"counts": counts, "messages": messages}


def _taxpayer_dob(scope: TaxpayerScope) -> str | None:
    try:
        return taxpayers_repo.get_taxpayer(scope).dob
    except ValueError:
        return None


def retained_downloads_dir() -> str:
    """Where pulled files are retained for the user (and scanned by the
    parked-AIS fallback). Override with ITR_PORTAL_PULL_DIR in tests."""
    import os
    return os.environ.get("ITR_PORTAL_PULL_DIR") or os.path.join(
        os.path.expanduser("~"), "ITR-Portal", "pull", "downloads")


def _fy_of_ay(ay: str) -> str:
    """'2026-27' → '2025-26' — the portal names AIS downloads by FY."""
    try:
        start = int(str(ay).split("-")[0])
    except (ValueError, IndexError):
        return ""
    return f"{start - 1}-{str(start)[2:]}"


def _retained_ais_candidates(pan: str, ay: str) -> list:
    """Retained ``*_AIS_*.json`` files that plausibly belong to this
    client, newest first. The portal's filenames carry a MASKED PAN
    (XXXPL1234X) — match on the visible middle characters and the FY
    token; decryption (keyed to the real PAN+DOB) is the authenticator,
    so a false candidate simply fails to decrypt and is skipped."""
    import os
    folder = retained_downloads_dir()
    if not os.path.isdir(folder) or not pan:
        return []
    fy = _fy_of_ay(ay)
    visible = pan[3:9].upper()
    out = []
    for name in os.listdir(folder):
        if "_AIS_" not in name or not name.lower().endswith(".json"):
            continue
        if fy and fy not in name:
            continue
        if visible not in name.upper():
            continue
        out.append(os.path.join(folder, name))
    out.sort(key=os.path.getmtime, reverse=True)
    return out


def import_pending_ais(scope: TaxpayerScope, ay: str) -> dict | None:
    """Import a parked AIS the moment it becomes decryptable (the DOB has
    reached the client record). Called by the Statements/Computation pages
    on render. Falls back to the retained pull downloads on disk for pulls
    that pre-date parking. Returns {"counts", "messages"} when something
    imported, else None (nothing parked, or still waiting for the DOB)."""
    try:
        tp = taxpayers_repo.get_taxpayer(scope)
    except ValueError:
        return None
    pending = importers_repo.get_pending_ais(scope, ay)
    saved = importers_repo.get_ais_document(scope, ay)
    candidates: list[tuple[bytes, str, bool]] = []   # payload, name, parked
    if pending:
        if saved and str(saved.get("created_at") or "") >= \
                str(pending.get("created_at") or ""):
            # A newer manual upload already superseded the parked pull.
            importers_repo.clear_pending_ais(scope, ay)
            return None
        candidates.append((pending["payload"],
                           pending["source_file"] or "parked AIS", True))
    else:
        if saved:
            return None            # nothing parked, data already imported
        import os
        for path in _retained_ais_candidates(tp.pan, ay):
            try:
                with open(path, "rb") as fh:
                    candidates.append((fh.read(), os.path.basename(path),
                                       False))
            except OSError:
                continue
    for payload, name, parked in candidates:
        if ais_crypto.looks_encrypted(payload) and not tp.dob:
            return None                    # still waiting for the DOB
        messages: list[str] = []
        try:
            counts = _import_ais(scope, ay, payload, tp.pan, tp.dob,
                                 messages)
        except ValueError:
            if parked:
                return None                # wrong DOB — keep it parked
            continue                       # not this retained file — next
        if parked:
            importers_repo.clear_pending_ais(scope, ay)
        auditlog.log(scope.principal.username, "AIS_PENDING_IMPORT",
                     entity="ais", entity_id=scope.taxpayer_id,
                     taxpayer_id=scope.taxpayer_id,
                     details={"ay": ay, "file": name, "counts": counts})
        messages.insert(0, f"Parked/retained AIS ({name}) imported "
                           "automatically.")
        return {"counts": counts, "messages": messages}
    return None


def _import_ais(scope: TaxpayerScope, ay: str, ais_json: bytes,
                pan: str, dob: str | None, messages: list[str]) -> dict:
    """Decrypt (if the envelope is encrypted) + parse + import AIS, mirroring
    the Statements-page upload: the rich v15 document is saved (for the Genius
    workbook) and its derived TIS summary imported, plus the flat de-duplicated
    entries that feed auto-fill. Falls back to the flat list for older JSON.

    Returns the per-source counts {"ais": n, "tis": m} (m absent for the older
    flat format that carries no derivable TIS summary)."""
    entries = ais_mod.parse_ais_json(ais_json, pan=pan, dob=dob)
    result = {"ais": importers_repo.import_ais(scope, ay, entries,
                                               "ITD portal")}
    try:
        doc = ais_mod.parse_ais_document(ais_json, pan=pan, dob=dob)
    except ValueError:
        return result                       # older flat format - no rich doc
    importers_repo.save_ais_document(
        scope, ay, ais_mod.document_to_dict(doc), "ITD portal")
    result["tis"] = importers_repo.import_tis(
        scope, ay, ais_mod.tis_entries(doc), "ITD portal")
    messages.append(f"Classified {len(doc.rows)} AIS information blocks and "
                    f"derived {result['tis']} TIS categories.")
    # Learn the DOB from the AIS's own partA when the record lacks it.
    learned = ais_crypto.iso_dob(doc.dob)
    if learned and not _taxpayer_dob(scope):
        taxpayers_repo.update_contact(scope, dob=learned)
        messages.append(f"Date of birth {learned} saved to the client "
                        "record (from the AIS).")
    return result


def _import_26as_pdf(scope: TaxpayerScope, ay: str, pdf: bytes,
                     messages: list[str]) -> int:
    parsed = form26as_pdf_mod.parse_26as_pdf(pdf)
    importers_repo.save_26as_document(
        scope, ay, form26as_pdf_mod.document_to_dict(parsed["document"]),
        "ITD portal")
    if parsed.get("skipped"):
        messages.append(f"{len(parsed['skipped'])} 26AS deductor(s) skipped "
                        "(no TDS total).")
    return importers_repo.import_26as(scope, ay, parsed["entries"])


def reset_password(scope: TaxpayerScope, client: ITDPortalClient, *,
                   pan: str, new_password: str | None = None,
                   otp: str | None = None) -> ResetOutcome:
    """Drive the portal's Forgot-Password flow. Step 1 (otp=None) triggers the
    OTP to the client's own mobile; step 2 (otp + new_password) completes it.
    On success the new password is stored in the vault and the reset is
    audit-logged. The OTP is relayed by a human - never read or intercepted."""
    outcome = client.reset_password(pan=pan, new_password=new_password,
                                    otp=otp)
    if outcome.status == "done" and new_password:
        creds_repo.save_credentials(scope, password=new_password)
        auditlog.log(scope.principal.username, "ITD_PASSWORD_RESET",
                     entity="taxpayer", entity_id=scope.taxpayer_id,
                     taxpayer_id=scope.taxpayer_id, details={})
    return outcome
