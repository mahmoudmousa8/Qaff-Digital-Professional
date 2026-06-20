#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qaff YouTube Publish Posts Automation

Navigates to each channel's Community Posts tab and publishes a text post
(optionally with an image) to every channel found in the channel switcher.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any

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
    print("Playwright is not installed. Install with: pip install playwright && playwright install")
    sys.exit(1)

try:
    from rich.console import Console as RichConsole
except Exception:
    RichConsole = None


# ── Settings ──────────────────────────────────────────────────────────────────

CHROME_PROFILE_PATH = r"C:\QaffChromeProfile"
HEADLESS = False
DEFAULT_TIMEOUT = 60_000
CHANNEL_SWITCHER_URL = "https://www.youtube.com/channel_switcher?"
REMOTE_DEBUGGING_PORT = 0

# ── Runtime paths ─────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent

STOP_EVENT = None   # Injected by app.py thread runner


# ── Console ───────────────────────────────────────────────────────────────────

class Console:
    def __init__(self) -> None:
        self._rich = RichConsole() if RichConsole else None

    def print(self, message: str = "", style: str | None = None) -> None:
        if self._rich:
            self._rich.print(message, style=style, markup=False)
        else:
            print(message)


console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_stop():
    """Raise RuntimeError if the stop event is set."""
    if STOP_EVENT is not None and STOP_EVENT.is_set():
        raise RuntimeError("Stopped by user.")


def safe_goto(page: Page, url: str, timeout_ms: int = 3000, max_attempts: int = 3) -> None:
    """Safely navigate to a URL, retrying if it hangs or times out."""
    for attempt in range(1, max_attempts + 1):
        _check_stop()
        console.print(f"  - Navigating to {url} (Attempt {attempt}/{max_attempts})...")
        try:
            # Set a timeout for this navigation attempt
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1000)
            return
        except Exception as e:
            console.print(f"    · Navigation attempt {attempt} failed: {e}", "yellow")
            if attempt == max_attempts:
                raise
            page.wait_for_timeout(1500)


def find_chrome_executable() -> str | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for path in candidates:
        if path and path.exists():
            return str(path)
    return None


def launch_browser(playwright) -> tuple[BrowserContext, Page, bool]:
    """Launch or connect to an existing Chrome browser."""
    port = globals().get("REMOTE_DEBUGGING_PORT", 0)
    if port > 0:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            console.print(f"- Connected to existing browser on port {port}.", "green")
            return context, page, True
        except Exception as e:
            console.print(
                f"- Could not connect to browser on port {port}: {e}. Launching new browser instead.",
                "yellow",
            )

    Path(CHROME_PROFILE_PATH).mkdir(parents=True, exist_ok=True)
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": CHROME_PROFILE_PATH,
        "headless": HEADLESS,
        "accept_downloads": True,
        "no_viewport": True,
        "args": ["--start-maximized"],
    }

    context: BrowserContext | None = None
    chrome_path = find_chrome_executable()

    for attempt in range(1, 4):
        try:
            context = playwright.chromium.launch_persistent_context(
                channel="chrome", **launch_kwargs
            )
            break
        except PlaywrightError as exc:
            if chrome_path and attempt < 3:
                try:
                    context = playwright.chromium.launch_persistent_context(
                        executable_path=chrome_path, **launch_kwargs
                    )
                    break
                except Exception:
                    pass
            if attempt == 3:
                raise RuntimeError(
                    f"Could not launch Google Chrome after {attempt} attempts."
                ) from exc
            time.sleep(1)

    if context is None:
        raise RuntimeError("Could not launch Google Chrome.")

    context.set_default_timeout(DEFAULT_TIMEOUT)
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT)
    return context, page, False


# ── Channel Handle Collection (reuses the same approach as auto_set_script) ────

def _extract_handles_from_item(item: Locator) -> list[str]:
    """Extract @handle texts from a ytd-account-item-renderer."""
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


def collect_handles_from_channel_switcher(page: Page) -> list[str]:
    """Open the channel switcher and collect all @handles."""
    console.print("Step 1: Auto-detect channel handles", "bold")
    console.print("- Opening channel switcher...")
    
    items = page.locator("ytd-account-item-renderer")
    loaded = False
    
    for attempt in range(1, 4):
        _check_stop()
        try:
            if attempt > 1:
                console.print(f"    · Retrying channel switcher load (Attempt {attempt}/3)...")
            safe_goto(page, CHANNEL_SWITCHER_URL, timeout_ms=3000)
            expect(items.first).to_be_visible(timeout=5000)
            loaded = True
            break
        except Exception as e:
            debug_log(f"Channel switcher collect first item not visible on attempt {attempt}: {e}")
            page.wait_for_timeout(1000)
            
    if not loaded:
        console.print("- No channel items were visible on the channel switcher after 3 attempts.", "yellow")
        screenshot_path = "D:/channel_switcher_collect_failed.png"
        try:
            page.screenshot(path=screenshot_path)
            console.print(f"  · Channel switcher capture saved to: {screenshot_path}", "yellow")
        except Exception:
            pass
        return []

    handles: list[str] = []
    seen: set[str] = set()
    last_count = -1
    last_total = -1
    stable_rounds = 0

    for _ in range(20):
        _check_stop()
        count = items.count()
        for index in range(count):
            item = items.nth(index)
            for candidate in _extract_handles_from_item(item):
                if not candidate:
                    continue
                key = candidate.casefold()
                if key in seen:
                    continue
                seen.add(key)
                handles.append(candidate)

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

    if handles:
        console.print(f"- Found {len(handles)} channel(s):", "green")
        for i, h in enumerate(handles, 1):
            console.print(f"  {i}. {h}")
    else:
        console.print("- No channel handles detected.", "yellow")
    console.print()
    return handles


def select_channel_by_handle(page: Page, handle: str) -> None:
    """Switch to a specific channel via the channel switcher."""
    console.print("  - Opening channel switcher...")
    
    items = page.locator("ytd-account-item-renderer")
    loaded = False
    
    for attempt in range(1, 4):
        _check_stop()
        try:
            if attempt > 1:
                console.print(f"    · Retrying channel switcher load (Attempt {attempt}/3)...")
            safe_goto(page, CHANNEL_SWITCHER_URL, timeout_ms=3000)
            expect(items.first).to_be_visible(timeout=5000)
            loaded = True
            break
        except Exception as e:
            debug_log(f"Channel switcher first item not visible on attempt {attempt}: {e}")
            page.wait_for_timeout(1000)
            
    if not loaded:
        # Save a screenshot to help debug channel switcher issues
        screenshot_path = f"D:/channel_switcher_failed_{handle.replace('@', '')}.png"
        try:
            page.screenshot(path=screenshot_path)
            console.print(f"    · Channel switcher screenshot saved to: {screenshot_path}", "yellow")
        except Exception:
            pass
        raise RuntimeError("Channel switcher did not load after 3 attempts (15s total).")

    exact_re = re.compile(rf"^{re.escape(handle)}$", re.IGNORECASE)
    last_count = -1
    stable_scrolls = 0

    for _ in range(10):
        _check_stop()
        count = items.count()
        for index in range(count):
            item = items.nth(index)
            for candidate in _extract_handles_from_item(item):
                if exact_re.fullmatch(candidate.strip()):
                    # Found the right channel — click it
                    clickable = item.locator(
                        "xpath=.//tp-yt-paper-icon-item[@role='option']"
                    ).first
                    clickable.scroll_into_view_if_needed(timeout=10_000)
                    expect(clickable).to_be_visible(timeout=10_000)
                    try:
                        clickable.click(timeout=15_000)
                    except Exception:
                        page.evaluate(
                            "(el) => el && el.click()",
                            clickable.element_handle(),
                        )
                    # Wait for the page to reload and cookies to fully settle
                    # This prevents 403 Forbidden errors on the posts page
                    try:
                        page.wait_for_load_state("networkidle", timeout=10_000)
                    except Exception:
                        pass
                    page.wait_for_timeout(3000)
                    console.print(f"  - Switched to channel {handle}.", "green")
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

    raise RuntimeError(f"Channel {handle} not found in switcher.")


# ── Resolve Channel ID from handle ────────────────────────────────────────────

def resolve_channel_id_from_handle(page: Page, handle: str) -> str | None:
    """
    Navigate to the channel page and extract the channel ID from the URL or
    the page source. Returns None if not found.
    """
    # Clean handle
    clean = handle.lstrip("@").strip()
    url = f"https://www.youtube.com/@{clean}"
    try:
        safe_goto(page, url, timeout_ms=3000)
        page.wait_for_timeout(2000)
    except Exception:
        return None

    # Try to extract from current URL if redirected to /channel/UC...
    current = page.url
    m = re.search(r"/channel/(UC[\w-]{20,})", current)
    if m:
        return m.group(1)

    # Try to extract from page source
    try:
        src = page.content()
        m = re.search(r'"channelId"\s*:\s*"(UC[\w-]{20,})"', src)
        if m:
            return m.group(1)
        # Try alternate key
        m = re.search(r'"externalId"\s*:\s*"(UC[\w-]{20,})"', src)
        if m:
            return m.group(1)
    except Exception:
        pass

    return None


# ── Post Publishing ────────────────────────────────────────────────────────────

def _focus_and_click(page: Page, element: Locator) -> None:
    try:
        element.focus(timeout=2000)
        element.click(timeout=2000)
    except Exception:
        # Fallback to JS focus and click
        try:
            page.evaluate("(el) => { if (el) { el.focus(); el.click(); } }", element.element_handle())
        except Exception:
            pass


def _click_post_text_area(page: Page) -> None:
    """
    Click on the YouTube Community Post text input area.
    """
    placeholder_selectors = [
        '#placeholder-area >> visible=true',
        '#simplebox-placeholder >> visible=true',
        '#placeholder >> visible=true',
        'ytd-backstage-post-dialog-renderer >> visible=true',
    ]
    # First try clicking any placeholder to expand/activate the editor
    for sel in placeholder_selectors:
        try:
            placeholder = page.locator(sel).first
            placeholder.wait_for(state="visible", timeout=1000)
            placeholder.click(timeout=2000)
            page.wait_for_timeout(500)
        except Exception:
            continue

    text_area_selectors = [
        '[contenteditable="true"] >> visible=true',
        '#contenteditable-root >> visible=true',
        '#contenteditable-textarea >> visible=true',
        'ytd-commentbox div[contenteditable="true"][id="contenteditable-root"] >> visible=true',
        'ytd-backstage-post-dialog-renderer div[contenteditable="true"][id="contenteditable-root"] >> visible=true',
    ]

    for sel in text_area_selectors:
        try:
            text_area = page.locator(sel).first
            text_area.wait_for(state="visible", timeout=2000)
            _focus_and_click(page, text_area)
            return
        except Exception:
            continue

    raise RuntimeError("Could not locate the post text area.")


def _type_post_text(page: Page, text: str) -> None:
    """Type the post text into the active contenteditable editor."""
    text_area_selectors = [
        '[contenteditable="true"] >> visible=true',
        '#contenteditable-root >> visible=true',
        '#contenteditable-textarea >> visible=true',
        'ytd-commentbox div[contenteditable="true"][id="contenteditable-root"] >> visible=true',
        'ytd-backstage-post-dialog-renderer div[contenteditable="true"][id="contenteditable-root"] >> visible=true',
    ]
    for sel in text_area_selectors:
        try:
            text_area = page.locator(sel).first
            text_area.wait_for(state="visible", timeout=2000)
            
            # 1. Focus and click the editor to prepare it
            _focus_and_click(page, text_area)
            page.wait_for_timeout(300)
            
            # 2. Try native fill and type first (most reliable for framework bindings)
            try:
                text_area.fill("")
                text_area.type(text, delay=10)
                # Dispatch events to update the framework's internal model
                page.evaluate(
                    "(el) => { "
                    "  if (el) { "
                    "    el.dispatchEvent(new Event('input', { bubbles: true })); "
                    "    el.dispatchEvent(new Event('change', { bubbles: true })); "
                    "    el.dispatchEvent(new Event('blur', { bubbles: true })); "
                    "  } "
                    "}",
                    text_area.element_handle()
                )
                debug_log(f"Typed text natively and dispatched events using {sel}")
                return
            except Exception as e:
                debug_log(f"Native typing failed on {sel}: {e}. Falling back to JS injection.")
                
            # 3. Inject text via DOM innerText and dispatch input events
            # This ensures that YouTube's internal state machine detects the new text
            # and enables the "Post/Publish" submit button.
            page.evaluate(
                "(el, val) => { "
                "  if (el) { "
                "    el.focus(); "
                "    el.innerText = val; "
                "    el.dispatchEvent(new Event('input', { bubbles: true })); "
                "    el.dispatchEvent(new Event('change', { bubbles: true })); "
                "    el.dispatchEvent(new Event('blur', { bubbles: true })); "
                "  } "
                "}",
                text_area.element_handle(),
                text
            )
            page.wait_for_timeout(300)
            return
        except Exception:
            continue
            
    # Fallback: type character by character
    page.keyboard.type(text, delay=20)


def _upload_image(page: Page, image_path: str) -> None:
    """
    Upload an image to a YouTube Community Post.

    Strategy (from DOM inspector):
    1. Click the Image toolbar button to activate the file select container.
    2. Set files directly on the hidden <input type="file" name="FileData">
       inside ytd-backstage-multi-image-select-renderer. This skips the OS file
       dialog entirely and is the most reliable approach.
    3. FALLBACK — click the 'a#select-link' anchor, expect_file_chooser, set files.
    4. LAST RESORT — click any visible image-related button and expect_file_chooser.
    """
    console.print(f"  - Attaching image: {Path(image_path).name}")

    # First, make sure the image attachment container is displayed by clicking the Image toolbar button
    image_btn_selectors = [
        "ytd-button-renderer#image-button button",
        "#image-button button",
        "ytd-button-renderer#image-link-button button",
        "#image-link-button button",
        "ytd-backstage-post-dialog-renderer ytd-button-renderer:nth-of-type(1) button",
        "ytd-commentbox ytd-button-renderer:nth-of-type(1) button",
        "ytd-backstage-post-dialog-renderer ytd-button-renderer:first-of-type button",
        "ytd-commentbox ytd-button-renderer:first-of-type button",
    ]
    for sel in image_btn_selectors:
        try:
            btn = page.locator(sel).first
            btn.wait_for(state="visible", timeout=1500)
            btn.click(timeout=5000)
            console.print(f"    · Clicked image toolbar button via: {sel}")
            page.wait_for_timeout(1000)
            break
        except Exception:
            continue

    uploaded = False

    # ── Strategy 1: set files directly on hidden input (no dialog needed) ──────
    hidden_input_selectors = [
        # Exact selector from DOM inspector
        'ytd-backstage-multi-image-select-renderer input[type="file"][name="FileData"]',
        'ytd-backstage-multi-image-select-renderer input[type="file"]',
        # Broader fallbacks
        'input[type="file"][name="FileData"]',
        'input[type="file"][accept*="image"]',
        'input[type="file"]',
    ]

    for sel in hidden_input_selectors:
        try:
            inp = page.locator(sel).first
            if inp.count() > 0 or page.locator(sel).count() > 0:
                page.locator(sel).first.set_input_files(image_path)
                console.print("    · Image set via hidden file input.")
                uploaded = True
                break
        except Exception:
            continue

    # ── Strategy 2: click a#select-link then use file chooser ─────────────────
    if not uploaded:
        try:
            with page.expect_file_chooser(timeout=10_000) as fc_info:
                select_link = page.locator(
                    "a#select-link.ytd-backstage-multi-image-select-renderer, "
                    "a#select-link"
                ).first
                select_link.wait_for(state="visible", timeout=2000)
                select_link.click(timeout=5000)
            fc_info.value.set_files(image_path)
            console.print("    · Image set via select-link anchor.")
            uploaded = True
        except Exception:
            pass

    # ── Strategy 3: try any image-related clickable button ─────────────────────
    if not uploaded:
        image_btn_selectors_fallback = [
            "ytd-backstage-multi-image-select-renderer button",
            "ytd-post-image-picker-renderer button",
            "#post-image-button",
        ]
        try:
            with page.expect_file_chooser(timeout=10_000) as fc_info:
                clicked = False
                for sel in image_btn_selectors_fallback:
                    try:
                        btn = page.locator(sel).first
                        btn.wait_for(state="visible", timeout=1500)
                        btn.click(timeout=5000)
                        clicked = True
                        break
                    except Exception:
                        continue
                if not clicked:
                    raise RuntimeError("Could not find the image attachment button.")

            fc_info.value.set_files(image_path)
            console.print("    · Image set via fallback button.")
            uploaded = True
        except Exception:
            pass

    # ── Wait for image upload to complete (Verify thumbnail preview or progress bar) ──
    console.print("    · Waiting for image upload processing...")
    page.wait_for_timeout(2000)  # Initial wait for YouTube to start processing
    
    # Wait up to 5 seconds for upload completion
    start_wait = time.time()
    upload_success = False
    while time.time() - start_wait < 5.0:
        _check_stop()
        
        # Check if the thumbnail/preview image is visible
        preview_img = page.locator(
            "ytd-backstage-image-renderer img, "
            "ytd-backstage-post-dialog-renderer ytd-backstage-image-renderer img, "
            "ytd-commentbox ytd-backstage-image-renderer img"
        ).first
        
        # Check progress bar visibility
        prog = page.locator(
            "tp-yt-paper-progress, "
            "ytd-thumbnail-overlay-loading-preview-renderer, "
            "ytd-backstage-image-upload-progress-renderer"
        ).first
        
        try:
            if preview_img.is_visible() and preview_img.get_attribute("src"):
                console.print("    · Image preview is visible and loaded.")
                upload_success = True
                break
        except Exception:
            pass
            
        try:
            if prog.count() > 0 and not prog.is_visible():
                console.print("    · Progress bar is hidden, assuming upload complete.")
                upload_success = True
                break
        except Exception:
            pass
            
        page.wait_for_timeout(500)
        
    if not upload_success:
        console.print("    · Warning: Image preview did not load within 5s. Proceeding anyway...", "yellow")


def _attach_youtube_video(page: Page, video_url: str) -> None:
    """
    Attach a previously uploaded YouTube video to a community post.
    """
    console.print("  - Attaching YouTube video to post...")

    # ── 1. Click the Video toolbar button ────────────────────────────────────
    video_btn_selectors = [
        # ID-based (most stable, confirmed from DOM inspector)
        "ytd-button-renderer#video-link-button button",
        "#video-link-button button",
        "ytd-button-renderer#video-button button",
        "#video-button button",
        # Position-based (5th button = video, as seen in screenshot)
        "ytd-backstage-post-dialog-renderer ytd-button-renderer:nth-of-type(5) button",
        "ytd-commentbox ytd-button-renderer:nth-of-type(5) button",
        "ytd-backstage-post-dialog-renderer ytd-button-renderer:last-of-type button",
        "ytd-commentbox ytd-button-renderer:last-of-type button",
        # ID-based fallback
        "ytd-button-renderer#image-poll-button ~ ytd-button-renderer button",
    ]

    clicked = False
    for sel in video_btn_selectors:
        try:
            btn = page.locator(sel).first
            btn.wait_for(state="visible", timeout=1500)
            btn.click(timeout=5000)
            clicked = True
            console.print(f"    · Clicked video toolbar button via: {sel}")
            break
        except Exception:
            continue

    if not clicked:
        raise RuntimeError(
            "Could not find the YouTube Video attachment button in the post toolbar. "
            "Make sure the post composer is open."
        )

    console.print("    · Waiting 3 seconds for the video picker dialog to load...")
    page.wait_for_timeout(3000)   # Wait 3 seconds for it to load as requested by user

    # ── 2. Locate the picker context (iframe or page) ────────────────────────
    # The Google Picker API operates in an iframe, while native falls back to page
    console.print("    · Locating video picker context...")
    picker = page
    for _ in range(10):
        found = False
        for frame in page.frames:
            if "picker" in frame.url:
                picker = frame
                console.print(f"    · Found Google Picker iframe: {frame.url}")
                found = True
                break
        if found:
            break
        page.wait_for_timeout(500)

    # ── 3. Handle URL input (if URL provided) ────────────────────────────────
    if video_url:
        # Switch to the URL tab inside picker context
        url_tab_selectors = [
            "div[role='tab']:nth-of-type(2)",
            "tp-yt-paper-tab:nth-of-type(2)",
            "[role='tab']:last-of-type",
            "tp-yt-paper-tab:last-of-type",
        ]
        for sel in url_tab_selectors:
            try:
                tab = picker.locator(sel).first
                tab.wait_for(state="visible", timeout=2000)
                tab.click(timeout=5000)
                console.print(f"    · Switched to URL tab via: {sel}")
                page.wait_for_timeout(800)
                break
            except Exception:
                continue

        # Find and fill the URL input
        url_input_selectors = [
            "input[type='text']",
            "input",
            "tp-yt-paper-input input",
        ]
        url_field = None
        for sel in url_input_selectors:
            try:
                candidate = picker.locator(sel).first
                candidate.wait_for(state="visible", timeout=2000)
                url_field = candidate
                console.print(f"    · Found URL input via: {sel}")
                break
            except Exception:
                continue

        if url_field is not None:
            # Focus and click url_field with JS fallback
            try:
                url_field.focus(timeout=2000)
                url_field.click(timeout=2000)
            except Exception:
                try:
                    picker.evaluate("(el) => { if (el) { el.focus(); el.click(); } }", url_field.element_handle())
                except Exception:
                    pass

            try:
                url_field.fill("")
                url_field.fill(video_url)
            except Exception:
                pass

            # Inject value via JS and dispatch DOM events to update DOM state
            try:
                picker.evaluate(
                    "(el, val) => { "
                    "  if (el) { "
                    "    el.value = val; "
                    "    el.dispatchEvent(new Event('input', { bubbles: true })); "
                    "    el.dispatchEvent(new Event('change', { bubbles: true })); "
                    "  } "
                    "}",
                    url_field.element_handle(),
                    video_url
                )
            except Exception:
                pass

            page.wait_for_timeout(2000)
            console.print(f"    · Entered video URL: {video_url}")
        else:
            console.print("    · Warning: URL input not found; trying to select first result.", "yellow")

    # Wait for at least one candidate result to become visible in picker
    try:
        picker.locator(
            ".fPuSnc.Fv4UIc, "
            ".fPuSnc, "
            ".Fv4UIc, "
            "[role='option'], "
            "img[src*='ytimg.com/vi/']"
        ).first.wait_for(state="visible", timeout=6000)
    except Exception:
        pass

    first_video_selectors = [
        # Scoped structural selectors inside picker (ignoring language or dynamic wrapper classes)
        ".fPuSnc.Fv4UIc >> visible=true",
        ".fPuSnc >> visible=true",
        ".Fv4UIc >> visible=true",
        "div[role='option'] >> visible=true",
        "img[src*='ytimg.com/vi/'] >> visible=true",
        "[role='option'] >> visible=true",
        "[role='listitem'] >> visible=true",
        "[data-item] >> visible=true",
        "action[aria-selected] >> visible=true",
    ]

    selected = False
    for sel in first_video_selectors:
        try:
            item = picker.locator(sel).first
            item.wait_for(state="visible", timeout=2000)
            
            # Hover to activate, click via Playwright, and fallback to JS click if needed
            try:
                item.hover(timeout=1000)
                item.click(timeout=3000)
            except Exception:
                picker.evaluate("(el) => { if (el) { el.click(); } }", item.element_handle())
            
            selected = True
            console.print(f"    · Selected first video via: {sel}")
            page.wait_for_timeout(1000)
            break
        except Exception:
            continue

    if not selected:
        raise RuntimeError(
            "Could not find any video in the picker dialog. "
            "Check that the channel has uploaded videos and the dialog loaded correctly."
        )

    # ── 4. Click the "Insert" button inside picker context ───────────────────
    insert_btn_selectors = [
        # Google Picker iframe insert button selectors:
        "div[role='button'][class*='picker-btn'] >> visible=true",
        "div[role='button']:has-text('إدراج') >> visible=true",
        "div[role='button']:has-text('Insert') >> visible=true",
        "div[role='button']:has-text('Select') >> visible=true",
        "div[role='button']:last-of-type >> visible=true",
        "button:last-of-type >> visible=true",
        # Fallbacks for main page host dialog:
        "tp-yt-paper-dialog #select-button button:not([disabled])",
        "tp-yt-paper-dialog #insert-button button:not([disabled])",
        "tp-yt-paper-dialog ytd-button-renderer:last-of-type button:not([disabled])",
        "yt-dialog-renderer ytd-button-renderer:last-of-type button:not([disabled])",
        "ytd-video-picker-renderer ytd-button-renderer:last-of-type button:not([disabled])",
        "tp-yt-paper-dialog [role='button']:last-of-type:not([disabled])",
        "tp-yt-paper-dialog button:not([disabled]):last-of-type",
    ]

    added = False
    page.wait_for_timeout(800)   # Small pause so the button becomes active

    for sel in insert_btn_selectors:
        try:
            btn = picker.locator(sel).last
            btn.wait_for(state="visible", timeout=2000)
            try:
                btn.click(timeout=5000)
            except Exception:
                picker.evaluate("(el) => { if (el) { el.click(); } }", btn.element_handle())
            added = True
            console.print(f"    · Clicked Insert button via selector: {sel}")
            break
        except Exception:
            continue

    if not added:
        raise RuntimeError(
            "Could not find the Insert button after selecting the video. "
            "The button may be disabled or not yet visible."
        )

    page.wait_for_timeout(2000)
    console.print("  - YouTube video attached successfully.", "green")


def debug_log(msg: str) -> None:
    log_path = Path("D:/publish_debug.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _get_active_composer_container(page: Page) -> Locator | None:
    # 1. Dialog composer
    dialog = page.locator("ytd-backstage-post-dialog-renderer").first
    if dialog.count() > 0 and dialog.is_visible():
        return dialog
        
    # 2. Inline composer (usually the first comment box or one with text)
    boxes = page.locator("ytd-commentbox")
    count = boxes.count()
    if count > 0:
        # Prefer the one with content in its editor
        for i in range(count):
            box = boxes.nth(i)
            try:
                editor = box.locator('[contenteditable="true"]').first
                if editor.count() > 0 and len(editor.inner_text().strip()) > 0:
                    return box
            except Exception:
                pass
        # Fallback to the first visible comment box
        for i in range(count):
            box = boxes.nth(i)
            if box.is_visible():
                return box
                
    return None


def _is_button_disabled(renderer: Locator) -> bool:
    try:
        # Check ytd-button-renderer itself
        r_disabled = renderer.get_attribute("disabled")
        r_aria = renderer.get_attribute("aria-disabled")
        r_cls = (renderer.get_attribute("class") or "").lower()
        if r_disabled is not None and r_disabled.lower() != "false":
            return True
        if r_aria == "true":
            return True
        if "disabled" in r_cls:
            return True
            
        # Check inner button
        btn = renderer.locator("button").first
        if btn.count() > 0:
            b_disabled = btn.get_attribute("disabled")
            b_aria = btn.get_attribute("aria-disabled")
            b_cls = (btn.get_attribute("class") or "").lower()
            if b_disabled is not None and b_disabled.lower() != "false":
                return True
            if b_aria == "true":
                return True
            if "disabled" in b_cls:
                return True
    except Exception:
        pass
    return False


def _click_publish_button(page: Page) -> None:
    """
    Click the final Publish / Post button to submit the community post.
    Resolves the active composer, finds its submit button, and clicks it
    using a multi-target native click sequence to guarantee submission.
    """
    debug_log("=== _click_publish_button START ===")
    console.print("    · Waiting for Publish button to become enabled...")
    
    # We will poll for up to 20 seconds for the container and enabled button to appear
    start_time = time.time()
    clicked = False
    
    while time.time() - start_time < 20.0:
        _check_stop()
        
        container = _get_active_composer_container(page)
        if not container:
            page.wait_for_timeout(500)
            continue
            
        renderer = container.locator("ytd-button-renderer#submit-button").first
        if renderer.count() == 0 or not renderer.is_visible():
            page.wait_for_timeout(500)
            continue
            
        if _is_button_disabled(renderer):
            debug_log("Publish button found but it is currently disabled. Waiting...")
            page.wait_for_timeout(500)
            continue
            
        # If we got here, we have a visible, enabled submit button in the active composer!
        debug_log("Found visible, enabled submit button in active composer.")
        
        # Locate all targets
        native_button = renderer.locator("button").first
        shape = renderer.locator("yt-button-shape").first
        
        # Target 1: ytd-button-renderer#submit-button itself (corresponds to Selenium By.id("submit-button"))
        try:
            debug_log("Attempt 1: Clicking ytd-button-renderer parent natively...")
            renderer.scroll_into_view_if_needed(timeout=2000)
            renderer.click(timeout=3000)
            clicked = True
            debug_log("Clicked ytd-button-renderer successfully.")
        except Exception as e:
            debug_log(f"Attempt 1 failed: {e}")
            
        page.wait_for_timeout(1000)
        
        # Check if we still need to click
        if container.is_visible() and renderer.is_visible() and not _is_button_disabled(renderer):
            # Target 2: native button element
            if native_button.count() > 0 and native_button.is_visible():
                try:
                    debug_log("Attempt 2: Clicking native button element...")
                    native_button.click(timeout=3000)
                    clicked = True
                    debug_log("Clicked native button successfully.")
                except Exception as e:
                    debug_log(f"Attempt 2 failed: {e}")
                    
        page.wait_for_timeout(1000)
        
        # Check if we still need to click
        if container.is_visible() and renderer.is_visible() and not _is_button_disabled(renderer):
            # Target 3: yt-button-shape
            if shape.count() > 0 and shape.is_visible():
                try:
                    debug_log("Attempt 3: Clicking yt-button-shape element...")
                    shape.click(timeout=3000)
                    clicked = True
                    debug_log("Clicked yt-button-shape successfully.")
                except Exception as e:
                    debug_log(f"Attempt 3 failed: {e}")
                    
        page.wait_for_timeout(1000)
        
        # Check if we still need to click (Final fallback: JS click)
        if container.is_visible() and renderer.is_visible() and not _is_button_disabled(renderer):
            try:
                debug_log("Attempt 4: Dispatching click event via JS...")
                if native_button.count() > 0:
                    page.evaluate("(el) => { if (el) el.click(); }", native_button.element_handle())
                page.evaluate("(el) => { if (el) el.click(); }", renderer.element_handle())
                clicked = True
                debug_log("Dispatched JS click successfully.")
            except Exception as e:
                debug_log(f"Attempt 4 failed: {e}")
                
        break
        
    debug_log(f"Publish button click finished: clicked={clicked}")
    debug_log("=== _click_publish_button END ===")





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


def _get_subscriber_count(page: Page) -> int:
    """Extract and parse the channel subscriber count from the page."""
    selectors = [
        "yt-formatted-string#subscriber-count",
        "#subscriber-count",
        "yt-content-metadata-view-model span",
        "#channel-tagline [id*='subscriber']",
        "#metadata-line span",
        "#meta #subscriber-count",
    ]
    
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(count):
                txt = loc.nth(i).inner_text(timeout=2000).strip()
                if any(kw in txt for kw in ["subscriber", "مشترك", "subscribers"]):
                    console.print(f"    · Found subscriber count text: '{txt}'")
                    return parse_subscriber_count(txt)
        except Exception:
            continue
            
    # Try general metadata elements or text search
    try:
        elements = page.locator("span, yt-formatted-string").all()
        for el in elements:
            txt = el.inner_text().strip()
            if any(kw in txt for kw in ["subscriber", "مشترك"]):
                console.print(f"    · Found fallback subscriber count text: '{txt}'")
                return parse_subscriber_count(txt)
    except Exception:
        pass
        
    console.print("    · Warning: Could not locate subscriber count element. Assuming 0.", "yellow")
    return 0


def publish_post_to_channel(
    page: Page,
    channel_id: str,
    handle: str,
    post_text: str,
    image_path: str | None = None,
    video_url: str | None = None,
    attach_video: bool = False,
    min_subscribers: int | None = None,
) -> dict[str, Any]:
    """
    Navigate to the channel's Community Posts tab and publish one post.
    Returns a result dict with status, handle, channel_id, error.
    """
    result: dict[str, Any] = {
        "handle": handle,
        "channel_id": channel_id,
        "status": "failed",
        "error": "",
    }

    posts_url = f"https://www.youtube.com/channel/{channel_id}/posts?show_create_dialog=1"
    console.print(f"  - Navigating to posts page: {posts_url}")

    try:
        safe_goto(page, posts_url, timeout_ms=3000)
        page.wait_for_timeout(2000)
        _check_stop()

        # Check subscriber count if threshold is specified
        if min_subscribers is not None:
            console.print("  - Checking channel subscriber count...")
            subs = _get_subscriber_count(page)
            console.print(f"    · Subscriber count: {subs} (required minimum: {min_subscribers})")
            if subs < min_subscribers:
                console.print(
                    f"  ⚠ Skipping channel: subscriber count {subs} is less than threshold {min_subscribers}.",
                    "yellow"
                )
                result["status"] = "skipped"
                result["error"] = f"Subscriber count {subs} < threshold {min_subscribers}"
                return result

        # Click the text area to open the post composer
        console.print("  - Opening post composer...")
        _click_post_text_area(page)
        page.wait_for_timeout(800)
        _check_stop()

        # Type the text (if provided)
        if post_text:
            console.print(f"  - Typing post text ({len(post_text)} chars)...")
            _type_post_text(page, post_text)
            page.wait_for_timeout(500)
            _check_stop()

        # Attach image (if provided)
        if image_path:
            _upload_image(page, image_path)
            _check_stop()

        # Attach YouTube video (if provided or requested)
        if video_url or attach_video:
            _attach_youtube_video(page, video_url)
            _check_stop()

        # Click Publish
        console.print("  - Clicking Publish button...")
        _click_publish_button(page)
        
        console.print("    · Waiting 5 seconds for the post to be completely registered...")
        page.wait_for_timeout(5000)
        
        result["status"] = "success"
        console.print(f"  ✓ Post published for {handle}!", "green")


    except RuntimeError as e:
        if "Stopped by user" in str(e):
            raise
            
        # Capture failure screenshot
        screenshot_path = f"D:/publish_failed_{handle.replace('@', '')}.png"
        try:
            page.screenshot(path=screenshot_path)
            console.print(f"  ✗ Verification failed: {e}", "red")
            console.print(f"    · Failure screenshot saved to: {screenshot_path}", "yellow")
        except Exception as ss_err:
            console.print(f"    · Could not take screenshot: {ss_err}", "yellow")
            
        # Log DOM debug info to publish_debug.log
        try:
            debug_log("=== Verification Failure DOM Debug ===")
            loc = page.locator("ytd-button-renderer#submit-button button")
            for i in range(loc.count()):
                btn = loc.nth(i)
                b_txt = btn.inner_text().strip()
                b_lbl = btn.get_attribute("aria-label") or ""
                b_dis = btn.get_attribute("disabled")
                b_cls = btn.get_attribute("class") or ""
                debug_log(f"Submit Btn {i} - text: '{b_txt}', label: '{b_lbl}', disabled: {b_dis}, class: '{b_cls}'")
        except Exception:
            pass
            
        result["error"] = str(e)
        console.print(f"  ✗ Failed to publish post for {handle}: {e}", "red")
    except Exception as e:
        result["error"] = str(e)
        console.print(f"  ✗ Error publishing post for {handle}: {e}", "red")

    return result


# ── Image Folder Utilities ─────────────────────────────────────────────────────

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def scan_image_folder(folder_path: str) -> list[str]:
    """
    Scan a folder for supported image files.
    Returns a sorted list of absolute image paths.
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        raise RuntimeError(f"Image folder not found: {folder_path}")

    images = sorted(
        str(p) for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    return images


# ── Main orchestration ─────────────────────────────────────────────────────────

def run(
    post_text: str,
    image_path: str | None = None,
    image_paths: list[str] | None = None,   # rotating list from a folder
    video_url: str | None = None,
    attach_video: bool = False,
    handles_override: list[str] | None = None,
    min_subscribers: int | None = None,
) -> list[dict[str, Any]]:
    """
    Main entry-point called by app.py thread.

    Args:
        post_text:    The text content of the post.
        image_path:   Single image path (used for all channels).
        image_paths:  List of image paths that rotate channel-by-channel.
                      Channel 0 → images[0], Channel 1 → images[1], …
                      Wraps around when the list is exhausted.
                      Takes priority over image_path when provided.
        video_url:    URL of a previously published YouTube video.
        attach_video: Whether to attach the first channel video automatically.
        handles_override: Skip channel discovery and use these handles.
        min_subscribers: Skip channels with subscribers less than this value.
    """
    results: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        context, page, is_cdp = launch_browser(pw)

        # Register console and error listeners for debugging
        def log_console(msg):
            debug_log(f"BROWSER CONSOLE [{msg.type}]: {msg.text}")
        def log_error(err):
            debug_log(f"BROWSER EXCEPTION: {err}")
        try:
            page.on("console", log_console)
            page.on("pageerror", log_error)
        except Exception as e:
            debug_log(f"Failed to register page listeners: {e}")

        try:
            # ── Step 1: Collect channel handles ──────────────────────────
            if handles_override:
                handles = handles_override
                console.print(f"Using {len(handles)} manually entered channel handle(s).", "bold")
            else:
                handles = collect_handles_from_channel_switcher(page)

            if not handles:
                console.print("No channels to process. Exiting.", "yellow")
                return results

            total = len(handles)
            console.print(f"\nPublishing to {total} channel(s)...\n", "bold")
            if image_paths:
                console.print(f"Image rotation: {len(image_paths)} image(s) in pool.", "bold")
            if min_subscribers is not None:
                console.print(f"Subscriber threshold limit: Skip channels with < {min_subscribers} subscribers.", "bold")

            # ── Step 2: For each handle, switch channel → get ID → post ──
            for idx, handle in enumerate(handles, 1):
                _check_stop()
                console.print(f"── [{idx}/{total}] Channel: {handle}", "bold cyan")

                # Determine which image to use for this channel
                current_image: str | None = None
                if image_paths:
                    current_image = image_paths[(idx - 1) % len(image_paths)]
                    img_name = Path(current_image).name
                    img_num  = ((idx - 1) % len(image_paths)) + 1
                    console.print(
                        f"  - Image [{img_num}/{len(image_paths)}]: {img_name}"
                    )
                elif image_path:
                    current_image = image_path

                try:
                    # Switch active channel (wait is handled inside select_channel_by_handle)
                    select_channel_by_handle(page, handle)

                    # Resolve channel ID
                    console.print(f"  - Resolving channel ID for {handle}...")
                    channel_id = resolve_channel_id_from_handle(page, handle)
                    if not channel_id:
                        console.print(f"  ✗ Could not resolve channel ID for {handle}.", "red")
                        results.append({
                            "handle": handle,
                            "channel_id": "",
                            "status": "failed",
                            "error": "Could not resolve channel ID",
                        })
                        continue

                    console.print(f"  - Channel ID: {channel_id}")

                    # Publish the post
                    result = publish_post_to_channel(
                        page=page,
                        channel_id=channel_id,
                        handle=handle,
                        post_text=post_text,
                        image_path=current_image,
                        video_url=video_url,
                        attach_video=attach_video,
                        min_subscribers=min_subscribers,
                    )
                    results.append(result)

                except Exception as e:
                    if "Stopped by user" in str(e):
                        console.print("Stopped by user.", "yellow")
                        break
                    console.print(f"  ✗ Unexpected error for {handle}: {e}", "red")
                    results.append({
                        "handle": handle,
                        "channel_id": "",
                        "status": "failed",
                        "error": str(e),
                    })

                console.print()

        finally:
            if not is_cdp:
                try:
                    context.close()
                except Exception:
                    pass

    # ── Summary ───────────────────────────────────────────────────────────────
    success_count = sum(1 for r in results if r["status"] == "success")
    skipped_count = sum(1 for r in results if r["status"] == "skipped")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    console.print("=" * 40, "bold")
    console.print(
        f"Done. Successful: {success_count} | Skipped: {skipped_count} | Failed: {failed_count}",
        "bold"
    )
    console.print("=" * 40, "bold")

    return results


if __name__ == "__main__":
    import sys
    text_arg = sys.argv[1] if len(sys.argv) > 1 else ""
    image_arg = sys.argv[2] if len(sys.argv) > 2 else None
    run(text_arg, image_arg)
