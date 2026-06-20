#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fast YouTube Studio monetization scraper.

Order:
1. Open channel switcher and scrape handles.
2. For every handle: open channel switcher again and select the channel.
3. Open YouTube Studio.
4. Read channel ID from Studio.
5. Open monetization overview directly.
6. If the data is not visible, refresh overview once.
7. Append the numbers to Excel.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

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
except Exception:
    RichConsole = None


CHROME_PROFILE_PATH = r"C:\QaffChromeProfile"
CHANNEL_SWITCHER_URL = "https://www.youtube.com/channel_switcher?"
YOUTUBE_STUDIO_URL = "https://studio.youtube.com/"
HEADLESS = False
PAUSE_FOR_LOGIN = True
DEFAULT_TIMEOUT = 30000
DELETE_LIVE = True
DELETE_SHORTS = True
DELETE_UPLOADS = False

# When running as a PyInstaller EXE, __file__ points to a temp dir.
# Use sys.executable's directory so output files land next to the EXE.
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
REPORT_PATH = BASE_DIR / "delete_report.json"
def get_unique_excel_path() -> Path:
    from datetime import datetime
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"youtube_delete_report_{now_str}"
    ext = ".xlsx"
    return BASE_DIR / f"{base_name}{ext}"


EXCEL_PATH = get_unique_excel_path()

EXCEL_HEADERS = [
    "timestamp",
    "channel_handle",
    "channel_id",
    "live_deleted_status",
    "shorts_deleted_status",
    "uploads_deleted_status",
    "status",
]

SECURITY_CHALLENGE_RE = re.compile(
    r"(captcha|unusual traffic|verify it'?s you|security check|"
    r"تحقق|التحقق|كابتشا|اختبار أمني|نشاط غير معتاد)",
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r"@[A-Za-z0-9._-]+")
CHANNEL_ID_RE = re.compile(r"/channel/([^/?#]+)")
ARABIC_DIGIT_TRANSLATION = str.maketrans(
    {
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
        "٬": ",",
        "٫": ".",
    }
)


class Console:
    def __init__(self) -> None:
        self._rich = RichConsole() if RichConsole else None
        self.callback = None
        self.input_callback = None

    def print(self, message: str = "", style: str | None = None) -> None:
        if self.callback:
            self.callback(message, style)
        elif self._rich:
            self._rich.print(message, style=style, markup=False)
        else:
            print(message)
            
    def input(self, prompt: str) -> str:
        if self.input_callback:
            return self.input_callback(prompt)
        return input(prompt)


console = Console()

# Pause and Resume control
pause_event = threading.Event()
pause_event.set()

# Stop control
stop_event = threading.Event()

# Skip channel control
skip_channel_event = threading.Event()

# Configuration variables (overridden by GUI)
FILTER_BY_SUB_COUNT = True
SUB_COUNT_THRESHOLD = 1000
USE_MANUAL_CHANNELS = False
MANUAL_CHANNELS = []

def check_pause() -> None:
    if stop_event.is_set():
        raise StepFailure("stopped_by_user", "Stopped by user.")
    if skip_channel_event.is_set():
        raise StepFailure("channel_skipped", "Channel skipped by user.")
    if not pause_event.is_set():
        console.print("- Paused. Waiting to resume...", "yellow")
        pause_event.wait()
        console.print("- Resumed.", "green")
    if stop_event.is_set():
        raise StepFailure("stopped_by_user", "Stopped by user.")
    if skip_channel_event.is_set():
        raise StepFailure("channel_skipped", "Channel skipped by user.")


class StepFailure(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class ProfileInUseError(RuntimeError):
    pass


def timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def normalize_handle(handle: str) -> str:
    handle = (handle or "").strip()
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    return handle


def safe_file_stem(handle: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", handle.strip().lstrip("@"))
    return stem.strip("._-") or "channel"


def print_header() -> None:
    console.print("=" * 48, "bold cyan")
    console.print("YouTube Studio Live & Shorts Video Deleter", "bold cyan")
    console.print(f"Chrome Profile: {CHROME_PROFILE_PATH}", "bold cyan")
    console.print(f"Excel File: {EXCEL_PATH}", "bold cyan")
    console.print("=" * 48, "bold cyan")
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
        if path.exists():
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
        return playwright.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
    except PlaywrightError as exc:
        if is_profile_in_use_error(exc):
            raise ProfileInUseError(str(exc)) from exc

        chrome_path = find_chrome_executable()
        if not chrome_path:
            raise RuntimeError("Google Chrome was not found.") from exc

        return playwright.chromium.launch_persistent_context(
            executable_path=chrome_path,
            **launch_kwargs,
        )


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

    for attempt in range(1, 6):
        try:
            context = launch_chrome_context_once(playwright, launch_kwargs)
            context.set_default_timeout(DEFAULT_TIMEOUT)
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT)
            return context, page, False
        except ProfileInUseError:
            console.print("Chrome profile is still in use.", "yellow")
            console.print(f"Close every Chrome window using: {CHROME_PROFILE_PATH}")
            if attempt == 5:
                raise
            console.input("After closing Chrome, press Enter to retry...")

    raise RuntimeError("Could not launch Google Chrome.")


def open_normal_chrome_for_login() -> subprocess.Popen | None:
    chrome_path = find_chrome_executable()
    if not chrome_path:
        console.print("- Google Chrome was not found. Open Chrome manually with this profile:")
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
        if globals().get("stop_event") and stop_event.is_set():
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


def detect_and_wait_for_security_challenge(page: Page) -> None:
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        text = ""

    if SECURITY_CHALLENGE_RE.search(text) or "captcha" in page.url.lower():
        console.print("- Security check detected.", "yellow")
        console.input("Complete it in Chrome, then press Enter...")


def wait_for_dom(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    except PlaywrightTimeoutError:
        pass


def handles_from_text(text: str) -> list[str]:
    handles: list[str] = []
    seen: set[str] = set()
    for match in HANDLE_RE.finditer(text or ""):
        handle = normalize_handle(match.group(0))
        key = handle.casefold()
        if key not in seen:
            seen.add(key)
            handles.append(handle)
    return handles


def handles_from_item(item: Locator) -> list[str]:
    texts: list[str] = []
    try:
        texts.extend(item.locator("yt-formatted-string").all_inner_texts())
    except Exception:
        pass
    if not texts:
        try:
            texts.append(item.inner_text(timeout=1500))
        except Exception:
            pass

    handles: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for handle in handles_from_text(text):
            key = handle.casefold()
            if key not in seen:
                seen.add(key)
                handles.append(handle)
    return handles


def parse_subscriber_count(text: str) -> int:
    text = text.translate(ARABIC_DIGIT_TRANSLATION).lower().strip()
    
    # Remove commas, spaces
    text = text.replace(",", "")
    
    # Check for 0/no subscribers
    if not text or any(x in text for x in ["no", "لا يوجد", "لا مشترك"]):
        return 0
        
    # Match numeric parts and suffixes
    # e.g., "1.54k", "1.54 ألف", "1.5m", "1.5 مليون"
    match = re.search(r"([\d.]+)\s*(k|m|ألف|مليون|b|مليار|thousand|million)?", text)
    if not match:
        return 0
        
    val_str = match.group(1)
    suffix = match.group(2)
    
    try:
        value = float(val_str)
    except ValueError:
        return 0
        
    if suffix:
        if suffix in ('k', 'ألف', 'thousand'):
            value *= 1000
        elif suffix in ('m', 'مليون', 'million'):
            value *= 1000000
        elif suffix in ('b', 'مليار', 'million'):
            value *= 1000000000
            
    return int(value)


def get_channel_info_from_item(item: Locator) -> tuple[str | None, int]:
    """
    Returns (handle, subscriber_count) from a ytd-account-item-renderer Locator.
    """
    try:
        texts = item.locator("yt-formatted-string").all_inner_texts()
    except Exception:
        try:
            # Fallback to splitting inner text
            raw_text = item.inner_text(timeout=1500)
            texts = [t.strip() for t in raw_text.split("\n") if t.strip()]
        except Exception:
            texts = []
            
    handle = None
    sub_count = 0
    
    for text in texts:
        text_clean = text.strip()
        # Find handle
        if not handle:
            match = HANDLE_RE.search(text_clean)
            if match:
                handle = normalize_handle(match.group(0))
        
        # Find subscriber count
        text_trans = text_clean.translate(ARABIC_DIGIT_TRANSLATION).lower()
        if any(kw in text_trans for kw in ["sub", "مشترك", "subscriber", "subscribers"]):
            sub_count = parse_subscriber_count(text_clean)
            
    return handle, sub_count



def wait_for_more_rows(page: Page, previous_count: int, previous_height: int) -> None:
    try:
        page.wait_for_function(
            """
            ({ previousCount, previousHeight }) => {
                const count = document.querySelectorAll('ytd-account-item-renderer').length;
                const height = document.documentElement.scrollHeight || document.body.scrollHeight || 0;
                return count > previousCount || height > previousHeight;
            }
            """,
            arg={"previousCount": previous_count, "previousHeight": previous_height},
            timeout=3000,
        )
    except PlaywrightTimeoutError:
        pass
    
    # Give the page 3 seconds to fully load data as requested
    page.wait_for_timeout(3000)


def open_channel_switcher(page: Page) -> Locator:
    console.print("- Opening channel switcher...")
    page.goto(CHANNEL_SWITCHER_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
    
    # Give the page 3 seconds to fully load its initial data
    page.wait_for_timeout(3000)
    
    detect_and_wait_for_security_challenge(page)

    items = page.locator("ytd-account-item-renderer")
    try:
        expect(items.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    except PlaywrightTimeoutError as exc:
        raise StepFailure("channel_switcher_empty", "No channel rows were visible.") from exc
    return items


def scrape_channel_handles(page: Page) -> list[str]:
    if USE_MANUAL_CHANNELS:
        console.print("Step 2: Using manual channel list provided by user", "bold")
        for handle in MANUAL_CHANNELS:
            console.print(f"  manual: {handle}")
        console.print(f"- Total manual channels: {len(MANUAL_CHANNELS)}")
        console.print()
        return MANUAL_CHANNELS

    console.print("Step 2: Scrape channel handles", "bold")
    items = open_channel_switcher(page)
    handles: list[str] = []
    seen: set[str] = set()

    console.print("- Auto-scrolling to wait for page to load completely before scraping...")
    stable_rounds = 0
    for _ in range(80):
        count = items.count()
        height = page.evaluate("document.documentElement.scrollHeight || document.body.scrollHeight || 0")
        
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight, 900))")
        wait_for_more_rows(page, count, int(height))
        
        new_count = items.count()
        new_height = page.evaluate("document.documentElement.scrollHeight || document.body.scrollHeight || 0")
        
        if new_count == count and new_height == height:
            stable_rounds += 1
        else:
            stable_rounds = 0
            
        if stable_rounds >= 3:
            break

    final_count = items.count()
    console.print(f"- Finished scrolling. Found {final_count} channel items.")
    for index in range(final_count):
        handle, sub_count = get_channel_info_from_item(items.nth(index))
        if handle:
            key = handle.casefold()
            if key not in seen:
                seen.add(key)
                if FILTER_BY_SUB_COUNT:
                    if sub_count >= SUB_COUNT_THRESHOLD:
                        handles.append(handle)
                        console.print(f"  found: {handle} ({sub_count} subscribers) - Kept")
                    else:
                        console.print(f"  found: {handle} ({sub_count} subscribers) - Filtered out (less than {SUB_COUNT_THRESHOLD})")
                else:
                    handles.append(handle)
                    console.print(f"  found: {handle} ({sub_count} subscribers)")

    console.print(f"- Processed and kept {len(handles)} handles.")
    console.print()
    return handles


def select_channel_by_handle(page: Page, handle: str) -> None:
    open_channel_switcher(page)
    wanted = normalize_handle(handle)
    stable_rounds = 0

    console.print(f"- Searching for channel: {wanted}")

    for attempt in range(80):
        # Precise XPath to find the row containing our handle and then target the clickable icon item inside it
        # This ensures we don't accidentally click the first account in the list.
        xpath = (
            f"//ytd-account-item-renderer"
            f"[.//yt-formatted-string[normalize-space(.)='{wanted}']]"
            f"//tp-yt-paper-icon-item[@role='option']"
        )
        
        target = page.locator(xpath).first
        
        if target.is_visible():
            console.print(f"- Found target row for {wanted}. Clicking...")
            target.scroll_into_view_if_needed(timeout=DEFAULT_TIMEOUT)
            page.wait_for_timeout(500) # Small pause to ensure UI stability
            
            # Native click on the specific clickable area
            target.click(timeout=DEFAULT_TIMEOUT)
            
            # Wait for the switcher to close and page to navigate/reload
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            
            console.print(f"Result: SUCCESS - Switched to {wanted}")
            return

        # If not found, scroll down to load more rows
        current_height = page.evaluate("document.documentElement.scrollHeight || document.body.scrollHeight || 0")
        page.evaluate("window.scrollBy(0, 800)")
        
        # Wait a bit for potential lazy loading (increased to 3000ms as requested)
        page.wait_for_timeout(3000)
        
        new_height = page.evaluate("document.documentElement.scrollHeight || document.body.scrollHeight || 0")
        if new_height == current_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
            
        if stable_rounds >= 5:
            break

    raise StepFailure("channel_not_found", f"Channel handle '{wanted}' was not found in the list after scrolling.")


def extract_channel_id(value: str | None) -> str | None:
    match = CHANNEL_ID_RE.search(value or "")
    return match.group(1) if match else None


def get_current_studio_channel_id(page: Page) -> str | None:
    channel_id = extract_channel_id(page.url)
    if channel_id:
        return channel_id

    try:
        hrefs = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('[href*="/channel/"]'))
                .map((element) => element.getAttribute('href') || '')
                .filter(Boolean)
            """
        )
    except Exception:
        hrefs = []

    for href in hrefs:
        channel_id = extract_channel_id(href)
        if channel_id:
            return channel_id
    return None


def dismiss_studio_welcome_if_present(page: Page) -> None:
    """Check for and dismiss the YouTube Studio Welcome modal if it appears."""
    try:
        xpath = (
            "//ytcp-button[@id='dismiss-button']//button"
            " | //button[@aria-label='متابعة' or @aria-label='Continue']"
        )
        btn = page.locator(f"xpath={xpath}").first
        btn.wait_for(state="attached", timeout=4000)
        console.print("- YouTube Studio welcome modal detected. Dismissing...")
        
        # Use JS click to bypass any overlay interceptions (tp-yt-iron-overlay-backdrop)
        btn.evaluate("node => node.click()")
        page.wait_for_timeout(1000)
    except Exception:
        # No modal found or failed to click, continue normally
        pass


def open_studio_and_get_channel_id(context: BrowserContext, page: Page) -> tuple[str, Page]:
    console.print("- Navigating to YouTube Studio via account menu...")
    
    page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    
    try:
        console.print("- Clicking avatar button...")
        avatar_btn = page.locator("button#avatar-btn").first
        avatar_btn.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        page.wait_for_timeout(1000)
        avatar_btn.click(timeout=DEFAULT_TIMEOUT)
        
        console.print("- Clicking YouTube Studio link...")
        studio_link = page.locator('a#endpoint[href*="studio.youtube.com"]').first
        studio_link.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        page.wait_for_timeout(500)
        
        # Remove target="_blank" to force opening in the same tab
        studio_link.evaluate("node => node.removeAttribute('target')")
        
        # Safely handle if it still opens in a new tab
        try:
            with context.expect_page(timeout=3000) as new_page_info:
                studio_link.click(timeout=DEFAULT_TIMEOUT)
            new_page = new_page_info.value
            console.print("- Studio opened in a new tab. Switching to it...")
            page.close()
            page = new_page
        except Exception:
            # Didn't open a new tab, continue on current page
            pass
            
    except Exception as exc:
        raise StepFailure("studio_navigation_failed", f"Failed to navigate to Studio via menu: {exc}")

    # Call the welcome modal dismisser immediately after loading Studio
    dismiss_studio_welcome_if_present(page)

    # Try to get the channel ID from the URL or page content over a few seconds
    # as Studio often redirects multiple times.
    channel_id = None
    for attempt in range(15):
        channel_id = get_current_studio_channel_id(page)
        if channel_id:
            break
        page.wait_for_timeout(1000)

    if not channel_id:
        # One last attempt to wait for URL
        try:
            page.wait_for_url(re.compile(r".*/channel/[^/?#]+.*"), timeout=5000)
            channel_id = get_current_studio_channel_id(page)
        except Exception:
            pass

    if not channel_id:
        raise StepFailure("channel_id_missing", "Could not read channel ID from Studio after waiting.")

    console.print(f"- Channel ID: {channel_id}")
    return channel_id, page


def delete_all_videos_on_page(page: Page, type_name: str) -> str:
    """
    Deletes all videos in the current page layout for the channel (Live or Shorts).
    Returns 'deleted', 'no_videos', or 'failed'.
    """
    console.print(f"- Starting deletion loop for {type_name}...")
    
    max_batches = 25  # Safety limit to prevent infinite loops
    deleted_any = False

    for batch in range(1, max_batches + 1):
        check_pause()
        
        # Wait for the table to load or stabilize
        page.wait_for_timeout(3500)
        
        # Check if selection checkbox is present
        selection_checkbox = page.locator("ytcp-table-header ytcp-checkbox-lit#selection-checkbox").first
        try:
            # Short wait to check if checkbox appears
            selection_checkbox.wait_for(state="visible", timeout=6000)
        except Exception:
            # If selection checkbox doesn't appear, check if there's an empty state
            content = page.locator("body").inner_text()
            if any(term in content for term in ["لا تتوفر", "No videos", "No matching videos", "ما من فيديوهات", "ما مِن فيديوهات"]):
                console.print(f"- No {type_name} videos found.")
                return "no_videos" if not deleted_any else "deleted"
            else:
                console.print(f"- No selection checkbox found for {type_name} (possibly empty).")
                return "no_videos" if not deleted_any else "deleted"

        console.print(f"- Batch {batch}: Selecting videos...")
        # 1. Click ytcp-table-header ytcp-checkbox-lit#selection-checkbox
        selection_checkbox.click(timeout=5000)
        page.wait_for_timeout(1500)

        # 3. Click the dropdown trigger: ytcp-dropdown-trigger div.borderless.container -> closest('ytcp-dropdown-trigger')
        console.print("  - Locating and clicking More actions dropdown trigger...")
        try:
            action_result = page.evaluate("""
                () => {
                    const logs = [];
                    logs.push("Searching for More Actions dropdown...");
                    
                    const querySelectorAllDeep = (selector, root = document) => {
                        const results = [];
                        const queue = [root];
                        while (queue.length > 0) {
                            const node = queue.shift();
                            if (node.querySelectorAll) {
                                try {
                                    const matches = node.querySelectorAll(selector);
                                    for (const m of matches) {
                                        if (!results.includes(m)) {
                                            results.push(m);
                                        }
                                    }
                                } catch (e) {}
                            }
                            if (node.shadowRoot) {
                                queue.push(node.shadowRoot);
                            }
                            if (node.querySelectorAll) {
                                try {
                                    const allEl = node.querySelectorAll('*');
                                    for (const el of allEl) {
                                        if (el.shadowRoot && !queue.includes(el.shadowRoot)) {
                                            queue.push(el.shadowRoot);
                                        }
                                    }
                                } catch (e) {}
                            }
                        }
                        return results;
                    };

                    const isElementVisible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0 && 
                               rect.height > 0 && 
                               style.display !== 'none' && 
                               style.visibility !== 'hidden';
                    };

                    const containers = querySelectorAllDeep('div.borderless.container, div.borderless');
                    logs.push("Found " + containers.length + " borderless container candidates.");

                    const triggers = [];
                    for (const container of containers) {
                        const trigger = container.closest('ytcp-dropdown-trigger') || container.parentElement;
                        if (trigger && isElementVisible(trigger) && !triggers.includes(trigger)) {
                            triggers.push({ trigger: trigger, container: container });
                        }
                    }
                    logs.push("Found " + triggers.length + " visible bulk action triggers.");

                    if (triggers.length === 0) {
                        return { success: false, log: "No visible bulk action triggers found.", logs: logs };
                    }

                    const keywords = ['actions', 'إجراءات', 'more', 'مزيد', 'أخرى', 'إضافية', 'other'];
                    let targetObj = null;

                    for (const obj of triggers) {
                        const text = (obj.trigger.innerText || obj.trigger.textContent || obj.container.innerText || obj.container.textContent || '').toLowerCase();
                        logs.push("Bulk action trigger text: \\"" + text.trim() + "\\"");
                        const hasKeyword = keywords.some(kw => text.includes(kw));
                        if (hasKeyword) {
                            targetObj = obj;
                            logs.push("Selected bulk action trigger by keyword.");
                            break;
                        }
                    }

                    if (!targetObj) {
                        targetObj = triggers[triggers.length - 1];
                        logs.push("Selected last visible bulk action trigger as fallback.");
                    }

                    if (targetObj) {
                        targetObj.trigger.click();
                        targetObj.container.click();
                        logs.push("Clicked trigger and container.");
                        return { success: true, logs: logs };
                    }

                    return { success: false, log: "Failed to select trigger.", logs: logs };
                }
            """)
            if not action_result or not action_result.get("success"):
                console.print("  - JavaScript dropdown click failed. Trying standard click fallback...")
                if action_result and "logs" in action_result:
                    for log_line in action_result["logs"]:
                        console.print(f"      [JS] {log_line}")
                dropdown_trigger = page.locator("ytcp-dropdown-trigger:has(div.borderless.container)").last
                dropdown_trigger.click()
            page.wait_for_timeout(1500)
        except Exception as e:
            console.print(f"  - Error clicking dropdown: {e}. Trying standard click fallback...")
            dropdown_trigger = page.locator("ytcp-dropdown-trigger:has(div.borderless.container)").last
            if dropdown_trigger.is_visible():
                dropdown_trigger.click()
                page.wait_for_timeout(1500)

        # 4. Find the "Delete forever" option inside the opened menu using JS evaluation
        console.print("  - Looking for Delete option...")
        try:
            # We evaluate a script to locate and click the delete menu item
            click_success = page.evaluate("""
                () => {
                    const isVisible = (el) => {
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== 'none';
                    };

                    let candidates = Array.from(document.querySelectorAll(
                        'ytcp-menu-service-item, paper-item, tp-yt-paper-item, tp-yt-paper-item-body, ytcp-ve, .ytcp-text-menu, yt-formatted-string'
                    ));

                    const deleteKeywords = ['delete', 'forever', 'permanently', 'حذف', 'نهائي', 'remove', 'supprimer', 'excluir', 'eliminar', 'löschen'];
                    
                    const matching = candidates.filter(el => {
                        if (!isVisible(el)) return false;
                        const text = (el.innerText || el.textContent || '').toLowerCase();
                        const hasDelete = deleteKeywords.some(kw => text.includes(kw));
                        const hasCancel = ['cancel', 'download', 'تنزيل', 'إلغاء'].some(kw => text.includes(kw));
                        return hasDelete && !hasCancel;
                    });

                    if (matching.length > 0) {
                        matching[0].click();
                        return true;
                    }

                    for (const kw of deleteKeywords) {
                        const xpath = `//*[not(self::script) and not(self::style) and contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '${kw}')]`;
                        const iterator = document.evaluate(xpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                        for (let i = 0; i < iterator.snapshotLength; i++) {
                            const el = iterator.snapshotItem(i);
                            if (isVisible(el)) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    
                    return false;
                }
            """)
            if not click_success:
                console.print("  - JavaScript menu click failed. Trying standard click fallback...")
                # Fallback to standard selector click
                fallback_delete = page.locator("ytcp-menu-service-item:has-text('Delete forever'), ytcp-menu-service-item:has-text('الحذف نهائيًا'), text=Delete forever, text=الحذف نهائيًا, text=Delete permanently").first
                fallback_delete.click()
            page.wait_for_timeout(1500)
        except Exception as e:
            console.print(f"  - Error clicking menu option: {e}. Trying standard click fallback...")
            fallback_delete = page.locator("ytcp-menu-service-item:has-text('Delete forever'), ytcp-menu-service-item:has-text('الحذف نهائيًا'), text=Delete forever, text=الحذف نهائيًا, text=Delete permanently").first
            if fallback_delete.is_visible():
                fallback_delete.click()
            page.wait_for_timeout(1500)

        # 6. Wait for the confirmation checkbox and delete button to be ready on the page
        console.print("  - Waiting for confirmation checkbox and delete button...")
        action_result = None
        for attempt in range(24): # 12 seconds timeout (24 * 500ms)
            check_pause()
            try:
                action_result = page.evaluate("""
                    () => {
                        const logs = [];
                        logs.push("Starting global shadow DOM traversal...");

                        const querySelectorAllDeep = (selector, root = document) => {
                            const results = [];
                            const queue = [root];
                            while (queue.length > 0) {
                                const node = queue.shift();
                                if (node.querySelectorAll) {
                                    try {
                                        const matches = node.querySelectorAll(selector);
                                        for (const m of matches) {
                                            if (!results.includes(m)) {
                                                results.push(m);
                                            }
                                        }
                                    } catch (e) {}
                                }
                                if (node.shadowRoot) {
                                    queue.push(node.shadowRoot);
                                }
                                if (node.querySelectorAll) {
                                    try {
                                        const allEl = node.querySelectorAll('*');
                                        for (const el of allEl) {
                                            if (el.shadowRoot && !queue.includes(el.shadowRoot)) {
                                                queue.push(el.shadowRoot);
                                            }
                                        }
                                    } catch (e) {}
                                }
                            }
                            return results;
                        };

                        const isElementVisible = (el) => {
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return rect.width > 0 && 
                                   rect.height > 0 && 
                                   style.display !== 'none' && 
                                   style.visibility !== 'hidden' && 
                                   style.opacity !== '0';
                        };

                        // 1. Try to find the active confirmation dialog
                        const dialogs = querySelectorAllDeep('ytcp-confirmation-dialog, ytcp-dialog, paper-dialog');
                        logs.push("Found " + dialogs.length + " dialog candidates.");
                        
                        let activeDialog = null;
                        for (const d of dialogs) {
                            if (isElementVisible(d)) {
                                activeDialog = d;
                                logs.push("Found visible dialog: " + d.tagName);
                                break;
                            }
                        }
                        
                        if (!activeDialog) {
                            // Try to find a dialog that contains the confirm-checkbox
                            for (const d of dialogs) {
                                const cbs = querySelectorAllDeep('#confirm-checkbox', d);
                                if (cbs.length > 0 && isElementVisible(cbs[0])) {
                                    activeDialog = d;
                                    logs.push("Found dialog containing visible confirm-checkbox: " + d.tagName);
                                    break;
                                }
                            }
                        }

                        // 2. Locate the confirmation checkbox
                        let targetCheckbox = null;
                        if (activeDialog) {
                            const cbs = querySelectorAllDeep('#confirm-checkbox, ytcp-checkbox-lit, input[type="checkbox"]', activeDialog);
                            for (const cb of cbs) {
                                if (isElementVisible(cb)) {
                                    targetCheckbox = cb;
                                    logs.push("Found checkbox inside active dialog.");
                                    break;
                                }
                            }
                        } else {
                            // Fallback: search globally ONLY for specific ID #confirm-checkbox
                            const cbs = querySelectorAllDeep('#confirm-checkbox');
                            for (const cb of cbs) {
                                if (isElementVisible(cb)) {
                                    targetCheckbox = cb;
                                    logs.push("Found checkbox globally by ID #confirm-checkbox.");
                                    break;
                                }
                            }
                        }

                        if (!targetCheckbox) {
                            return { status: "checkbox_not_found", logs: logs };
                        }

                        // 3. Tick the checkbox
                        logs.push("Ticking the checkbox...");
                        let nativeInput = null;
                        if (targetCheckbox.tagName.toLowerCase() === 'input' && targetCheckbox.type === 'checkbox') {
                            nativeInput = targetCheckbox;
                        } else {
                            const inputs = querySelectorAllDeep('input[type="checkbox"]', targetCheckbox);
                            if (inputs.length > 0) {
                                nativeInput = inputs[0];
                            }
                        }

                        if (nativeInput) {
                            logs.push("Native input checked status: " + nativeInput.checked);
                            if (!nativeInput.checked) {
                                targetCheckbox.click();
                                logs.push("Clicked checkbox host.");
                                
                                if (!nativeInput.checked) {
                                    const container = querySelectorAllDeep('#checkbox-container', targetCheckbox)[0];
                                    if (container) {
                                        container.click();
                                        logs.push("Clicked #checkbox-container.");
                                    }
                                }
                                
                                if (!nativeInput.checked) {
                                    logs.push("Trying to click native input directly.");
                                    nativeInput.click();
                                }
                                if (!nativeInput.checked) {
                                    logs.push("Setting checked property and dispatching change event.");
                                    nativeInput.checked = true;
                                    nativeInput.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                            } else {
                                logs.push("Checkbox was already checked.");
                            }
                        } else {
                            targetCheckbox.click();
                            logs.push("Clicked checkbox element (no native input found).");
                        }

                        // 4. Locate the confirm/delete button
                        let targetButton = null;
                        const deleteKeywords = ['delete', 'forever', 'حذف', 'نهائي', 'confirm', 'تأكيد', 'oui', 'yes'];
                        const cancelKeywords = ['cancel', 'إلغاء', 'no', 'non', 'dismiss'];

                        if (activeDialog) {
                            const buttons = querySelectorAllDeep('#confirm-button, button, ytcp-button', activeDialog);
                            for (const btn of buttons) {
                                if (!isElementVisible(btn)) continue;
                                
                                if (btn.id === 'confirm-button' || btn.getAttribute('id') === 'confirm-button') {
                                    targetButton = btn;
                                    logs.push("Found confirm button by ID inside dialog.");
                                    break;
                                }
                                
                                let btnText = (btn.innerText || btn.textContent || btn.getAttribute('aria-label') || '').toLowerCase();
                                if (!btnText && btn.shadowRoot) {
                                    btnText = (btn.shadowRoot.textContent || '').toLowerCase();
                                }
                                const hasDelete = deleteKeywords.some(kw => btnText.includes(kw));
                                const hasCancel = cancelKeywords.some(kw => btnText.includes(kw));
                                if (hasDelete && !hasCancel) {
                                    targetButton = btn;
                                    logs.push("Found confirm button by text inside dialog: \\"" + btnText.trim() + "\\"");
                                    break;
                                }
                            }
                        } else {
                            const buttons = querySelectorAllDeep('#confirm-button');
                            for (const btn of buttons) {
                                if (isElementVisible(btn)) {
                                    targetButton = btn;
                                    logs.push("Found confirm button globally by ID #confirm-button.");
                                    break;
                                }
                            }
                        }

                        if (!targetButton) {
                            return { status: "button_not_found", logs: logs };
                        }

                        logs.push("Clicking target button...");
                        targetButton.click();
                        const innerButtons = querySelectorAllDeep('button', targetButton);
                        if (innerButtons.length > 0) {
                            logs.push("Clicking inner native button inside target button.");
                            innerButtons[0].click();
                        }

                        return { status: "success", logs: logs };
                    }
                """)
                if action_result and action_result.get("status") == "success":
                    break
            except Exception as e:
                console.print(f"    [JS Attempt {attempt+1} Error] {e}", "yellow")
            page.wait_for_timeout(500)

        if not action_result or action_result.get("status") != "success":
            console.print("  - Confirmation dialog action failed.", "red")
            if action_result and "logs" in action_result:
                console.print("    JS Logs:")
                for log_line in action_result["logs"]:
                    console.print(f"      * {log_line}")
            raise StepFailure("confirmation_failed", f"Failed to confirm deletion. JS Status: {action_result.get('status') if action_result else 'None'}")

        console.print("  - Deletion confirmed. Waiting 1.5 minutes (90 seconds) for deletion to process...")
        deleted_any = True
        
        # Wait for 1.5 minutes (90 seconds) as requested by the user
        page.wait_for_timeout(90000)
        
        # Reload the page to refresh status and see if more videos exist
        page.reload(wait_until="domcontentloaded")

    return "deleted"


def take_error_screenshot(page: Page, handle: str) -> str | None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"{safe_file_stem(handle)}_error.png"
    try:
        page.screenshot(path=str(path), full_page=True, timeout=DEFAULT_TIMEOUT)
        return str(path.relative_to(BASE_DIR)).replace("\\", "/")
    except Exception:
        return None


def default_result(handle: str) -> dict[str, Any]:
    return {
        "timestamp": timestamp(),
        "channel_handle": handle,
        "channel_id": "",
        "status": "failed",
        "live_deleted_status": "not_started",
        "shorts_deleted_status": "not_started",
        "uploads_deleted_status": "not_started",
        "screenshot": None,
        "error": None,
    }


def process_channel(context: BrowserContext, handle: str, index: int, total: int) -> dict[str, Any]:
    check_pause()
    skip_channel_event.clear()
    
    # Clean up any extra tabs that might have opened
    while len(context.pages) > 1:
        context.pages[-1].close()
        
    page = context.pages[0] if context.pages else context.new_page()
    result = default_result(handle)
    console.print(f"[{index}/{total}] {handle}", "bold")

    try:
        select_channel_by_handle(page, handle)
        channel_id, page = open_studio_and_get_channel_id(context, page)
        result["channel_id"] = channel_id

        # 1. Live Videos Deletion
        if DELETE_LIVE:
            live_url = f"https://studio.youtube.com/channel/{channel_id}/videos/live"
            console.print(f"- Navigating to Live videos page: {live_url}")
            page.goto(live_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
            page.wait_for_timeout(2000)
            dismiss_studio_welcome_if_present(page)
            
            live_status = delete_all_videos_on_page(page, "Live")
            result["live_deleted_status"] = live_status
            console.print(f"- Live videos deletion status: {live_status}")
        else:
            result["live_deleted_status"] = "skipped"
            console.print("- Live videos deletion skipped by user choice.")

        # 2. Shorts Videos Deletion
        if DELETE_SHORTS:
            shorts_url = f"https://studio.youtube.com/channel/{channel_id}/videos/short"
            console.print(f"- Navigating to Shorts videos page: {shorts_url}")
            page.goto(shorts_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
            page.wait_for_timeout(2000)
            dismiss_studio_welcome_if_present(page)

            shorts_status = delete_all_videos_on_page(page, "Shorts")
            result["shorts_deleted_status"] = shorts_status
            console.print(f"- Shorts videos deletion status: {shorts_status}")
        else:
            result["shorts_deleted_status"] = "skipped"
            console.print("- Shorts videos deletion skipped by user choice.")

        # 3. Uploads Videos Deletion
        if DELETE_UPLOADS:
            uploads_url = f"https://studio.youtube.com/channel/{channel_id}/videos"
            console.print(f"- Navigating to Uploads videos page: {uploads_url}")
            page.goto(uploads_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
            page.wait_for_timeout(2000)
            dismiss_studio_welcome_if_present(page)

            uploads_status = delete_all_videos_on_page(page, "Uploads")
            result["uploads_deleted_status"] = uploads_status
            console.print(f"- Uploads videos deletion status: {uploads_status}")
        else:
            result["uploads_deleted_status"] = "skipped"
            console.print("- Uploads videos deletion skipped by user choice.")

        result["status"] = "success"
        console.print("Result: SUCCESS", "green")
    except StepFailure as exc:
        result["error"] = exc.code
        if exc.code == "channel_skipped":
            result["status"] = "skipped"
            result["live_deleted_status"] = "skipped"
            result["shorts_deleted_status"] = "skipped"
            result["uploads_deleted_status"] = "skipped"
            console.print(f"- Skip: {exc}", "yellow")
            console.print("Result: SKIPPED", "yellow")
        else:
            result["screenshot"] = take_error_screenshot(page, handle)
            console.print(f"- Error: {exc}", "red")
            console.print("Result: FAILED", "red")
    except Exception as exc:
        result["error"] = "unexpected_error"
        result["screenshot"] = take_error_screenshot(page, handle)
        console.print(f"- Error: {exc}", "red")
        console.print("Result: FAILED", "red")

    append_result_to_excel(result)
    console.print()
    return result


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def column_index(cell_reference: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_reference.upper())
    total = 0
    for char in letters:
        total = total * 26 + (ord(char) - 64)
    return total


def read_shared_strings(zip_file: zipfile.ZipFile) -> list[str]:
    try:
        data = zip_file.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ET.fromstring(data)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    return ["".join(node.text or "" for node in item.findall(f".//{ns}t")) for item in root.findall(f"{ns}si")]


def read_existing_xlsx_rows(path: Path) -> list[list[Any]]:
    if not path.exists():
        return []

    try:
        with zipfile.ZipFile(path, "r") as zip_file:
            shared_strings = read_shared_strings(zip_file)
            root = ET.fromstring(zip_file.read("xl/worksheets/sheet1.xml"))
    except Exception:
        console.print("- Existing Excel file could not be read; creating a fresh one.", "yellow")
        return []

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows: list[list[Any]] = []
    for row_node in root.findall(f".//{ns}row"):
        values_by_column: dict[int, Any] = {}
        for cell in row_node.findall(f"{ns}c"):
            ref = cell.attrib.get("r", "A1")
            cell_type = cell.attrib.get("t")
            value_node = cell.find(f"{ns}v")

            if cell_type == "s":
                index = int(value_node.text or "0") if value_node is not None else 0
                value: Any = shared_strings[index] if index < len(shared_strings) else ""
            elif cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(f".//{ns}t"))
            else:
                text = value_node.text if value_node is not None else ""
                value = int(text) if text and re.fullmatch(r"\d+", text) else text

            values_by_column[column_index(ref)] = value

        if values_by_column:
            rows.append([values_by_column.get(col, "") for col in range(1, max(values_by_column) + 1)])

    return rows


def build_sheet_xml(rows: list[list[Any]]) -> str:
    xml_rows: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, value in enumerate(row, start=1):
            ref = f"{column_name(col_index)}{row_index}"
            if isinstance(value, int):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = "" if value is None else escape(str(value))
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData>'
        "</worksheet>"
    )


def write_xlsx(path: Path, rows: list[list[Any]]) -> None:
    now = timestamp()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            "</Types>",
        )
        zip_file.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>",
        )
        zip_file.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Monetization" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
        )
        zip_file.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zip_file.writestr("xl/worksheets/sheet1.xml", build_sheet_xml(rows))
        zip_file.writestr(
            "docProps/core.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:creator>Codex</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            "</cp:coreProperties>",
        )
        zip_file.writestr(
            "docProps/app.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            "<Application>Python</Application>"
            "</Properties>",
        )


def result_to_excel_row(result: dict[str, Any]) -> list[Any]:
    return [
        result.get("timestamp") or timestamp(),
        result.get("channel_handle") or "",
        result.get("channel_id") or "",
        result.get("live_deleted_status") or "",
        result.get("shorts_deleted_status") or "",
        result.get("uploads_deleted_status") or "",
        result.get("status") or "",
    ]


def append_result_to_excel(result: dict[str, Any]) -> None:
    global EXCEL_PATH
    rows = read_existing_xlsx_rows(EXCEL_PATH)
    if not rows:
        rows = [EXCEL_HEADERS]
    elif rows[0][: len(EXCEL_HEADERS)] != EXCEL_HEADERS:
        rows.insert(0, EXCEL_HEADERS)

    rows.append(result_to_excel_row(result))
    try:
        write_xlsx(EXCEL_PATH, rows)
        console.print(f"- Excel updated: {EXCEL_PATH}")
    except PermissionError:
        import time
        alternative_path = EXCEL_PATH.parent / f"{EXCEL_PATH.stem}_{int(time.time())}{EXCEL_PATH.suffix}"
        try:
            write_xlsx(alternative_path, rows)
            console.print(f"⚠️ Original file is open! Report saved with alternative name: {alternative_path.name}", "warning")
            EXCEL_PATH = alternative_path
        except Exception as exc:
            console.print(f"❌ Failed to save alternative Excel file: {exc}", "error")


def save_report(results: list[dict[str, Any]]) -> None:
    pass


def print_summary(results: list[dict[str, Any]]) -> None:
    success = sum(1 for item in results if item["status"] == "success")
    failed = sum(1 for item in results if item["status"] == "failed")
    console.print("Summary:", "bold")
    console.print(f"Successful: {success}")
    console.print(f"Failed: {failed}")
    console.print(f"Excel saved to: {EXCEL_PATH}")


def main() -> None:
    print_header()
    results: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        context, page, is_cdp = launch_browser(playwright)
        try:
            if PAUSE_FOR_LOGIN:
                wait_for_manual_login(page)
                
            handles = scrape_channel_handles(page)
            if not handles:
                console.print("No handles found.", "yellow")
                return

            console.print("Step 3: Process channels", "bold")
            for index, handle in enumerate(handles, start=1):
                if stop_event.is_set():
                    console.print("- Stopped by user.", "yellow")
                    break
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
