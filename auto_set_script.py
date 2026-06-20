#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qaff YouTube Channel Setup Automation

Uses real Google Chrome with a fixed, persistent profile and manual login.
The script does not bypass CAPTCHA, 2FA, or Google security checks.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import csv
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

try:
    from playwright.sync_api import (
        BrowserContext,
        Error as PlaywrightError,
        Locator,
        Page,
        TimeoutError as PlaywrightTimeoutError,
        expect,
        sync_playwright,
    )
except ImportError:
    print("Playwright is not installed.")
    print("Install it with:")
    print("  pip install playwright")
    print("  playwright install")
    sys.exit(1)

try:
    from rich.console import Console as RichConsole
except Exception:  # rich is optional
    RichConsole = None


# =========================
# User settings
# =========================

CHROME_PROFILE_PATH = r"C:\QaffChromeProfile"
CHANNEL_SWITCHER_URL = "https://www.youtube.com/channel_switcher?"
YOUTUBE_STUDIO_URL = "https://studio.youtube.com/"
LINK_TITLE = "Website"
LINK_URL = "digital.qaff.net"
BUSINESS_EMAIL = "info@digital.qaff.net"
LIVE_FULL_NAME = "Mahmoud Ahmed Ali Mousa"
LIVE_COMPANY_NAME = "Qaff Digital"
HEADLESS = False
PAUSE_FOR_LOGIN = True
PAUSE_FOR_LOGO_UPLOAD = False
DEFAULT_TIMEOUT = 60000
POST_PUBLISH_SETTLE_MS = 1000
FINAL_STEP_SETTLE_MS = 2000
AUTO_COLLECT_HANDLES = True
RUN_MODE = "data"


# =========================
# Runtime paths
# =========================

BASE_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
REPORT_PATH = BASE_DIR / "set_report.json"


EN_AR = {
    "customization": ["Customization", "التخصيص"],
    "basic_info": ["Basic info", "Profile", "المعلومات الأساسية", "معلومات أساسية"],
    "layout": ["Home tab", "Layout", "علامة تبويب الصفحة الرئيسية", "التنسيق", "التخطيط"],
    "add_link": ["Add link", "إضافة رابط"],
    "publish": ["Publish", "نشر"],
    "home_tab": ["Home tab", "علامة تبويب الصفحة الرئيسية", "Home"],
    "email": [
        "Business email",
        "Email address",
        "Email",
        "البريد الإلكتروني",
        "عنوان البريد الإلكتروني",
    ],
    "link_title": ["Enter title", "Link title", "Title", "إدخال عنوان", "عنوان الرابط"],
    "url": ["Enter URL", "URL", "إدخال عنوان URL", "عنوان URL"],
}

SECURITY_CHALLENGE_RE = re.compile(
    r"(captcha|unusual traffic|verify it'?s you|security check|"
    r"تحقق|التحقق|كابتشا|اختبار أمني|نشاط غير معتاد)",
    re.IGNORECASE,
)


# Clean language maps are defined again here to override any mojibake text that
# may exist in older copies of this script.
EN_AR = {
    "customization": ["Customization", "Branding", "التخصيص"],
    "basic_info": ["Basic info", "Profile", "المعلومات الأساسية", "معلومات أساسية"],
    "layout": ["Home tab", "Layout", "علامة تبويب الصفحة الرئيسية", "التنسيق", "التخطيط"],
    "add_link": ["Add link", "إضافة رابط", "Ajouter un lien", "Agregar enlace"],
    "publish": ["Publish", "Save", "نشر", "حفظ", "Publier", "Guardar", "Simpan"],
    "home_tab": ["Home tab", "Show Home tab", "علامة تبويب الصفحة الرئيسية", "عرض علامة تبويب الصفحة الرئيسية", "Home"],
    "email": [
        "Business email",
        "Email address",
        "Email",
        "البريد الإلكتروني",
        "عنوان البريد الإلكتروني",
        "معلومات الاتصال",
    ],
    "link_title": ["Enter title", "Link title", "Title", "إدخال عنوان", "عنوان الرابط"],
    "url": ["Enter URL", "URL", "إدخال عنوان URL", "عنوان URL"],
    "links": ["Links", "روابط", "الروابط"],
    "contact_info": ["Contact info", "Business email", "Email address", "معلومات الاتصال", "البريد الإلكتروني", "عنوان البريد الإلكتروني"],
    "discard": ["Discard", "Discard changes", "تجاهل", "تجاهل التغييرات"],
    "cancel": ["Cancel", "إلغاء"],
}

SECURITY_CHALLENGE_RE = re.compile(
    r"(captcha|unusual traffic|verify it'?s you|security check|"
    r"تحقق|التحقق|كابتشا|اختبار أمني|نشاط غير معتاد|"
    r"verifica|verificar|vérifier|sicherheitscheck|coba lagi|verifique)",
    re.IGNORECASE,
)

STUDIO_ERROR_PATTERNS = [
    "Oops, something went wrong",
    "Sorry, something went wrong",
    "حدث خطأ",
    "عذرا، حدث خطأ",
    "Lo sentimos, algo salio mal",
    "Lo sentimos, algo salió mal",
    "Une erreur s'est produite",
    "Es ist ein Fehler aufgetreten",
    "Si e verificato un errore",
    "Si è verificato un errore",
    "Coba lagi",
    "Maaf, ada yang tidak beres",
]


class Console:
    def __init__(self) -> None:
        self._rich = RichConsole() if RichConsole else None

    def print(self, message: str = "", style: str | None = None) -> None:
        if self._rich:
            self._rich.print(message, style=style, markup=False)
        else:
            print(message)


console = Console()


class StepFailure(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class ProfileInUseError(RuntimeError):
    pass


def make_exact_text_regex(values: Iterable[str]) -> re.Pattern[str]:
    choices = [re.escape(v) for v in values]
    return re.compile(r"^\s*(?:" + "|".join(choices) + r")\s*$", re.IGNORECASE)


def normalize_handle(handle: str) -> str:
    handle = (handle or "").strip()
    if not handle:
        return ""
    if not handle.startswith("@"):
        handle = "@" + handle
    return handle


def normalize_channel_identifier(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("@"):
        return normalize_handle(value)
    return value


def is_handle_identifier(value: str) -> bool:
    return (value or "").strip().startswith("@")


def xpath_text_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def safe_file_stem(handle: str) -> str:
    stem = handle.strip().lstrip("@")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    stem = stem.strip("._-")
    return stem or "channel"


def status_label(value: str) -> str:
    return value.replace("_", " ")


def body_contains_any(page: Page, phrases: Iterable[str], timeout: int = 5000) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=timeout)
    except Exception:
        return False

    lowered = text.casefold()
    return any(phrase.casefold() in lowered for phrase in phrases if phrase)


def studio_shell_is_visible(page: Page, timeout: int = 5000) -> bool:
    for locator in [
        page.locator("ytcp-app"),
        page.locator("ytcp-header"),
        page.locator("ytcp-navigation-drawer"),
        page.locator("ytcp-form-input-container"),
        page.locator("input.ytcpChannelLinkItemTitleInput"),
        page.locator("input.ytcpChannelLinkItemFormInput"),
    ]:
        try:
            expect(locator.first).to_be_visible(timeout=timeout)
            return True
        except Exception:
            continue
    return False


def print_header() -> None:
    console.print("=" * 40, "bold cyan")
    console.print("Qaff YouTube Channel Setup Automation", "bold cyan")
    console.print(f"Chrome Profile: {CHROME_PROFILE_PATH}", "bold cyan")
    console.print("=" * 40, "bold cyan")
    console.print()


def find_chrome_executable() -> str | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
    ]
    for path in candidates:
        if path and path.exists():
            return str(path)
    return None


def is_profile_in_use_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        phrase in message
        for phrase in [
            "profile is in use",
            "user data directory is already in use",
            "processsingleton",
            "singleton",
            "exitcode=21",
        ]
    )


def launch_chrome_context_once(playwright, launch_kwargs: dict[str, Any]) -> BrowserContext:
    try:
        return playwright.chromium.launch_persistent_context(
            channel="chrome",
            **launch_kwargs,
        )
    except PlaywrightError as exc:
        if is_profile_in_use_error(exc):
            raise ProfileInUseError(str(exc)) from exc

        chrome_path = find_chrome_executable()
        if not chrome_path:
            raise RuntimeError(
                "Could not start Google Chrome using channel='chrome', and chrome.exe "
                "was not found in common Windows locations. Install Google Chrome or "
                "set it in a standard path."
            ) from exc

        console.print("- channel='chrome' failed; using executable_path fallback.", "yellow")
        try:
            return playwright.chromium.launch_persistent_context(
                executable_path=chrome_path,
                **launch_kwargs,
            )
        except PlaywrightError as fallback_exc:
            if is_profile_in_use_error(fallback_exc):
                raise ProfileInUseError(str(fallback_exc)) from fallback_exc
            raise


REMOTE_DEBUGGING_PORT = 0

def launch_browser(playwright) -> tuple[BrowserContext, Page, bool]:
    port = globals().get("REMOTE_DEBUGGING_PORT", 0)
    if port > 0:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            return context, page, True
        except Exception as e:
            console.print(f"- Could not connect to browser on port {port}: {e}. Launching new browser instead.", "yellow")

    Path(CHROME_PROFILE_PATH).mkdir(parents=True, exist_ok=True)
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": CHROME_PROFILE_PATH,
        "headless": HEADLESS,
        "accept_downloads": True,
        "no_viewport": True,
        "args": ["--start-maximized"],
    }

    context: BrowserContext | None = None
    for attempt in range(1, 6):
        try:
            context = launch_chrome_context_once(playwright, launch_kwargs)
            break
        except ProfileInUseError:
            console.print()
            console.print("Chrome profile is still in use.", "yellow")
            console.print(f"Close every Chrome window using this profile: {CHROME_PROFILE_PATH}")
            console.print("Also check the taskbar/tray if Chrome is still running in the background.")
            if attempt == 5:
                raise RuntimeError(
                    "Chrome profile stayed locked after multiple attempts. Close Chrome completely "
                    "and run the script again."
                )
            input("After closing Chrome completely, press Enter to retry...")
            time.sleep(1)

    if context is None:
        raise RuntimeError("Could not launch Google Chrome.")

    context.set_default_timeout(DEFAULT_TIMEOUT)
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT)
    return context, page, False


def open_normal_chrome_for_login() -> subprocess.Popen | None:
    chrome_path = find_chrome_executable()
    if not chrome_path:
        console.print("- Google Chrome was not found in common Windows locations.", "yellow")
        console.print("  Open Chrome manually with this profile, then press Enter:")
        console.print(f'  chrome.exe --user-data-dir="{CHROME_PROFILE_PATH}" https://www.youtube.com/')
        return None

    Path(CHROME_PROFILE_PATH).mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            chrome_path,
            f"--user-data-dir={CHROME_PROFILE_PATH}",
            "--profile-directory=Default",
            "--new-window",
            "https://www.youtube.com/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_manual_login(page: Page) -> None:
    console.print("Step 1: Check login status", "bold")
    console.print("Checking login status...", "accent")
    
    try:
        page.goto("https://www.youtube.com/", wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
    except Exception:
        pass
        
    avatar = page.locator("button#avatar-btn").first
    logged_in = False
    for _ in range(300):
        if globals().get("STOP_EVENT") and STOP_EVENT.is_set():
            raise RuntimeError("Stopped by user.")
            
        try:
            if avatar.is_visible(timeout=500):
                logged_in = True
                break
        except Exception:
            pass
            
        current_url = page.url
        if "accounts.google.com" in current_url or "ServiceLogin" in current_url:
            console.print("Warning: Please log in in the opened browser window to continue.", "yellow")
        page.wait_for_timeout(2000)
        
    if not logged_in:
        raise RuntimeError("Login timeout expired.")
    console.print("✓ Login detected successfully!", "green")


def wait_for_manual_logo_upload(page: Page, handle: str) -> None:
    console.print("- Customization page is open.")
    console.print("  Upload or update the channel logo manually in Chrome if needed.")
    input("  After finishing the logo, press Enter to continue...")


def collect_handles_from_user() -> list[str]:
    console.print("Step 2: Handles Input", "bold")
    console.print("Paste channel handles or exact channel titles one per line.")
    console.print("Type DONE or press Enter on empty line to start.")
    console.print()

    handles: list[str] = []
    seen: set[str] = set()

    while True:
        line = input("> ")
        stripped = line.strip()
        if not stripped:
            break
        if stripped.upper() == "DONE":
            break

        handle = normalize_channel_identifier(stripped)
        if not handle:
            continue

        key = handle.casefold()
        if key in seen:
            continue
        seen.add(key)
        handles.append(handle)

    console.print()
    return handles


def confirm_handles(handles: list[str]) -> None:
    if not handles:
        console.print("No channels were entered. Nothing to process.", "yellow")
        return

    console.print("Final channels to process:", "bold")
    for index, handle in enumerate(handles, start=1):
        console.print(f"{index}. {handle}")
    console.print()
    input("Press Enter to start...")
    console.print()


def detect_and_wait_for_security_challenge(page: Page) -> None:
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        text = ""

    if SECURITY_CHALLENGE_RE.search(text) or "captcha" in page.url.lower():
        console.print("- Security check detected.", "yellow")
        console.print("  Complete it manually in Chrome. The script will continue after Enter.")
        input("Press Enter after completing the security check...")


def wait_for_soft_network_idle(page: Page, timeout: int = 1000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeoutError:
        pass


def extract_exact_handle_from_channel_item(item: Locator) -> list[str]:
    """Return handle-like texts from one ytd-account-item-renderer only."""
    texts: list[str] = []
    try:
        texts.extend(item.locator("yt-formatted-string").all_inner_texts())
    except Exception:
        pass

    if not texts:
        try:
            texts.append(item.inner_text(timeout=3000))
        except Exception:
            pass

    handles: list[str] = []
    for text in texts:
        for line in re.split(r"[\r\n]+", text):
            cleaned = line.strip()
            if not cleaned.startswith("@"):
                continue
            token = cleaned.split()[0].strip()
            if re.fullmatch(r"@[^\s]+", token):
                handles.append(token)

    return handles


def extract_channel_titles_from_channel_item(item: Locator) -> list[str]:
    titles: list[str] = []
    try:
        texts = item.locator("yt-formatted-string#channel-title").all_inner_texts()
    except Exception:
        texts = []

    for text in texts:
        cleaned = " ".join(text.split())
        if cleaned:
            titles.append(cleaned)
    return titles


def collect_handles_from_channel_switcher(page: Page) -> list[str]:
    console.print("Step 2: Auto-detect Channel Handles", "bold")
    console.print("- Opening channel switcher...")
    page.goto(CHANNEL_SWITCHER_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
    detect_and_wait_for_security_challenge(page)

    items = page.locator("ytd-account-item-renderer")
    try:
        expect(items.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    except PlaywrightTimeoutError:
        console.print("- No channel items were visible on the channel switcher.", "yellow")
        return []

    handles: list[str] = []
    seen: set[str] = set()
    last_count = -1
    last_total = -1
    stable_rounds = 0

    for _ in range(20):
        count = items.count()
        for index in range(count):
            item = items.nth(index)
            for candidate in extract_exact_handle_from_channel_item(item):
                handle = normalize_handle(candidate)
                if not handle:
                    continue

                key = handle.casefold()
                if key in seen:
                    continue

                seen.add(key)
                handles.append(handle)

        if count == last_count and len(handles) == last_total:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_count = count
            last_total = len(handles)

        if stable_rounds >= 3:
            break

        page.mouse.wheel(0, 900)
        page.wait_for_timeout(500)

    console.print()
    if handles:
        console.print("Detected channel handles:", "bold")
        for index, handle in enumerate(handles, start=1):
            console.print(f"{index}. {handle}")
    else:
        console.print("No channel handles were detected. Nothing to process.", "yellow")
    console.print()
    return handles


def select_channel_by_handle(page: Page, handle: str) -> None:
    console.print("- Opening channel switcher...")
    page.goto(CHANNEL_SWITCHER_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
    detect_and_wait_for_security_challenge(page)

    items = page.locator("ytd-account-item-renderer")
    try:
        expect(items.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    except PlaywrightTimeoutError as exc:
        raise StepFailure("channel_not_found", "No channel items were visible.") from exc

    wanted = normalize_channel_identifier(handle)
    select_by_handle = is_handle_identifier(wanted)
    exact_re = re.compile(rf"^{re.escape(wanted)}$", re.IGNORECASE)
    available: list[str] = []
    scanned_keys: set[str] = set()
    last_count = -1
    stable_scrolls = 0

    for _ in range(10):
        count = items.count()
        for index in range(count):
            item = items.nth(index)
            candidates = (
                extract_exact_handle_from_channel_item(item)
                if select_by_handle
                else extract_channel_titles_from_channel_item(item)
            )
            for candidate in candidates:
                if candidate.casefold() not in scanned_keys:
                    scanned_keys.add(candidate.casefold())
                    available.append(candidate)
                matched = (
                    bool(exact_re.fullmatch(candidate.strip()))
                    if select_by_handle
                    else candidate.strip() == wanted
                )
                if matched:
                    if select_by_handle:
                        text_locator = item.locator(
                            f"xpath=.//yt-formatted-string[@secondary and normalize-space()={xpath_text_literal(wanted)}]"
                        ).first
                    else:
                        text_locator = item.locator(
                            f"xpath=.//yt-formatted-string[@id='channel-title' and normalize-space()={xpath_text_literal(wanted)}]"
                        ).first
                    expect(text_locator).to_be_visible(timeout=10000)
                    account_row = text_locator.locator(
                        "xpath=./ancestor::ytd-account-item-renderer[1]"
                    ).first
                    clickable = account_row.locator(
                        "xpath=.//tp-yt-paper-icon-item[@role='option']"
                    ).first
                    clickable.scroll_into_view_if_needed(timeout=10000)
                    expect(clickable).to_be_visible(timeout=10000)
                    try:
                        clickable.click(timeout=15000)
                    except Exception:
                        page.evaluate(
                            "(el) => el && el.click()",
                            clickable.element_handle(),
                        )
                    wait_for_soft_network_idle(page)
                    page.wait_for_timeout(1000)
                    console.print("- Channel selected successfully.")
                    page.wait_for_timeout(1000)
                    return

        if count == last_count:
            stable_scrolls += 1
        else:
            stable_scrolls = 0
            last_count = count

        if stable_scrolls >= 2:
            break

        page.mouse.wheel(0, 900)
        page.wait_for_timeout(500)

    if available:
        label = "handles" if select_by_handle else "titles"
        console.print(f"- Visible {label} scanned: " + ", ".join(available[:12]), "dim")
    label = "handle" if select_by_handle else "title"
    raise StepFailure("channel_not_found", f"Exact channel {label} not found: {wanted}")


def detect_youtube_error_page(page: Page) -> None:
    """
    Detect the YouTube permission/error page and redirect to studio.youtube.com
    if p#error-text and img.error-image exist.
    """
    try:
        if page.locator("p#error-text").count() > 0 and page.locator("img.error-image").count() > 0:
            console.print("- YouTube error/permission page detected. Redirecting to studio...", "yellow")
            page.goto("https://studio.youtube.com/", wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
            page.wait_for_timeout(1000)
    except Exception:
        pass


def dismiss_studio_overlays(page: Page) -> None:
    """
    Dismiss any open dialogs/overlays before interacting with elements below them.
    """
    try:
        backdrop = page.locator("tp-yt-iron-overlay-backdrop[opened]")
        if backdrop.count() > 0 and backdrop.first.is_visible(timeout=500):
            console.print("- Handling open overlay before publishing...", "yellow")
            
            dialog_buttons = [
                # Language-agnostic CSS selectors (IDs and attributes)
                page.locator("ytcp-button#dismiss-button button"),
                page.locator("ytcp-button#action-button button"),
                page.locator("ytcp-button#close-button button"),
                page.locator("tp-yt-paper-dialog ytcp-button[type='filled'] button"),
                page.locator("ytcp-dialog ytcp-button[type='filled'] button"),
                # Fallback explicit labels just in case
                page.locator("button[aria-label='Continue']"),
                page.locator("button[aria-label='متابعة']"),
                page.locator("button[aria-label='OK']"),
                page.locator("button[aria-label='حسنًا']"),
            ]
            
            clicked = False
            for btn in dialog_buttons:
                try:
                    if btn.count() > 0 and btn.first.is_visible(timeout=500):
                        btn.first.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue
                    
            if clicked:
                try:
                    backdrop.first.wait_for(state="hidden", timeout=5000)
                except Exception:
                    pass
    except Exception:
        pass


def get_current_studio_channel_id(page: Page) -> str | None:
    match = re.search(r"/channel/([^/?#]+)", page.url)
    if match:
        return match.group(1)
    try:
        channel_id = page.evaluate(
            """
            () => {
                const values = [location.href];
                for (const a of document.querySelectorAll('a[href]')) {
                    values.push(a.href || a.getAttribute('href') || '');
                }
                for (const script of document.querySelectorAll('script')) {
                    const text = script.textContent || '';
                    if (text.includes('/channel/UC')) values.push(text.slice(0, 300000));
                }
                for (const value of values) {
                    const match = String(value).match(/\\/channel\\/(UC[A-Za-z0-9_-]+)/);
                    if (match) return match[1];
                }
                return null;
            }
            """
        )
        if channel_id:
            return str(channel_id)
    except Exception:
        pass
    return None


def wait_for_studio_channel_id(page: Page, timeout: int = 30000) -> str | None:
    deadline = time.monotonic() + (timeout / 1000)
    while time.monotonic() < deadline:
        channel_id = get_current_studio_channel_id(page)
        if channel_id:
            return channel_id
        wait_for_soft_network_idle(page, timeout=1000)
        page.wait_for_timeout(500)
    return None


def is_basic_info_editing_url(url: str) -> bool:
    return "/editing/profile" in url or "/editing/details" in url


def optional_click_visible(page: Page, selector: str, timeout: int = 3000) -> bool:
    locator = page.locator(selector).first
    try:
        expect(locator).to_be_visible(timeout=timeout)
    except Exception:
        return False

    try:
        locator.scroll_into_view_if_needed(timeout=3000)
        locator.click(timeout=5000)
        page.wait_for_timeout(500)
        return True
    except Exception:
        try:
            page.evaluate("(el) => el && el.click()", locator.element_handle())
            page.wait_for_timeout(500)
            return True
        except Exception:
            return False


def fill_optional_textarea_in(root: Page | Locator, selector: str, value: str, timeout: int = 5000) -> bool:
    field = root.locator(selector).first
    try:
        expect(field).to_be_visible(timeout=timeout)
    except Exception:
        return False

    fill_input(field, value)
    actual = read_input_value(field)
    if actual != value:
        raise StepFailure("livestreaming_failed", f"Could not fill field {selector}. Current value: {actual!r}")
    return True


def fill_optional_textarea(page: Page, selector: str, value: str, timeout: int = 5000) -> bool:
    return fill_optional_textarea_in(page, selector, value, timeout=timeout)


def checkbox_is_checked(locator: Locator) -> bool:
    try:
        return bool(
            locator.evaluate(
                """
                el => {
                    if (el.getAttribute('aria-checked') === 'true') return true;
                    if (el.hasAttribute('checked')) return true;
                    if (el.checked === true) return true;
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


def check_optional_checkbox_in(root: Page | Locator, timeout: int = 5000) -> bool:
    checkbox = root.locator("ytcp-checkbox-lit#confirm-checkbox").last
    try:
        expect(checkbox).to_be_visible(timeout=timeout)
    except Exception:
        return False

    if checkbox_is_checked(checkbox):
        return True

    checkbox.scroll_into_view_if_needed(timeout=3000)
    checkbox.click(timeout=5000)
    return True


def check_optional_checkbox(page: Page, selector: str, timeout: int = 5000) -> bool:
    return check_optional_checkbox_in(page, timeout=timeout)


def find_visible_livestreaming_dialog(page: Page, timeout: int = 5000) -> Locator | None:
    dialog = (
        page.locator("ytcp-dialog:visible")
        .filter(has=page.locator("ytcp-form-textarea#full-name-input textarea"))
        .last
    )
    try:
        expect(dialog).to_be_visible(timeout=timeout)
        return dialog
    except Exception:
        return None


def handle_livestreaming_dialog_if_present(page: Page) -> bool:
    opened = optional_click_visible(
        page,
        "ytcp-dialog:visible ytcp-button#error-dialog-end-aligned-action button:visible",
        timeout=5000,
    )
    if not opened:
        opened = optional_click_visible(
            page,
            "ytcp-button#error-dialog-end-aligned-action button:visible",
            timeout=2500,
        )

    dialog = find_visible_livestreaming_dialog(page, timeout=5000 if opened else 2500)
    root: Page | Locator = dialog if dialog is not None else page

    form_seen = False
    form_seen = fill_optional_textarea_in(
        root,
        "ytcp-form-textarea#full-name-input textarea",
        LIVE_FULL_NAME,
        timeout=5000 if opened else 2500,
    ) or form_seen
    form_seen = fill_optional_textarea_in(
        root,
        "ytcp-form-textarea#email-address-input textarea",
        BUSINESS_EMAIL,
        timeout=5000 if opened else 2500,
    ) or form_seen
    form_seen = fill_optional_textarea_in(
        root,
        "ytcp-form-textarea#company-name-input textarea",
        LIVE_COMPANY_NAME,
        timeout=5000 if opened else 2500,
    ) or form_seen

    if not form_seen:
        return opened

    check_optional_checkbox_in(root, timeout=5000)

    confirm = root.locator("ytcp-button#confirm-button button:visible").last
    try:
        expect(confirm).to_be_visible(timeout=10000)
        page.wait_for_function(
            """
            el => el && !el.disabled &&
                !el.hasAttribute('disabled') &&
                el.getAttribute('aria-disabled') !== 'true' &&
                !String(el.className || '').includes('disabled')
            """,
            arg=confirm.element_handle(),
            timeout=10000,
        )
        confirm.scroll_into_view_if_needed(timeout=3000)
        confirm.click(timeout=10000)
        wait_for_soft_network_idle(page)
        page.wait_for_timeout(1000)
    except Exception as exc:
        raise StepFailure("livestreaming_failed", f"Could not confirm livestreaming dialog: {exc}") from exc

    return True


def open_livestreaming_page(page: Page) -> str:
    console.print("- Opening Livestreaming page...")
    channel_id = get_current_studio_channel_id(page)
    if not channel_id:
        raise StepFailure("livestreaming_failed", "Could not extract channel ID from Studio URL.")

    livestreaming_url = f"https://studio.youtube.com/channel/{channel_id}/livestreaming"
    try:
        page.goto(livestreaming_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        detect_and_wait_for_security_challenge(page)
        wait_for_soft_network_idle(page)
        expect(page.locator("body")).to_be_visible(timeout=DEFAULT_TIMEOUT)
        if handle_livestreaming_dialog_if_present(page):
            console.print("- Livestreaming dialog status: completed")
    except Exception as exc:
        raise StepFailure("livestreaming_failed", f"Could not open livestreaming page: {exc}") from exc

    return livestreaming_url


def open_youtube_studio(page: Page) -> None:
    console.print("- Opening YouTube Studio via account menu...")

    try:
        # 1. Click the avatar button (language-independent selector)
        avatar_btn = page.locator("button#avatar-btn").first
        expect(avatar_btn).to_be_visible(timeout=15000)
        page.wait_for_timeout(500)
        avatar_btn.click(timeout=10000)
        page.wait_for_timeout(1000)

        # 2. Find the Studio link
        studio_link = page.locator('a#endpoint[href*="studio.youtube.com"]').first
        expect(studio_link).to_be_visible(timeout=10000)
        page.wait_for_timeout(300)
        
        # Click it and catch the new tab if YouTube opens one
        try:
            with page.context.expect_page(timeout=4000) as new_page_info:
                studio_link.click(timeout=10000)
                page.wait_for_timeout(1000)
            
            new_page = new_page_info.value
            console.print("- Studio opened in a new tab. Bringing it to the main tab...")
            new_page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
            target_url = new_page.url
            new_page.close()
            page.goto(target_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
            page.wait_for_timeout(1000)
            detect_youtube_error_page(page)
        except Exception:
            # If no new tab was opened, the click either navigated the current tab or failed.
            # We don't need to do anything here; the script will verify if we reached Studio.
            page.wait_for_timeout(1000)
            pass

    except Exception as exc:
        console.print(f"- Avatar/menu click failed ({exc}). Falling back to direct URL.", "yellow")
        try:
            page.goto(YOUTUBE_STUDIO_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        except PlaywrightTimeoutError as goto_exc:
            raise StepFailure("studio_load_failed", "YouTube Studio timed out.") from goto_exc

    detect_and_wait_for_security_challenge(page)

    shell_candidates = [
        page.locator("ytcp-app"),
        page.locator("ytcp-navigation-drawer"),
        page.locator("ytcp-header"),
        page.locator('a[href*="/editing"]'),
        page.get_by_text(make_exact_text_regex(EN_AR["customization"])),
    ]

    for locator in shell_candidates:
        try:
            expect(locator.first).to_be_visible(timeout=15000)
            page.wait_for_timeout(1000)
            dismiss_studio_overlays(page)
            return
        except Exception:
            continue

    if "studio.youtube.com" in page.url and "signin" not in page.url.lower():
        page.wait_for_timeout(1000)
        dismiss_studio_overlays(page)
        return

    raise StepFailure("studio_load_failed", "Could not detect YouTube Studio shell.")



def first_visible_locator(locators: Iterable[Locator], timeout: int = 3000) -> Locator | None:
    for locator in locators:
        try:
            candidate = locator.first
            expect(candidate).to_be_visible(timeout=timeout)
            return candidate
        except Exception:
            continue
    return None


def click_named_button_or_link(
    page: Page,
    names: list[str],
    timeout: int = 12000,
    scroll: bool = True,
) -> bool:
    exact = make_exact_text_regex(names)
    deadline = time.monotonic() + (timeout / 1000)

    while time.monotonic() < deadline:
        locators = [
            page.get_by_role("button", name=exact),
            page.get_by_role("link", name=exact),
            page.locator("ytcp-button, button, tp-yt-paper-button").filter(has_text=exact),
            page.locator("a, tp-yt-paper-item, ytcp-ve").filter(has_text=exact),
        ]
        locator = first_visible_locator(locators, timeout=1000)
        if locator:
            locator.scroll_into_view_if_needed(timeout=5000)
            locator.click(timeout=10000)
            return True

        if not scroll:
            break
        page.mouse.wheel(0, 700)
        page.wait_for_timeout(350)

    return False


def open_customization(page: Page) -> None:
    console.print("- Opening Customization...")

    clicked = click_named_button_or_link(
        page,
        EN_AR["customization"],
        timeout=12000,
        scroll=False,
    )
    if clicked:
        wait_for_soft_network_idle(page)

    channel_id = get_current_studio_channel_id(page)
    if channel_id:
        if RUN_MODE == "logo":
            details_url = f"https://studio.youtube.com/channel/{channel_id}/editing/profile"
        else:
            details_url = f"https://studio.youtube.com/channel/{channel_id}/editing/details"
        page.goto(details_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        detect_youtube_error_page(page)
        detect_and_wait_for_security_challenge(page)
    elif not clicked:
        edit_link = first_visible_locator([page.locator('a[href*="/editing"]')], timeout=10000)
        if edit_link:
            edit_link.click(timeout=10000)
        else:
            raise StepFailure("customization_not_found", "Customization link was not found.")

    wait_for_soft_network_idle(page)
    if body_contains_any(page, EN_AR["customization"] + EN_AR["basic_info"] + EN_AR["links"], timeout=12000):
        return
    body = page.locator("body")
    expected = re.compile(
        r"(Customization|Basic info|Profile|Links|التخصيص|معلومات أساسية|المعلومات الأساسية|روابط)",
        re.IGNORECASE,
    )
    try:
        expect(body).to_contain_text(expected, timeout=DEFAULT_TIMEOUT)
    except PlaywrightTimeoutError as exc:
        raise StepFailure("customization_not_found", "Customization page did not load.") from exc

    page.wait_for_timeout(1000)
    dismiss_studio_overlays(page)


def marker_locator(page: Page, marker: str) -> Locator:
    return page.locator(f'[data-qaff-marker="{marker}"]').first


def find_input_by_patterns(
    page: Page,
    field_patterns: list[str],
    context_patterns: list[str] | None = None,
    prefer_empty: bool | None = None,
    scroll_steps: int = 0,
) -> Locator | None:
    context_patterns = context_patterns or []

    for step in range(scroll_steps + 1):
        marker = f"qaff_{time.time_ns()}"
        found = page.evaluate(
            """
            ({ fieldPatterns, contextPatterns, preferEmpty, marker }) => {
                const fieldRegexes = fieldPatterns.map((p) => new RegExp(p, 'iu'));
                const contextRegexes = contextPatterns.map((p) => new RegExp(p, 'iu'));

                function allDeep(root, selector) {
                    const found = [];
                    const walk = (node) => {
                        if (!node) return;
                        if (node.querySelectorAll) {
                            found.push(...node.querySelectorAll(selector));
                        }
                        const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                        for (const child of children) {
                            if (child.shadowRoot) walk(child.shadowRoot);
                        }
                    };
                    walk(root);
                    return [...new Set(found)];
                }

                const inputs = allDeep(
                    document,
                    'input, textarea, [contenteditable="true"], [role="textbox"]'
                );

                function visible(el) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' &&
                        style.display !== 'none';
                }

                function disabled(el) {
                    return el.disabled ||
                        el.getAttribute('aria-disabled') === 'true' ||
                        el.hasAttribute('disabled');
                }

                function parentLike(el) {
                    if (el.parentElement) return el.parentElement;
                    const root = el.getRootNode && el.getRootNode();
                    return root && root.host ? root.host : null;
                }

                function fieldValue(el) {
                    if (el.value !== undefined) return el.value || '';
                    return el.innerText || el.textContent || '';
                }

                function labelText(el) {
                    const bits = [];
                    for (const attr of ['aria-label', 'placeholder', 'name', 'id', 'title', 'type', 'label']) {
                        bits.push(el.getAttribute(attr) || '');
                    }
                    if (el.labels) {
                        for (const label of el.labels) bits.push(label.innerText || '');
                    }
                    let parent = parentLike(el);
                    for (let i = 0; parent && i < 7; i++, parent = parentLike(parent)) {
                        bits.push((parent.innerText || '').slice(0, 1500));
                        for (const attr of ['aria-label', 'placeholder', 'label', 'id', 'name']) {
                            bits.push(parent.getAttribute ? parent.getAttribute(attr) || '' : '');
                        }
                    }
                    return bits.join('\\n');
                }

                function directText(el) {
                    const bits = [];
                    for (const attr of ['aria-label', 'placeholder', 'name', 'id', 'title', 'type', 'label']) {
                        bits.push(el.getAttribute(attr) || '');
                    }
                    if (el.labels) {
                        for (const label of el.labels) bits.push(label.innerText || '');
                    }
                    return bits.join('\\n');
                }

                let best = null;
                let bestScore = -999;

                for (const el of inputs) {
                    if (!visible(el) || disabled(el)) continue;
                    const haystack = labelText(el);
                    const direct = directText(el);
                    const value = fieldValue(el).trim();
                    let score = 0;

                    if (fieldRegexes.some((rx) => rx.test(direct))) score += 40;
                    if (fieldRegexes.some((rx) => rx.test(haystack))) score += 18;
                    if (contextRegexes.length && contextRegexes.some((rx) => rx.test(haystack))) {
                        score += 12;
                    }

                    if (preferEmpty === true) score += value ? -10 : 8;
                    if (preferEmpty === false && value) score += 3;

                    if (score > bestScore) {
                        best = el;
                        bestScore = score;
                    }
                }

                if (!best || bestScore < 18) return false;
                best.setAttribute('data-qaff-marker', marker);
                return true;
            }
            """,
            {
                "fieldPatterns": field_patterns,
                "contextPatterns": context_patterns,
                "preferEmpty": prefer_empty,
                "marker": marker,
            },
        )

        if found:
            locator = marker_locator(page, marker)
            try:
                expect(locator).to_be_visible(timeout=5000)
                return locator
            except Exception:
                pass

        if step < scroll_steps:
            page.mouse.wheel(0, 650)
            page.wait_for_timeout(350)

    return None


def read_input_value(locator: Locator) -> str:
    try:
        return locator.input_value(timeout=3000).strip()
    except Exception:
        try:
            value = locator.evaluate(
                "el => ((el.value !== undefined ? el.value : (el.innerText || el.textContent)) || '').trim()"
            )
            return str(value).strip()
        except Exception:
            return ""


def locator_is_enabled(locator: Locator) -> bool:
    try:
        return bool(
            locator.evaluate(
                """
                el => {
                    const disabled = el.disabled ||
                        el.hasAttribute('disabled') ||
                        el.getAttribute('aria-disabled') === 'true' ||
                        (String(el.className || '').includes('disabled'));
                    return !disabled;
                }
                """
            )
        )
    except Exception:
        try:
            return locator.is_enabled(timeout=1000)
        except Exception:
            return False


def fill_input(locator: Locator, value: str) -> None:
    locator.scroll_into_view_if_needed(timeout=5000)
    expect(locator).to_be_visible(timeout=10000)
    if not locator_is_enabled(locator):
        raise RuntimeError("Field is not editable.")
    try:
        locator.fill(value, timeout=10000)
    except Exception:
        locator.click(timeout=10000)
        try:
            locator.press("Control+A", timeout=5000)
            locator.press("Backspace", timeout=5000)
            locator.type(value, timeout=10000)
        except Exception:
            locator.evaluate(
                """
                (el, value) => {
                    if (el.value !== undefined) {
                        el.value = value;
                    } else {
                        el.textContent = value;
                    }
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                value,
            )
    try:
        expect(locator).to_have_value(value, timeout=5000)
    except Exception:
        pass


def target_url_terms(target_url: str) -> tuple[str, str]:
    parsed = urlparse(target_url)
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.lower().removeprefix("www.")
    return target_url.lower().rstrip("/"), host


def link_exists(page: Page, target_url: str) -> bool:
    normalized_url, host = target_url_terms(target_url)
    try:
        return bool(
            page.evaluate(
                """
                ({ normalizedUrl, host }) => {
                    const values = [];

                    function allDeep(root, selector) {
                        const found = [];
                        const walk = (node) => {
                            if (!node) return;
                            if (node.querySelectorAll) {
                                found.push(...node.querySelectorAll(selector));
                            }
                            const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                            for (const child of children) {
                                if (child.shadowRoot) walk(child.shadowRoot);
                            }
                        };
                        walk(root);
                        return [...new Set(found)];
                    }

                    for (const el of allDeep(
                        document,
                        'input.ytcpChannelLinkItemFormInput:not(.ytcpChannelLinkItemTitleInput), a[href]'
                    )) {
                        values.push(el.value || el.innerText || el.textContent || '');
                        values.push(el.getAttribute('href') || '');
                    }

                    return values.some((value) => {
                        const text = String(value).toLowerCase().trim().replace(/^https?:\\/\\//, '').replace(/\\/+$/, '');
                        const target = String(normalizedUrl).toLowerCase().trim().replace(/^https?:\\/\\//, '').replace(/\\/+$/, '');
                        return text === target || text.includes(target) || text.includes(host);
                    });
                }
                """,
                {"normalizedUrl": normalized_url, "host": host},
            )
        )
    except Exception:
        return False


def text_or_field_value_exists(page: Page, expected: str, exact: bool = False) -> bool:
    expected_norm = expected.strip().casefold()
    if not expected_norm:
        return False
    try:
        return bool(
            page.evaluate(
                """
                ({ expected, exact }) => {
                    const values = [];

                    function allDeep(root, selector) {
                        const found = [];
                        const walk = (node) => {
                            if (!node) return;
                            if (node.querySelectorAll) {
                                found.push(...node.querySelectorAll(selector));
                            }
                            const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                            for (const child of children) {
                                if (child.shadowRoot) walk(child.shadowRoot);
                            }
                        };
                        walk(root);
                        return [...new Set(found)];
                    }

                    for (const el of allDeep(document, 'input, textarea, [contenteditable="true"], [role="textbox"]')) {
                        values.push(el.value || el.innerText || el.textContent || '');
                    }
                    values.push(document.body ? document.body.innerText || '' : '');

                    const target = String(expected).trim().toLocaleLowerCase();
                    return values.some((value) => {
                        const text = String(value).trim().toLocaleLowerCase();
                        return exact ? text === target : text.includes(target);
                    });
                }
                """,
                {"expected": expected_norm, "exact": exact},
            )
        )
    except Exception:
        return False


def scroll_to_section(page: Page, names: list[str], timeout: int = 12000) -> bool:
    deadline = time.monotonic() + (timeout / 1000)
    while time.monotonic() < deadline:
        found = page.evaluate(
            """
            ({ names }) => {
                const exact = names.map((name) => String(name).trim().toLocaleLowerCase());

                function allDeep(root, selector) {
                    const found = [];
                    const walk = (node) => {
                        if (!node) return;
                        if (node.querySelectorAll) {
                            found.push(...node.querySelectorAll(selector));
                        }
                        const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                        for (const child of children) {
                            if (child.shadowRoot) walk(child.shadowRoot);
                        }
                    };
                    walk(root);
                    return [...new Set(found)];
                }

                function visible(el) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' &&
                        style.display !== 'none';
                }

                const candidates = allDeep(document, 'h1,h2,h3,h4,div,span,ytcp-ve')
                    .filter(visible)
                    .map((el) => ({
                        el,
                        text: (el.innerText || el.textContent || '').trim(),
                        rect: el.getBoundingClientRect(),
                    }))
                    .filter((item) => item.text && item.text.length <= 80);

                const match = candidates
                    .filter((item) => exact.includes(item.text.toLocaleLowerCase()))
                    .sort((a, b) => a.text.length - b.text.length || a.rect.top - b.rect.top)[0];

                if (!match) return false;
                match.el.scrollIntoView({ block: 'center', inline: 'nearest' });
                return true;
            }
            """,
            {"names": names},
        )
        if found:
            page.wait_for_timeout(500)
            return True
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(350)
    return False


def business_email_exists(page: Page, email: str) -> bool:
    return text_or_field_value_exists(page, email, exact=False)


def collect_visible_validation_errors(page: Page) -> list[str]:
    try:
        errors = page.evaluate(
            """
            () => {
                const snippets = [];
                const rx = /(invalid|required|enter a valid|not valid|error|خطأ|غير صالح|مطلوب|أدخل)/iu;
                const selectors = [
                    '[role="alert"]',
                    '[aria-invalid="true"]',
                    '.error',
                    '.error-message',
                    'ytcp-form-input-container[invalid]',
                    'tp-yt-paper-input-container[invalid]',
                ];
                for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text) snippets.push(text);
                    }
                }
                const bodyText = document.body ? document.body.innerText || '' : '';
                for (const line of bodyText.split(/\\r?\\n/)) {
                    const trimmed = line.trim();
                    if (trimmed && rx.test(trimmed)) snippets.push(trimmed);
                }
                return [...new Set(snippets)].slice(0, 8);
            }
            """
        )
        return [str(item) for item in errors if str(item).strip()]
    except Exception:
        return []


def verify_basic_info_saved(
    page: Page,
    expect_link: bool,
    expect_email: bool,
) -> tuple[bool, bool, list[str]]:
    console.print("- Verifying saved basic info...")
    page.wait_for_timeout(1000)
    try:
        open_customization_tab(page, "details")
        page.reload(wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        detect_and_wait_for_security_challenge(page)
        wait_for_soft_network_idle(page)
    except Exception:
        pass

    if expect_link:
        scroll_to_section(page, ["Links", "روابط"], timeout=10000)
    link_ok = True if not expect_link else link_exists(page, LINK_URL)

    if expect_email:
        scroll_to_section(
            page,
            [
                "Contact info",
                "Business email",
                "Email address",
                "معلومات الاتصال",
                "البريد الإلكتروني",
                "عنوان البريد الإلكتروني",
            ],
            timeout=10000,
        )
    email_ok = True if not expect_email else business_email_exists(page, BUSINESS_EMAIL)
    return link_ok, email_ok, collect_visible_validation_errors(page)


def mark_existing_editable_fields(page: Page) -> None:
    try:
        page.evaluate(
            """
            () => {
                function allDeep(root, selector) {
                    const found = [];
                    const walk = (node) => {
                        if (!node) return;
                        if (node.querySelectorAll) {
                            found.push(...node.querySelectorAll(selector));
                        }
                        const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                        for (const child of children) {
                            if (child.shadowRoot) walk(child.shadowRoot);
                        }
                    };
                    walk(root);
                    return [...new Set(found)];
                }
                for (const el of allDeep(document, 'input, textarea, [contenteditable="true"], [role="textbox"]')) {
                    el.setAttribute('data-qaff-existing-before-add-link', '1');
                }
            }
            """
        )
    except Exception:
        pass


def find_new_link_fields(page: Page) -> tuple[Locator | None, Locator | None]:
    marker_title = f"qaff_link_title_{time.time_ns()}"
    marker_url = f"qaff_link_url_{time.time_ns()}"
    found = page.evaluate(
        """
        ({ markerTitle, markerUrl }) => {
            const linkHeadingRx = /^(Links|روابط)$/iu;
            const contactHeadingRx = /^(Contact info|Business email|Email address|البريد الإلكتروني|عنوان البريد الإلكتروني|معلومات الاتصال)$/iu;

            function allDeep(root, selector) {
                const found = [];
                const walk = (node) => {
                    if (!node) return;
                    if (node.querySelectorAll) {
                        found.push(...node.querySelectorAll(selector));
                    }
                    const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                    for (const child of children) {
                        if (child.shadowRoot) walk(child.shadowRoot);
                    }
                };
                walk(root);
                return [...new Set(found)];
            }

            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none';
            }

            function docTop(rect) {
                return rect.top + window.scrollY;
            }

            function disabled(el) {
                return el.disabled ||
                    el.getAttribute('aria-disabled') === 'true' ||
                    el.hasAttribute('disabled');
            }

            function valueOf(el) {
                if (el.value !== undefined) return el.value || '';
                return el.innerText || el.textContent || '';
            }

            function parentLike(el) {
                if (el.parentElement) return el.parentElement;
                const root = el.getRootNode && el.getRootNode();
                return root && root.host ? root.host : null;
            }

            function textAround(el) {
                const bits = [];
                for (const attr of ['aria-label', 'placeholder', 'name', 'id', 'title', 'type', 'label']) {
                    bits.push(el.getAttribute(attr) || '');
                }
                let parent = parentLike(el);
                for (let i = 0; parent && i < 7; i++, parent = parentLike(parent)) {
                    bits.push((parent.innerText || '').slice(0, 1200));
                    for (const attr of ['aria-label', 'placeholder', 'name', 'id', 'title', 'label']) {
                        bits.push(parent.getAttribute ? parent.getAttribute(attr) || '' : '');
                    }
                }
                return bits.join('\\n');
            }

            function findHeading(rx) {
                return allDeep(document, 'h1,h2,h3,h4,div,span,ytcp-ve')
                    .filter(visible)
                    .map((el) => ({
                        el,
                        text: (el.innerText || el.textContent || '').trim(),
                        rect: el.getBoundingClientRect(),
                    }))
                    .filter((item) => item.text && item.text.length <= 80 && rx.test(item.text))
                    .sort((a, b) => docTop(a.rect) - docTop(b.rect))[0] || null;
            }

            const linksHeading = findHeading(linkHeadingRx);
            const contactHeading = findHeading(contactHeadingRx);
            const lowerBound = linksHeading ? docTop(linksHeading.rect) - 20 : -Infinity;
            const upperBound = contactHeading && docTop(contactHeading.rect) > lowerBound
                ? docTop(contactHeading.rect) - 8
                : Infinity;

            const fields = allDeep(
                document,
                'input, textarea, [contenteditable="true"], [role="textbox"]'
            )
                .filter((el) => visible(el) && !disabled(el))
                .map((el) => ({
                    el,
                    text: textAround(el),
                    value: valueOf(el).trim(),
                    type: (el.getAttribute('type') || '').toLowerCase(),
                    existed: el.getAttribute('data-qaff-existing-before-add-link') === '1',
                    rect: el.getBoundingClientRect(),
                }))
                .filter((f) => {
                    const top = docTop(f.rect);
                    return top >= lowerBound && top < upperBound;
                });

            const emailRx = /(Business email|Email address|\\bEmail\\b|Contact info|البريد الإلكتروني|عنوان البريد الإلكتروني|معلومات الاتصال)/iu;
            const urlRx = /(Enter URL|\\bURL\\b|إدخال عنوان URL|عنوان URL|https?:\\/\\/|www\\.|\\.com|\\.net)/iu;
            const titleRx = /(Enter title|Link title|\\bTitle\\b|إدخال عنوان|عنوان الرابط)/iu;

            let url = null;
            let title = null;

            const cleanFields = fields.filter((f) => {
                if (f.type === 'email') return false;
                if (emailRx.test(f.text)) return false;
                if (/@/.test(f.value)) return false;
                return true;
            });

            const emptyFields = cleanFields.filter((f) => !f.value);
            const newEmptyFields = emptyFields.filter((f) => !f.existed);
            const candidates = newEmptyFields.length >= 2 ? newEmptyFields : emptyFields;

            const rows = new Map();
            for (const f of candidates) {
                const key = Math.round(docTop(f.rect) / 24) * 24;
                if (!rows.has(key)) rows.set(key, []);
                rows.get(key).push(f);
            }

            const pairedRows = [...rows.values()]
                .map((row) => row.sort((a, b) => a.rect.left - b.rect.left))
                .filter((row) => row.length >= 2)
                .sort((a, b) => b[0].rect.top - a[0].rect.top);

            for (const row of pairedRows) {
                const likely = row.slice(0, 2);
                const rowText = likely.map((f) => f.text).join('\\n');
                if (!emailRx.test(rowText)) {
                    title = likely.find((f) => titleRx.test(f.text)) || likely[0];
                    url = likely.find((f) => urlRx.test(f.text)) || likely.find((f) => f.el !== title.el) || likely[1];
                    break;
                }
            }

            const urlCandidates = emptyFields
                .filter((f) => urlRx.test(f.text))
                .sort((a, b) => b.rect.top - a.rect.top);

            if ((!url || !title) && urlCandidates.length) {
                url = urlCandidates[0];
                const before = emptyFields
                    .filter((f) => f.el !== url.el && f.rect.top <= url.rect.top + 20)
                    .sort((a, b) => b.rect.top - a.rect.top);
                title = before.find((f) => titleRx.test(f.text)) || before[0] || null;
            }

            if (!url || !title) {
                const linkish = candidates
                    .filter((f) => /(Links|روابط|Enter title|Enter URL|Link title|URL|إدخال عنوان)/iu.test(f.text))
                    .sort((a, b) => {
                        if (Math.abs(docTop(a.rect) - docTop(b.rect)) > 8) return docTop(b.rect) - docTop(a.rect);
                        return a.rect.left - b.rect.left;
                    });
                if (linkish.length >= 2) {
                    const pair = linkish.slice(0, 2).sort((a, b) => a.rect.left - b.rect.left || docTop(a.rect) - docTop(b.rect));
                    title = title || pair.find((f) => titleRx.test(f.text)) || pair[0];
                    url = url || pair.find((f) => urlRx.test(f.text)) || pair.find((f) => f.el !== title.el) || pair[1];
                }
            }

            if (!title || !url || title.el === url.el) return false;
            title.el.setAttribute('data-qaff-marker', markerTitle);
            url.el.setAttribute('data-qaff-marker', markerUrl);
            return true;
        }
        """,
        {"markerTitle": marker_title, "markerUrl": marker_url},
    )

    if not found:
        return None, None

    title_locator = marker_locator(page, marker_title)
    url_locator = marker_locator(page, marker_url)
    try:
        expect(title_locator).to_be_visible(timeout=5000)
        expect(url_locator).to_be_visible(timeout=5000)
        return title_locator, url_locator
    except Exception:
        return None, None


def click_add_link_icon(page: Page) -> bool:
    marker = f"qaff_add_link_{time.time_ns()}"
    found = page.evaluate(
        """
        ({ marker }) => {
            const linkHeadingRx = /^(Links|روابط)$/iu;
            const stopHeadingRx = /^(Contact info|Business email|Email address|البريد الإلكتروني|عنوان البريد الإلكتروني|معلومات الاتصال)$/iu;

            function allDeep(root, selector) {
                const found = [];
                const walk = (node) => {
                    if (!node) return;
                    if (node.querySelectorAll) {
                        found.push(...node.querySelectorAll(selector));
                    }
                    const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                    for (const child of children) {
                        if (child.shadowRoot) walk(child.shadowRoot);
                    }
                };
                walk(root);
                return [...new Set(found)];
            }

            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none';
            }

            function disabled(el) {
                return el.disabled ||
                    el.hasAttribute('disabled') ||
                    el.getAttribute('aria-disabled') === 'true';
            }

            function docTop(rect) {
                return rect.top + window.scrollY;
            }

            function textOf(el) {
                return [
                    el.innerText || el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('label') || '',
                    el.getAttribute('id') || '',
                    el.getAttribute('class') || '',
                ].join('\\n');
            }

            function findHeading(rx) {
                return allDeep(document, 'h1,h2,h3,h4,div,span,ytcp-ve')
                    .filter(visible)
                    .map((el) => ({
                        el,
                        text: (el.innerText || el.textContent || '').trim(),
                        rect: el.getBoundingClientRect(),
                    }))
                    .filter((item) => item.text && item.text.length <= 80 && rx.test(item.text))
                    .sort((a, b) => docTop(a.rect) - docTop(b.rect))[0] || null;
            }

            const linkHeading = findHeading(linkHeadingRx);
            if (!linkHeading) return false;
            const lowerBound = docTop(linkHeading.rect) - 20;
            const stopHeading = allDeep(document, 'h1,h2,h3,h4,div,span,ytcp-ve')
                .filter(visible)
                .map((el) => ({
                    text: (el.innerText || el.textContent || '').trim(),
                    rect: el.getBoundingClientRect(),
                }))
                .filter((item) => item.text && item.text.length <= 100 && stopHeadingRx.test(item.text) && docTop(item.rect) > lowerBound + 5)
                .sort((a, b) => docTop(a.rect) - docTop(b.rect))[0] || null;
            const upperBound = stopHeading ? docTop(stopHeading.rect) - 8 : lowerBound + 600;

            const clickables = allDeep(
                document,
                'button, ytcp-button, ytcp-icon-button, tp-yt-paper-icon-button, yt-icon-button, [role="button"], a'
            )
                .filter((el) => visible(el) && !disabled(el))
                .map((el) => ({
                    el,
                    text: textOf(el),
                    rect: el.getBoundingClientRect(),
                    hasTouchFeedback: !!el.querySelector('yt-touch-feedback-shape'),
                    hasAddIcon: !!el.querySelector('yt-icon[icon*="add"], iron-icon[icon*="add"], .add-icon, #add-icon'),
                }))
                .filter((item) => {
                    const top = docTop(item.rect);
                    return top >= lowerBound && top < upperBound;
                });

            let best = null;
            let bestScore = -999;
            for (const item of clickables) {
                let score = 0;
                if (/(Add link|إضافة رابط)/iu.test(item.text)) score += 120;
                if (/(add|plus|إضافة)/iu.test(item.text)) score += 40;
                if (item.hasAddIcon) score += 40;
                if (item.hasTouchFeedback) score += 15;
                if (item.rect.width <= 80 && item.rect.height <= 80) score += 10;
                score += Math.max(0, 80 - Math.abs(item.rect.top - linkHeading.rect.top));
                if (score > bestScore) {
                    best = item;
                    bestScore = score;
                }
            }

            if (!best || bestScore < 20) return false;
            best.el.setAttribute('data-qaff-marker', marker);
            best.el.scrollIntoView({ block: 'center', inline: 'nearest' });
            return true;
        }
        """,
        {"marker": marker},
    )

    if not found:
        return False

    button = marker_locator(page, marker)
    try:
        expect(button).to_be_visible(timeout=5000)
        button.click(timeout=10000)
        page.wait_for_timeout(700)
        return True
    except Exception:
        try:
            page.evaluate(
                """
                (marker) => {
                    const el = document.querySelector(`[data-qaff-marker="${marker}"]`);
                    if (!el) return false;
                    el.click();
                    return true;
                }
                """,
                marker,
            )
            page.wait_for_timeout(700)
            return True
        except Exception:
            return False


def find_business_email_field(page: Page) -> Locator | None:
    marker = f"qaff_business_email_{time.time_ns()}"
    scroll_to_section(
        page,
        [
            "Contact info",
            "Business email",
            "Email address",
            "معلومات الاتصال",
            "البريد الإلكتروني",
            "عنوان البريد الإلكتروني",
        ],
        timeout=12000,
    )
    found = page.evaluate(
        """
        ({ marker }) => {
            const contactHeadingRx = /^(Contact info|Business email|Email address|معلومات الاتصال|البريد الإلكتروني|عنوان البريد الإلكتروني)$/iu;
            const nextHeadingRx = /^(Links|روابط|Channel URL|Handle|Pronouns|الروابط|عنوان URL للقناة|المعرّف)$/iu;
            const emailRx = /(Business email|Email address|\\bEmail\\b|البريد الإلكتروني|عنوان البريد الإلكتروني)/iu;
            const urlRx = /(Enter URL|\\bURL\\b|Link title|Links|روابط|عنوان URL|رابط)/iu;

            function allDeep(root, selector) {
                const found = [];
                const walk = (node) => {
                    if (!node) return;
                    if (node.querySelectorAll) {
                        found.push(...node.querySelectorAll(selector));
                    }
                    const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                    for (const child of children) {
                        if (child.shadowRoot) walk(child.shadowRoot);
                    }
                };
                walk(root);
                return [...new Set(found)];
            }

            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none';
            }

            function disabled(el) {
                return el.disabled ||
                    el.getAttribute('aria-disabled') === 'true' ||
                    el.hasAttribute('disabled');
            }

            function docTop(rect) {
                return rect.top + window.scrollY;
            }

            function parentLike(el) {
                if (el.parentElement) return el.parentElement;
                const root = el.getRootNode && el.getRootNode();
                return root && root.host ? root.host : null;
            }

            function textAround(el) {
                const bits = [];
                for (const attr of ['aria-label', 'placeholder', 'name', 'id', 'title', 'type', 'label']) {
                    bits.push(el.getAttribute(attr) || '');
                }
                if (el.labels) {
                    for (const label of el.labels) bits.push(label.innerText || '');
                }
                let parent = parentLike(el);
                for (let i = 0; parent && i < 6; i++, parent = parentLike(parent)) {
                    bits.push((parent.innerText || '').slice(0, 1200));
                    for (const attr of ['aria-label', 'placeholder', 'name', 'id', 'title', 'label']) {
                        bits.push(parent.getAttribute ? parent.getAttribute(attr) || '' : '');
                    }
                }
                return bits.join('\\n');
            }

            function valueOf(el) {
                if (el.value !== undefined) return el.value || '';
                return el.innerText || el.textContent || '';
            }

            function findHeading(rx) {
                return allDeep(document, 'h1,h2,h3,h4,div,span,ytcp-ve')
                    .filter(visible)
                    .map((el) => ({
                        el,
                        text: (el.innerText || el.textContent || '').trim(),
                        rect: el.getBoundingClientRect(),
                    }))
                    .filter((item) => item.text && item.text.length <= 100 && rx.test(item.text))
                    .sort((a, b) => docTop(a.rect) - docTop(b.rect))[0] || null;
            }

            const contactHeading = findHeading(contactHeadingRx);
            const lowerBound = contactHeading ? docTop(contactHeading.rect) - 20 : -Infinity;
            const nextHeading = allDeep(document, 'h1,h2,h3,h4,div,span,ytcp-ve')
                .filter(visible)
                .map((el) => ({
                    text: (el.innerText || el.textContent || '').trim(),
                    rect: el.getBoundingClientRect(),
                }))
                .filter((item) => item.text && item.text.length <= 100 && nextHeadingRx.test(item.text) && docTop(item.rect) > lowerBound + 5)
                .sort((a, b) => docTop(a.rect) - docTop(b.rect))[0] || null;
            const upperBound = nextHeading ? docTop(nextHeading.rect) - 8 : Infinity;

            const fields = allDeep(document, 'input, [role="textbox"], [contenteditable="true"]')
                .filter((el) => visible(el) && !disabled(el))
                .map((el) => ({
                    el,
                    tag: el.tagName.toLowerCase(),
                    type: (el.getAttribute('type') || '').toLowerCase(),
                    text: textAround(el),
                    value: valueOf(el).trim(),
                    rect: el.getBoundingClientRect(),
                }))
                .filter((f) => {
                    const top = docTop(f.rect);
                    if (top < lowerBound || top >= upperBound) return false;
                    if (f.tag === 'textarea') return false;
                    if (f.type === 'url') return false;
                    if (urlRx.test(f.text)) return false;
                    return true;
                });

            let best = null;
            let bestScore = -999;
            for (const f of fields) {
                let score = 0;
                if (f.type === 'email') score += 80;
                if (emailRx.test(f.text)) score += 50;
                if (/@/.test(f.value)) score += 10;
                if (!f.value) score += 5;
                if (f.rect.height > 80) score -= 20;
                if (score > bestScore) {
                    best = f;
                    bestScore = score;
                }
            }

            if (!best || bestScore < 40) return false;
            best.el.setAttribute('data-qaff-marker', marker);
            return true;
        }
        """,
        {"marker": marker},
    )

    if not found:
        return None

    locator = marker_locator(page, marker)
    try:
        expect(locator).to_be_visible(timeout=5000)
        return locator
    except Exception:
        return None


def add_or_update_link(page: Page, title: str, url: str) -> tuple[str, bool]:
    console.print("- Link status: checking...")
    scroll_to_section(page, ["Links", "روابط"], timeout=12000)
    if link_exists(page, url):
        return "already_exists", False

    add_link_button = page.get_by_role(
        "button",
        name=re.compile(r"^(إضافة رابط|Add link)$", re.IGNORECASE),
    ).first

    try:
        expect(add_link_button).to_be_visible(timeout=15000)
        add_link_button.scroll_into_view_if_needed(timeout=5000)
        add_link_button.click(timeout=10000)
    except Exception as exc:
        raise StepFailure("link_failed", f"Add link button was not found/clickable: {exc}") from exc

    title_inputs = page.locator("input.ytcpChannelLinkItemTitleInput")
    url_inputs = page.locator(
        "input.ytcpChannelLinkItemFormInput:not(.ytcpChannelLinkItemTitleInput)"
    )
    title_field = title_inputs.last
    url_field = url_inputs.last

    try:
        expect(title_field).to_be_visible(timeout=15000)
        expect(url_field).to_be_visible(timeout=15000)
        fill_input(title_field, title)
        fill_input(url_field, url)
        expect(title_field).to_have_value(title, timeout=5000)
        expect(url_field).to_have_value(url, timeout=5000)
        page.wait_for_timeout(700)
    except Exception as exc:
        raise StepFailure("link_failed", f"Could not fill link title/URL inputs: {exc}") from exc

    return "added", True


def add_or_update_business_email(page: Page, email: str) -> tuple[str, bool, str | None]:
    console.print("- Email status: checking...")
    field = page.locator("ytcp-form-input-container#business-email input").first

    if not field:
        raise StepFailure("email_failed", "Business email field was not found.")

    try:
        expect(field).to_be_visible(timeout=15000)
    except Exception as exc:
        raise StepFailure("email_failed", f"Business email input was not visible: {exc}") from exc

    current = read_input_value(field)
    if current.casefold() == email.casefold():
        return "already_exists", False, None

    if not locator_is_enabled(field):
        raise StepFailure("email_failed", "Business email field is not editable.")

    try:
        fill_input(field, email)
    except Exception as exc:
        raise StepFailure("email_failed", f"Could not fill business email: {exc}") from exc

    return ("updated" if current else "added"), True, (current or None)


def find_publish_button(page: Page) -> Locator | None:
    exact = make_exact_text_regex(EN_AR["publish"])
    locators = [
        page.get_by_role("button", name=exact),
        page.locator("ytcp-button, button, tp-yt-paper-button").filter(has_text=exact),
    ]
    return first_visible_locator(locators, timeout=2500)


def publish_changes(page: Page) -> bool:
    button = find_publish_button(page)
    if not button:
        return False
    if not locator_is_enabled(button):
        return False

    button.scroll_into_view_if_needed(timeout=5000)
    dismiss_studio_overlays(page)
    button.click(timeout=10000)
    wait_for_soft_network_idle(page)

    try:
        page.wait_for_function(
            """
            () => {
                const rx = /^(Publish|نشر)$/i;
                const buttons = [...document.querySelectorAll('ytcp-button, button, tp-yt-paper-button')]
                    .filter((el) => rx.test((el.innerText || el.textContent || '').trim()));
                if (!buttons.length) return true;
                return buttons.every((el) => {
                    return el.disabled ||
                        el.hasAttribute('disabled') ||
                        el.getAttribute('aria-disabled') === 'true' ||
                        String(el.className || '').includes('disabled');
                });
            }
            """,
            timeout=DEFAULT_TIMEOUT,
        )
    except PlaywrightTimeoutError:
        console.print("- Publish clicked, but the button did not become disabled in time.", "yellow")

    try:
        page.wait_for_function(
            """
            () => {
                const text = document.body ? document.body.innerText || '' : '';
                return /(Changes published|Published|Changes saved|تم النشر|تم نشر|تم حفظ)/i.test(text);
            }
            """,
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(POST_PUBLISH_SETTLE_MS)
    return True


def cancel_pending_changes(page: Page) -> None:
    cancel_names = ["Cancel", "إلغاء"]
    discard_names = ["Discard", "تجاهل", "Discard changes", "تجاهل التغييرات"]

    try:
        button = first_visible_locator(
            [
                page.get_by_role("button", name=make_exact_text_regex(cancel_names)),
                page.locator("ytcp-button, button, tp-yt-paper-button").filter(
                    has_text=make_exact_text_regex(cancel_names)
                ),
            ],
            timeout=2000,
        )
        if button and locator_is_enabled(button):
            button.click(timeout=5000)
            page.wait_for_timeout(700)

            discard = first_visible_locator(
                [
                    page.get_by_role("button", name=make_exact_text_regex(discard_names)),
                    page.locator("ytcp-button, button, tp-yt-paper-button").filter(
                        has_text=make_exact_text_regex(discard_names)
                    ),
                ],
                timeout=2000,
            )
            if discard and locator_is_enabled(discard):
                discard.click(timeout=5000)
                page.wait_for_timeout(700)
    except Exception:
        pass

    try:
        open_customization_tab(page, "details")
        page.reload(wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        detect_and_wait_for_security_challenge(page)
        wait_for_soft_network_idle(page)
    except Exception:
        pass


def open_customization_tab(page: Page, tab: str) -> None:
    channel_id = get_current_studio_channel_id(page)
    names = EN_AR["layout"] if tab == "layout" else EN_AR["basic_info"]

    if tab == "layout" and channel_id:
        # Current Studio exposes this page as "Home tab"; older builds used "layout".
        # Try the current route first and avoid staying on Studio's generic error page.
        for route in ("hometab", "layout"):
            url = f"https://studio.youtube.com/channel/{channel_id}/editing/{route}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
                detect_and_wait_for_security_challenge(page)
                wait_for_soft_network_idle(page)
                body_text = page.locator("body").inner_text(timeout=8000)
                # Detect error pages in any language
                _yt_errors = [
                    "Oops, something went wrong",
                    "Maaf, ada yang tidak beres",   # Indonesian
                    "عذرًا، حدث خطأ",               # Arabic
                    "Lo sentimos, algo salió mal",  # Spanish
                    "Une erreur s'est produite",    # French
                    "Es ist ein Fehler aufgetreten", # German
                    "Si è verificato un errore",    # Italian
                    "Произошла ошибка",             # Russian
                    "Coba lagi",                    # Indonesian retry button
                ]
                _is_error_page = any(e in body_text for e in _yt_errors)
                if not _is_error_page and re.search(
                    r"(Home tab|Layout|Add section|Featured sections|"
                    r"علامة تبويب الصفحة الرئيسية|التنسيق|التخطيط)",
                    body_text,
                    re.IGNORECASE,
                ):
                    return
            except Exception:
                continue

        # Recover from a bad direct Studio route by returning to customization and clicking the tab.
        try:
            page.goto(
                f"https://studio.youtube.com/channel/{channel_id}/editing/details",
                wait_until="domcontentloaded",
                timeout=DEFAULT_TIMEOUT,
            )
            detect_and_wait_for_security_challenge(page)
            wait_for_soft_network_idle(page)
        except Exception:
            pass

    elif channel_id:
        url = f"https://studio.youtube.com/channel/{channel_id}/editing/{tab}"
        page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        detect_and_wait_for_security_challenge(page)
        wait_for_soft_network_idle(page)
        return

    if not click_named_button_or_link(page, names, timeout=16000, scroll=False):
        raise StepFailure("customization_not_found", f"Could not open customization tab: {tab}")

    wait_for_soft_network_idle(page)


def find_home_tab_switch(page: Page) -> Locator | None:
    exact_strong = make_exact_text_regex(
        [
            "Home tab",
            "Show Home tab",
            "علامة تبويب الصفحة الرئيسية",
            "عرض علامة تبويب الصفحة الرئيسية",
            "إظهار علامة تبويب الصفحة الرئيسية",
        ]
    )
    exact_home = make_exact_text_regex(["Home"])

    locators = [
        page.get_by_role("switch", name=exact_strong),
        page.get_by_role("checkbox", name=exact_strong),
        page.get_by_role("switch", name=exact_home),
        page.get_by_role("checkbox", name=exact_home),
    ]
    locator = first_visible_locator(locators, timeout=2500)
    if locator:
        return locator

    marker = f"qaff_switch_{time.time_ns()}"
    found = page.evaluate(
        """
        ({ marker }) => {
            const labelRegexes = [
                /Home tab/i,
                /Show Home tab/i,
                /علامة تبويب الصفحة الرئيسية/i,
                /عرض علامة تبويب الصفحة الرئيسية/i,
                /إظهار علامة تبويب الصفحة الرئيسية/i,
                /^\\s*Home\\s*$/i,
            ];

            function allDeep(root, selector) {
                const found = [];
                const walk = (node) => {
                    if (!node) return;
                    if (node.querySelectorAll) {
                        found.push(...node.querySelectorAll(selector));
                    }
                    const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                    for (const child of children) {
                        if (child.shadowRoot) walk(child.shadowRoot);
                    }
                };
                walk(root);
                return [...new Set(found)];
            }

            const switches = allDeep(
                document,
                '[role="switch"], [role="checkbox"], input[type="checkbox"], ytcp-toggle-button, tp-yt-paper-toggle-button, paper-toggle-button'
            );

            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none';
            }

            function textAround(el) {
                const bits = [
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('label') || '',
                ];
                function parentLike(node) {
                    if (node.parentElement) return node.parentElement;
                    const root = node.getRootNode && node.getRootNode();
                    return root && root.host ? root.host : null;
                }
                let parent = parentLike(el);
                for (let i = 0; parent && i < 7; i++, parent = parentLike(parent)) {
                    bits.push((parent.innerText || '').slice(0, 1200));
                    for (const attr of ['aria-label', 'title', 'label']) {
                        bits.push(parent.getAttribute ? parent.getAttribute(attr) || '' : '');
                    }
                }
                return bits.join('\\n');
            }

            let best = null;
            let bestScore = -999;
            for (const el of switches) {
                if (!visible(el)) continue;
                const text = textAround(el);
                let score = 0;
                if (/Home tab/i.test(text) || /علامة تبويب الصفحة الرئيسية/i.test(text)) score += 30;
                if (/Show Home tab/i.test(text) || /عرض علامة تبويب الصفحة الرئيسية/i.test(text)) score += 12;
                if (labelRegexes.some((rx) => rx.test((el.getAttribute('aria-label') || '').trim()))) {
                    score += 20;
                }
                if (/^\\s*Home\\s*$/i.test((el.getAttribute('aria-label') || '').trim())) {
                    score += 8;
                }
                if (score > bestScore) {
                    best = el;
                    bestScore = score;
                }
            }

            if (!best || bestScore < 20) return false;
            best.setAttribute('data-qaff-marker', marker);
            return true;
        }
        """,
        {"marker": marker},
    )
    if found:
        candidate = marker_locator(page, marker)
        try:
            expect(candidate).to_be_visible(timeout=5000)
            return candidate
        except Exception:
            return None
    return None


def switch_is_checked(switch: Locator) -> bool | None:
    try:
        value = switch.get_attribute("aria-checked", timeout=2000)
        if value == "true":
            return True
        if value == "false":
            return False
    except Exception:
        pass

    try:
        return bool(
            switch.evaluate(
                """
                el => {
                    if (typeof el.checked === 'boolean') return el.checked;
                    const child = el.querySelector('[aria-checked], input[type="checkbox"]');
                    if (child) {
                        if (child.getAttribute('aria-checked') === 'true') return true;
                        if (child.getAttribute('aria-checked') === 'false') return false;
                        if (typeof child.checked === 'boolean') return child.checked;
                    }
                    if (el.hasAttribute('checked')) return true;
                    return null;
                }
                """
            )
        )
    except Exception:
        return None


def enable_home_tab(page: Page) -> tuple[str, bool]:
    console.print("- Home tab status: checking...")
    open_customization_tab(page, "layout")

    # Some Studio versions expose a separate "Home tab" sub-tab; click it if visible.
    click_named_button_or_link(
        page,
        ["Home tab", "علامة تبويب الصفحة الرئيسية"],
        timeout=4000,
        scroll=False,
    )

    try:
        body_text = page.locator("body").inner_text(timeout=5000)
        _yt_errors = [
            "Oops, something went wrong",
            "Maaf, ada yang tidak beres",
            "عذرًا، حدث خطأ",
            "Lo sentimos, algo salió mal",
            "Une erreur s'est produite",
            "Coba lagi",
        ]
        if any(e in body_text for e in _yt_errors):
            raise StepFailure("home_tab_failed", "YouTube Studio opened an error page for Home tab.")
    except StepFailure:
        raise
    except Exception:
        pass

    home_switch = find_home_tab_switch(page)
    if not home_switch:
        raise StepFailure("home_tab_failed", "Home tab switch was not found.")

    checked = switch_is_checked(home_switch)
    if checked is True:
        return "enabled", False

    if not locator_is_enabled(home_switch):
        raise StepFailure("home_tab_failed", "Home tab switch is not editable.")

    home_switch.scroll_into_view_if_needed(timeout=5000)
    home_switch.click(timeout=10000)

    try:
        page.wait_for_function(
            """
            el => {
                if (!el) return false;
                if (el.getAttribute('aria-checked') === 'true') return true;
                if (typeof el.checked === 'boolean') return el.checked === true;
                const child = el.querySelector('[aria-checked], input[type="checkbox"]');
                if (!child) return false;
                return child.getAttribute('aria-checked') === 'true' || child.checked === true;
            }
            """,
            arg=home_switch.element_handle(),
            timeout=10000,
        )
    except Exception:
        pass

    return "enabled_now", True


def verify_basic_info_saved(
    page: Page,
    expect_link: bool,
    expect_email: bool,
) -> tuple[bool, bool, list[str]]:
    console.print("- Verifying saved basic info...")
    page.wait_for_timeout(1000)
    try:
        open_customization_tab(page, "details")
        page.reload(wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        detect_and_wait_for_security_challenge(page)
        wait_for_soft_network_idle(page)
    except Exception:
        pass

    if expect_link:
        scroll_to_section(page, EN_AR["links"], timeout=10000)
    link_ok = True if not expect_link else link_exists(page, LINK_URL)

    if expect_email:
        scroll_to_section(page, EN_AR["contact_info"], timeout=10000)
    email_ok = True if not expect_email else business_email_exists(page, BUSINESS_EMAIL)
    return link_ok, email_ok, collect_visible_validation_errors(page)


def open_customization(page: Page) -> None:
    console.print("- Opening Customization...")
    channel_id = wait_for_studio_channel_id(page, timeout=25000)
    if channel_id:
        page.goto(
            f"https://studio.youtube.com/channel/{channel_id}/editing/profile",
            wait_until="domcontentloaded",
            timeout=DEFAULT_TIMEOUT,
        )
        detect_and_wait_for_security_challenge(page)
        wait_for_soft_network_idle(page)
        if body_contains_any(page, STUDIO_ERROR_PATTERNS, timeout=4000):
            raise StepFailure("customization_not_found", "Studio opened an error page instead of customization.")
        if is_basic_info_editing_url(page.url) and studio_shell_is_visible(page, timeout=8000):
            return
        if body_contains_any(page, EN_AR["basic_info"] + EN_AR["links"] + EN_AR["customization"], timeout=10000):
            return

    if click_named_button_or_link(page, EN_AR["customization"], timeout=12000, scroll=False):
        wait_for_soft_network_idle(page)
        channel_id = wait_for_studio_channel_id(page, timeout=10000)
        if channel_id and not is_basic_info_editing_url(page.url):
            page.goto(
                f"https://studio.youtube.com/channel/{channel_id}/editing/profile",
                wait_until="domcontentloaded",
                timeout=DEFAULT_TIMEOUT,
            )
            detect_and_wait_for_security_challenge(page)
            wait_for_soft_network_idle(page)
        if "/editing" in page.url and studio_shell_is_visible(page, timeout=8000):
            return
        if body_contains_any(page, EN_AR["basic_info"] + EN_AR["links"] + EN_AR["customization"], timeout=10000):
            return

    raise StepFailure("customization_not_found", f"Customization page did not load. Current URL: {page.url}")


def find_new_link_fields(page: Page) -> tuple[Locator | None, Locator | None]:
    title_inputs = page.locator("input.ytcpChannelLinkItemTitleInput")
    url_inputs = page.locator("input.ytcpChannelLinkItemFormInput:not(.ytcpChannelLinkItemTitleInput)")

    try:
        title_field = title_inputs.last
        url_field = url_inputs.last
        expect(title_field).to_be_visible(timeout=5000)
        expect(url_field).to_be_visible(timeout=5000)
        return title_field, url_field
    except Exception:
        pass

    title_field = find_input_by_patterns(
        page,
        [r"Enter title", r"Link title", r"\bTitle\b", r"إدخال عنوان", r"عنوان الرابط"],
        context_patterns=[r"Links", r"روابط", r"الروابط"],
        prefer_empty=True,
        scroll_steps=2,
    )
    url_field = find_input_by_patterns(
        page,
        [r"Enter URL", r"\bURL\b", r"عنوان URL", r"رابط", r"\.net", r"\.com"],
        context_patterns=[r"Links", r"روابط", r"الروابط"],
        prefer_empty=True,
        scroll_steps=2,
    )
    return title_field, url_field


def click_add_link_icon(page: Page) -> bool:
    if click_named_button_or_link(page, EN_AR["add_link"], timeout=5000, scroll=False):
        page.wait_for_timeout(500)
        return True

    marker = f"qaff_add_link_{time.time_ns()}"
    found = page.evaluate(
        """
        ({ marker }) => {
            function allDeep(root, selector) {
                const found = [];
                const walk = (node) => {
                    if (!node) return;
                    if (node.querySelectorAll) found.push(...node.querySelectorAll(selector));
                    const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                    for (const child of children) {
                        if (child.shadowRoot) walk(child.shadowRoot);
                    }
                };
                walk(root);
                return [...new Set(found)];
            }

            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none';
            }

            const buttons = allDeep(
                document,
                'button, ytcp-button, ytcp-icon-button, tp-yt-paper-icon-button, yt-icon-button, [role="button"]'
            ).filter(visible);

            const addRegexes = [
                /Add link/i,
                /إضافة رابط/i,
                /add/i,
                /plus/i,
            ];

            let best = null;
            let bestScore = -999;
            for (const el of buttons) {
                const text = [
                    el.innerText || el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                ].join('\\n');
                let score = 0;
                if (addRegexes.some((rx) => rx.test(text))) score += 50;
                if (el.querySelector('yt-touch-feedback-shape')) score += 10;
                if (el.querySelector('yt-icon, iron-icon')) score += 5;
                if (score > bestScore) {
                    best = el;
                    bestScore = score;
                }
            }

            if (!best || bestScore < 10) return false;
            best.setAttribute('data-qaff-marker', marker);
            best.scrollIntoView({ block: 'center', inline: 'nearest' });
            return true;
        }
        """,
        {"marker": marker},
    )
    if not found:
        return False

    button = marker_locator(page, marker)
    try:
        button.click(timeout=8000)
        page.wait_for_timeout(500)
        return True
    except Exception:
        return False


def find_business_email_field(page: Page) -> Locator | None:
    direct = page.locator("ytcp-form-input-container#business-email input").first
    try:
        expect(direct).to_be_visible(timeout=5000)
        return direct
    except Exception:
        pass

    scroll_to_section(page, EN_AR["contact_info"], timeout=12000)
    return find_input_by_patterns(
        page,
        [r"Business email", r"Email address", r"\bEmail\b", r"البريد الإلكتروني", r"عنوان البريد الإلكتروني"],
        context_patterns=[r"Contact info", r"معلومات الاتصال", r"البريد الإلكتروني"],
        prefer_empty=None,
        scroll_steps=2,
    )


def add_or_update_link(page: Page, title: str, url: str) -> tuple[str, bool]:
    console.print("- Link status: checking...")
    scroll_to_section(page, EN_AR["links"], timeout=12000)
    if link_exists(page, url):
        return "already_exists", False

    if not click_add_link_icon(page):
        raise StepFailure("link_failed", "Add link button was not found/clickable.")

    title_field, url_field = find_new_link_fields(page)
    if not title_field or not url_field:
        raise StepFailure("link_failed", "Link title or URL field was not found after opening Add link.")

    try:
        fill_input(title_field, title)
        fill_input(url_field, url)
        expect(title_field).to_have_value(title, timeout=5000)
        expect(url_field).to_have_value(url, timeout=5000)
        page.wait_for_timeout(500)
    except Exception as exc:
        raise StepFailure("link_failed", f"Could not fill link title/URL inputs: {exc}") from exc

    return "added", True


def add_or_update_business_email(page: Page, email: str) -> tuple[str, bool, str | None]:
    console.print("- Email status: checking...")
    field = find_business_email_field(page)
    if not field:
        raise StepFailure("email_failed", "Business email field was not found.")

    try:
        expect(field).to_be_visible(timeout=10000)
    except Exception as exc:
        raise StepFailure("email_failed", f"Business email input was not visible: {exc}") from exc

    current = read_input_value(field)
    if current.casefold() == email.casefold():
        return "already_exists", False, None

    if not locator_is_enabled(field):
        raise StepFailure("email_failed", "Business email field is not editable.")

    try:
        fill_input(field, email)
    except Exception as exc:
        raise StepFailure("email_failed", f"Could not fill business email: {exc}") from exc

    return ("updated" if current else "added"), True, (current or None)


def find_publish_button(page: Page) -> Locator | None:
    exact = make_exact_text_regex(EN_AR["publish"])
    return first_visible_locator(
        [
            page.get_by_role("button", name=exact),
            page.locator("ytcp-button, button, tp-yt-paper-button").filter(has_text=exact),
        ],
        timeout=2500,
    )


def publish_changes(page: Page) -> bool:
    button = find_publish_button(page)
    if not button or not locator_is_enabled(button):
        return False

    button.scroll_into_view_if_needed(timeout=5000)
    dismiss_studio_overlays(page)
    button.click(timeout=10000)
    wait_for_soft_network_idle(page)

    try:
        page.wait_for_function(
            """
            () => {
                const text = document.body ? document.body.innerText || '' : '';
                return /(Changes published|Published|Changes saved|Saved|تم النشر|تم نشر|تم حفظ|Publié|Guardado|Disimpan)/i.test(text);
            }
            """,
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(POST_PUBLISH_SETTLE_MS)
    return True


def click_add_link_icon(page: Page) -> bool:
    if click_named_button_or_link(page, EN_AR["add_link"], timeout=2500, scroll=False):
        page.wait_for_timeout(500)
        return True

    marker = f"qaff_add_link_{time.time_ns()}"
    found = page.evaluate(
        """
        ({ marker }) => {
            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none';
            }

            function enabled(el) {
                return !el.disabled &&
                    !el.hasAttribute('disabled') &&
                    el.getAttribute('aria-disabled') !== 'true';
            }

            function topOf(el) {
                return el.getBoundingClientRect().top + window.scrollY;
            }

            const links = document.querySelector('ytcp-channel-links');
            const linkTop = links ? topOf(links) : Infinity;
            const buttons = [...document.querySelectorAll(
                'ytcp-channel-editing-profile-tab ytcp-button button, ' +
                'ytcp-channel-editing-profile-tab ytcp-button, ' +
                'ytcp-channel-editing-profile-tab button'
            )].filter((el) => visible(el) && enabled(el));

            let best = null;
            let bestScore = -999;
            for (const el of buttons) {
                const rect = el.getBoundingClientRect();
                const text = [
                    el.innerText || el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                ].join('\\n');
                const top = topOf(el);
                let score = 0;
                if (/Add link|إضافة رابط|add|plus/i.test(text)) score += 80;
                if (el.closest('ytcp-button')) score += 20;
                if (el.querySelector('yt-touch-feedback-shape') || el.closest('ytcp-button')?.querySelector('yt-touch-feedback-shape')) score += 10;
                if (links && top < linkTop) score += Math.max(0, 120 - Math.abs(linkTop - top));
                if (rect.height <= 80) score += 5;
                if (score > bestScore) {
                    best = el;
                    bestScore = score;
                }
            }

            if (!best || bestScore < 20) return false;
            best.setAttribute('data-qaff-marker', marker);
            best.scrollIntoView({ block: 'center', inline: 'nearest' });
            return true;
        }
        """,
        {"marker": marker},
    )
    if not found:
        return False

    button = marker_locator(page, marker)
    try:
        button.click(timeout=8000)
        page.wait_for_timeout(700)
        return True
    except Exception:
        try:
            page.evaluate(
                """
                (marker) => {
                    const el = document.querySelector(`[data-qaff-marker="${marker}"]`);
                    if (!el) return false;
                    el.click();
                    return true;
                }
                """,
                marker,
            )
            page.wait_for_timeout(700)
            return True
        except Exception:
            return False


def find_publish_button(page: Page) -> Locator | None:
    exact = make_exact_text_regex(EN_AR["publish"])
    locator = first_visible_locator(
        [
            page.locator("ytcp-primary-action-bar ytcp-button#publish-button button:not([disabled])"),
            page.locator("ytcp-primary-action-bar ytcp-button#publish-button:not([disabled])"),
            page.locator("ytcp-primary-action-bar ytcp-button button:not([disabled])[aria-disabled='false']"),
            page.locator("ytcp-primary-action-bar ytcp-button:not([disabled])[aria-disabled='false']"),
            page.locator("ytcp-sticky-header ytcp-button button:not([disabled])[aria-disabled='false']"),
            page.get_by_role("button", name=exact),
            page.locator("ytcp-button, button, tp-yt-paper-button").filter(has_text=exact),
        ],
        timeout=1500,
    )
    if locator and locator_is_enabled(locator):
        return locator

    marker = f"qaff_publish_{time.time_ns()}"
    found = page.evaluate(
        """
        ({ marker }) => {
            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none';
            }

            function enabled(el) {
                return !el.disabled &&
                    !el.hasAttribute('disabled') &&
                    el.getAttribute('aria-disabled') !== 'true' &&
                    !String(el.className || '').includes('disabled');
            }

            const bar = document.querySelector('ytcp-primary-action-bar, ytcp-sticky-header');
            if (!bar) return false;
            const buttons = [...bar.querySelectorAll('button, ytcp-button, tp-yt-paper-button')]
                .filter((el) => visible(el) && enabled(el));
            if (!buttons.length) return false;

            const reject = /(Cancel|Discard|إلغاء|تجاهل|لغو|انصراف|لغو کردن|رد کردن)/i;
            const candidates = buttons.filter((el) => !reject.test((el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim()));
            const chosen = (candidates.length ? candidates : buttons)
                .sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left)[0];
            chosen.setAttribute('data-qaff-marker', marker);
            chosen.scrollIntoView({ block: 'center', inline: 'nearest' });
            return true;
        }
        """,
        {"marker": marker},
    )
    if not found:
        return None

    candidate = marker_locator(page, marker)
    try:
        expect(candidate).to_be_visible(timeout=3000)
        return candidate
    except Exception:
        return None


def link_title_input_count(page: Page) -> int:
    try:
        return page.locator("input.ytcpChannelLinkItemTitleInput").count()
    except Exception:
        return 0


def link_url_input_count(page: Page) -> int:
    try:
        return page.locator("ytcp-form-input-container.ytcpChannelLinkItemUrlContainer input").count()
    except Exception:
        return 0


def click_add_link_icon(page: Page) -> bool:
    before_count = link_title_input_count(page)
    button = page.locator("ytcp-button.YtcpChannelLinksAddLinkButton button").first
    try:
        expect(button).to_be_visible(timeout=10000)
        button.scroll_into_view_if_needed(timeout=5000)
        button.click(timeout=10000)
    except Exception as exc:
        raise StepFailure("link_failed", f"Stable Add link button was not clickable: {exc}") from exc

    try:
        page.wait_for_function(
            "(count) => document.querySelectorAll('input.ytcpChannelLinkItemTitleInput').length > count",
            arg=before_count,
            timeout=10000,
        )
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(500)
    return link_title_input_count(page) > before_count


def find_new_link_fields(page: Page) -> tuple[Locator | None, Locator | None]:
    title_inputs = page.locator("input.ytcpChannelLinkItemTitleInput")
    url_inputs = page.locator("ytcp-form-input-container.ytcpChannelLinkItemUrlContainer input")
    try:
        title_field = title_inputs.last
        url_field = url_inputs.last
        expect(title_field).to_be_visible(timeout=10000)
        expect(url_field).to_be_visible(timeout=10000)
        return title_field, url_field
    except Exception:
        return None, None


def add_or_update_link(page: Page, title: str, url: str) -> tuple[str, bool]:
    console.print("- Link status: checking...")
    try:
        page.locator("ytcp-channel-links").scroll_into_view_if_needed(timeout=8000)
    except Exception:
        scroll_to_section(page, EN_AR["links"], timeout=8000)

    if link_exists(page, url):
        return "already_exists", False

    if not click_add_link_icon(page):
        raise StepFailure("link_failed", "Add link button was not found/clickable or no new link row appeared.")

    title_field, url_field = find_new_link_fields(page)
    if not title_field or not url_field:
        raise StepFailure("link_failed", "Link title or URL field was not found after opening Add link.")

    try:
        fill_input(title_field, title)
        fill_input(url_field, url)
        title_value = read_input_value(title_field)
        url_value = read_input_value(url_field)
        if title_value != title or url_value != url:
            raise RuntimeError(f"Field values did not stick. title={title_value!r}, url={url_value!r}")
        page.wait_for_timeout(700)
    except Exception as exc:
        raise StepFailure("link_failed", f"Could not fill link title/URL inputs: {exc}") from exc

    return "added", True


def cancel_pending_changes(page: Page) -> None:
    try:
        button = first_visible_locator(
            [
                page.get_by_role("button", name=make_exact_text_regex(EN_AR["cancel"])),
                page.locator("ytcp-button, button, tp-yt-paper-button").filter(
                    has_text=make_exact_text_regex(EN_AR["cancel"])
                ),
            ],
            timeout=1500,
        )
        if button and locator_is_enabled(button):
            button.click(timeout=5000)
            page.wait_for_timeout(500)

            discard = first_visible_locator(
                [
                    page.get_by_role("button", name=make_exact_text_regex(EN_AR["discard"])),
                    page.locator("ytcp-button, button, tp-yt-paper-button").filter(
                        has_text=make_exact_text_regex(EN_AR["discard"])
                    ),
                ],
                timeout=1500,
            )
            if discard and locator_is_enabled(discard):
                discard.click(timeout=5000)
                page.wait_for_timeout(500)
    except Exception:
        pass


def open_customization_tab(page: Page, tab: str) -> None:
    channel_id = get_current_studio_channel_id(page)
    names = EN_AR["layout"] if tab == "layout" else EN_AR["basic_info"]

    if channel_id:
        route = "profile" if tab == "details" else "hometab"
        page.goto(
            f"https://studio.youtube.com/channel/{channel_id}/editing/{route}",
            wait_until="domcontentloaded",
            timeout=DEFAULT_TIMEOUT,
        )
        detect_and_wait_for_security_challenge(page)
        wait_for_soft_network_idle(page)
        if not body_contains_any(page, STUDIO_ERROR_PATTERNS, timeout=4000):
            return

    if not click_named_button_or_link(page, names, timeout=16000, scroll=False):
        raise StepFailure("customization_not_found", f"Could not open customization tab: {tab}")
    wait_for_soft_network_idle(page)


def find_home_tab_switch(page: Page) -> Locator | None:
    structural = first_visible_locator(
        [
            page.locator("ytcp-home-tab-section ytcp-switch button[role='switch']"),
            page.locator("ytcp-home-tab-section button#home-tab-enabled-switch"),
            page.locator("ytcp-home-tab-section ytcp-switch [role='switch']"),
        ],
        timeout=3000,
    )
    if structural:
        return structural

    exact = make_exact_text_regex(EN_AR["home_tab"])
    locator = first_visible_locator(
        [
            page.get_by_role("switch", name=exact),
            page.get_by_role("checkbox", name=exact),
        ],
        timeout=3000,
    )
    if locator:
        return locator

    marker = f"qaff_home_switch_{time.time_ns()}"
    found = page.evaluate(
        """
        ({ marker }) => {
            function allDeep(root, selector) {
                const found = [];
                const walk = (node) => {
                    if (!node) return;
                    if (node.querySelectorAll) found.push(...node.querySelectorAll(selector));
                    const children = node.querySelectorAll ? node.querySelectorAll('*') : [];
                    for (const child of children) {
                        if (child.shadowRoot) walk(child.shadowRoot);
                    }
                };
                walk(root);
                return [...new Set(found)];
            }

            function visible(el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.visibility !== 'hidden' &&
                    style.display !== 'none';
            }

            function textAround(el) {
                const bits = [
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('label') || '',
                ];
                let parent = el.parentElement;
                for (let i = 0; parent && i < 6; i++, parent = parent.parentElement) {
                    bits.push((parent.innerText || parent.textContent || '').slice(0, 1200));
                }
                return bits.join('\\n');
            }

            const regexes = [
                /Home tab/i,
                /Show Home tab/i,
                /علامة تبويب الصفحة الرئيسية/i,
                /عرض علامة تبويب الصفحة الرئيسية/i,
                /^\\s*Home\\s*$/i,
            ];

            let best = null;
            let bestScore = -999;
            for (const el of allDeep(document, '[role="switch"], [role="checkbox"], input[type="checkbox"]')) {
                if (!visible(el)) continue;
                const text = textAround(el);
                let score = 0;
                if (regexes.some((rx) => rx.test(text))) score += 30;
                if ((el.getAttribute('aria-label') || '').trim() === 'Home') score += 5;
                if (score > bestScore) {
                    best = el;
                    bestScore = score;
                }
            }

            if (!best || bestScore < 20) return false;
            best.setAttribute('data-qaff-marker', marker);
            return true;
        }
        """,
        {"marker": marker},
    )
    if not found:
        return None
    return marker_locator(page, marker)


def enable_home_tab(page: Page) -> tuple[str, bool]:
    console.print("- Home tab status: checking...")
    open_customization_tab(page, "layout")
    click_named_button_or_link(page, EN_AR["home_tab"], timeout=3000, scroll=False)

    if body_contains_any(page, STUDIO_ERROR_PATTERNS, timeout=4000):
        raise StepFailure("home_tab_failed", "YouTube Studio opened an error page for Home tab.")

    home_switch = find_home_tab_switch(page)
    if not home_switch:
        raise StepFailure("home_tab_failed", "Home tab switch was not found.")

    checked = switch_is_checked(home_switch)
    if checked is True:
        return "enabled", False

    if not locator_is_enabled(home_switch):
        raise StepFailure("home_tab_failed", "Home tab switch is not editable.")

    home_switch.scroll_into_view_if_needed(timeout=5000)
    home_switch.click(timeout=10000)
    page.wait_for_timeout(700)
    return "enabled_now", True


def take_error_screenshot(page: Page, handle: str) -> str | None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"{safe_file_stem(handle)}_error.png"
    try:
        page.screenshot(path=str(path), full_page=True, timeout=15000)
        return str(path.relative_to(BASE_DIR)).replace("\\", "/")
    except Exception as exc:
        console.print(f"- Screenshot failed: {exc}", "yellow")
        return None


def default_result(handle: str) -> dict[str, Any]:
    return {
        "handle": handle,
        "channel_id": "",
        "status": "failed",
        "channel_selected": False,
        "exact_match": False,
        "link_status": "not_started",
        "email_status": "not_started",
        "previous_email": None,
        "home_tab_status": "not_started",
        "livestreaming_status": "not_started",
        "livestreaming_url": None,
        "published": False,
        "screenshot": None,
        "error": None,
    }


def record_failure(result: dict[str, Any], page: Page, handle: str, code: str, message: str) -> None:
    result["status"] = "failed"
    if result["error"] is None:
        result["error"] = code
    if result["screenshot"] is None:
        result["screenshot"] = take_error_screenshot(page, handle)
    console.print(f"- Error: {message}", "red")


def process_channel(context: BrowserContext, handle: str, index: int, total: int) -> dict[str, Any]:
    page = context.pages[0] if context.pages else context.new_page()
    result = default_result(handle)
    console.print(f"[{index}/{total}] {handle}", "bold")

    try:
        select_channel_by_handle(page, handle)
        result["channel_selected"] = True
        result["exact_match"] = True
    except StepFailure as exc:
        record_failure(result, page, handle, exc.code, str(exc))
        console.print("Result: FAILED", "red")
        console.print()
        return result
    except Exception as exc:
        record_failure(result, page, handle, "unexpected_error", str(exc))
        console.print("Result: FAILED", "red")
        console.print()
        return result

    try:
        open_youtube_studio(page)
        page.wait_for_timeout(1000)
    except StepFailure as exc:
        record_failure(result, page, handle, exc.code, str(exc))
        console.print("Result: FAILED", "red")
        console.print()
        return result

    if RUN_MODE == "logo":
        try:
            open_customization(page)
            wait_for_manual_logo_upload(page, handle)
            result["status"] = "success"
            console.print("Result: SUCCESS", "green")
        except StepFailure as exc:
            record_failure(result, page, handle, exc.code, str(exc))
            console.print("Result: FAILED", "red")
        console.print()
        return result

    try:
        open_customization(page)
    except StepFailure as exc:
        record_failure(result, page, handle, exc.code, str(exc))
        console.print("Result: FAILED", "red")
        console.print()
        return result
        
    result["channel_id"] = get_current_studio_channel_id(page) or ""

    basic_changed = False

    try:
        link_status, link_changed = add_or_update_link(page, LINK_TITLE, LINK_URL)
        result["link_status"] = link_status
        basic_changed = basic_changed or link_changed
        console.print(f"- Link status: {status_label(link_status)}")
    except StepFailure as exc:
        result["link_status"] = "failed"
        record_failure(result, page, handle, exc.code, str(exc))

    try:
        email_status, email_changed, previous_email = add_or_update_business_email(page, BUSINESS_EMAIL)
        result["email_status"] = email_status
        if previous_email:
            result["previous_email"] = previous_email
        basic_changed = basic_changed or email_changed
        console.print(f"- Email status: {status_label(email_status)}")
    except StepFailure as exc:
        result["email_status"] = "failed"
        record_failure(result, page, handle, exc.code, str(exc))

    if basic_changed:
        published = publish_changes(page)
        result["published"] = result["published"] or published
        if published:
            console.print("- Publish status: published")
        else:
            record_failure(
                result,
                page,
                handle,
                "unexpected_error",
                "Publish button was not enabled after basic-info changes.",
            )
    else:
        console.print("- Publish status: no basic-info changes")

    try:
        home_status, home_changed = enable_home_tab(page)
        result["home_tab_status"] = home_status
        console.print(f"- Home tab status: {status_label(home_status)}")
        if home_changed:
            published = publish_changes(page)
            result["published"] = result["published"] or published
            if published:
                console.print("- Publish status: published")
            else:
                record_failure(
                    result,
                    page,
                    handle,
                    "unexpected_error",
                    "Publish button was not enabled after Home tab change.",
                )
    except StepFailure as exc:
        result["home_tab_status"] = "failed"
        record_failure(result, page, handle, exc.code, str(exc))

    if result["error"] is None:
        try:
            livestreaming_url = open_livestreaming_page(page)
            result["livestreaming_status"] = "opened"
            result["livestreaming_url"] = livestreaming_url
            console.print("- Livestreaming status: opened")
            page.wait_for_timeout(FINAL_STEP_SETTLE_MS)
        except StepFailure as exc:
            result["livestreaming_status"] = "failed"
            record_failure(result, page, handle, exc.code, str(exc))

    if result["error"] is None:
        result["status"] = "success"
        console.print("Result: SUCCESS", "green")
    else:
        console.print("Result: FAILED", "red")

    console.print()
    return result


def save_report(results: list[dict[str, Any]]) -> None:
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = BASE_DIR / f"report_{ts}.csv"
    
    headers = [
        "Channel Handle (القناة المستهدفة)",
        "Channel ID (معرف القناة)",
        "Website Link Added (إضافة الموقع)",
        "Email Added (إضافة الإيميل)",
        "Home Tab Enabled (إضافة Home Tab)",
        "Livestreaming Data Added (بيانات البث المباشر)",
        "Status (الحالة النهائية)",
        "Error (الخطأ)"
    ]
    
    try:
        with open(csv_path, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for r in results:
                link_added = "TRUE" if r.get("link_status") in ("added", "updated", "already_exists") else "FALSE"
                email_added = "TRUE" if r.get("email_status") in ("added", "updated", "already_exists") else "FALSE"
                home_added = "TRUE" if r.get("home_tab_status") in ("enabled", "enabled_now") else "FALSE"
                live_added = "TRUE" if r.get("livestreaming_status") in ("opened", "completed") else "FALSE"
                
                row = [
                    r.get("handle", ""),
                    r.get("channel_id", ""),
                    link_added,
                    email_added,
                    home_added,
                    live_added,
                    r.get("status", ""),
                    r.get("error", "")
                ]
                writer.writerow(row)
    except Exception as exc:
        console.print(f"- Could not save Excel/CSV report: {exc}", "yellow")


def print_summary(results: list[dict[str, Any]]) -> None:
    success = sum(1 for item in results if item["status"] == "success")
    failed = sum(1 for item in results if item["status"] == "failed")
    not_found = sum(1 for item in results if item.get("error") == "channel_not_found")
    failed_handles = [item["handle"] for item in results if item["status"] == "failed"]
    has_screenshots = any(item.get("screenshot") for item in results)

    console.print("Summary:", "bold")
    console.print(f"Successful: {success}")
    console.print(f"Failed: {failed}")
    console.print(f"Channel not found: {not_found}")
    if failed_handles:
        console.print("Failed handles: " + ", ".join(failed_handles))
    console.print("Report saved to:")
    console.print(str(BASE_DIR / "report.csv"))
    if has_screenshots:
        console.print()
        console.print("Screenshots saved to:")
        console.print(str(SCREENSHOTS_DIR))


def main() -> None:
    print_header()
    results: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        context, page, is_cdp = launch_browser(playwright)
        try:
            if PAUSE_FOR_LOGIN:
                wait_for_manual_login(page)
                try:
                    page.goto(
                        "https://www.youtube.com/",
                        wait_until="domcontentloaded",
                        timeout=DEFAULT_TIMEOUT,
                    )
                except PlaywrightTimeoutError:
                    pass

            if AUTO_COLLECT_HANDLES:
                handles = collect_handles_from_channel_switcher(page)
            else:
                handles = collect_handles_from_user()
                confirm_handles(handles)

            if not handles:
                return

            console.print(f"Processing {len(handles)} channels...", "bold")
            console.print()

            for index, handle in enumerate(handles, start=1):
                result = process_channel(context, handle, index, len(handles))
                results.append(result)
                save_report(results)

            print_summary(results)
        finally:
            if not is_cdp:
                context.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\nStopped by user.", "yellow")
