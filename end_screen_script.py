from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import asyncio
import concurrent.futures
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError, sync_playwright
from playwright.async_api import async_playwright as _async_playwright


# Pause and Resume control
pause_event = threading.Event()
pause_event.set()

# Stop control
stop_event = threading.Event()

def check_pause() -> None:
    if stop_event.is_set():
        raise RuntimeError("Stopped by user.")
    if not pause_event.is_set():
        print("- Paused. Waiting to resume...", flush=True)
        pause_event.wait()
        print("- Resumed.", flush=True)
    if stop_event.is_set():
        raise RuntimeError("Stopped by user.")

def wait_for_manual_login(page: Page) -> None:
    print("Step 1: Check login status", flush=True)
    print("Checking login status...", flush=True)
    
    try:
        page.goto("https://www.youtube.com/", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
        
    avatar = page.locator("button#avatar-btn").first
    logged_in = False
    for _ in range(300):
        if stop_event.is_set():
            raise RuntimeError("Stopped by user.")
            
        try:
            if avatar.is_visible(timeout=500):
                logged_in = True
                break
        except Exception:
            pass
            
        current_url = page.url
        if "accounts.google.com" in current_url or "ServiceLogin" in current_url:
            print("Warning: Please log in in the opened browser window to continue.", flush=True)
        page.wait_for_timeout(2000)
        
    if not logged_in:
        raise RuntimeError("Login timeout expired.")
    print("✓ Login detected successfully!", flush=True)


VIDEO_DETAILS_URL = "https://studio.youtube.com/video/{video_id}/edit"

DEFAULT_CONFIG = {
    "chrome_user_data_root": "bot_profile",
    "chrome_profile_directory": "Default",
    "chrome_executable": "",
    "connect_over_cdp_url": "",
    "studio_videos_url": "https://studio.youtube.com/channel/UCWcgAxp9uB2DLYo7ybZbjvA/videos",
    "ignore_automation_flag": True,
    "headless": False,
    "slow_mo": 250,
    "auto_confirm": True,
    "process_all_channels": True,
    "max_channels": 5,
    "max_videos_per_channel": 5,
    "video_visibility_target": "scheduled",
    "enable_premiere_for_scheduled": True,
    "template_card_index": 3,
    "enable_card_teaser_outline": True,
    "template_keywords": [
        "1 video, 1 subscribe",
        "1 video 1 subscribe",
        "one video one subscribe",
        "فيديو واحد",
        "اشتراك واحد",
    ],
    "screenshots_dir": "artifacts/screenshots",
    "debug_dir": "artifacts/debug",
    "results_json": "artifacts/results.json",
    "results_csv": "artifacts/results.csv",
    "browser_launch_timeout_ms": 30000,
    "navigation_timeout_ms": 45000,
    "action_timeout_ms": 12000,
    "max_parallel_browsers": 30,
    "browser_launch_delay_ms": 5000,
    "max_concurrent_tabs": 15,
    "manual_start": True,
    "tab_open_delay_ms": 0,
}

PROFILE_NAME_PATTERN = re.compile(r"^(Default|Profile \d+|Guest Profile|HENG_Chrome-\d+)$")


def build_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
        stream=sys.stdout,
    )
    return logging.getLogger("youtube-end-screen-bot")


LOGGER = build_logger()


@dataclass
class VideoRecord:
    video_id: str
    title: str
    href: str = ""


@dataclass
class RunResult:
    video_id: str
    title: str
    status: str  # success | skipped | failed
    detail: str
    screenshot: str = ""


class ConfigError(RuntimeError):
    pass


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}\n"
            "Create it from config.example.json, then set chrome_profile_directory."
        )
    with config_path.open("r", encoding="utf-8") as fh:
        user_config = json.load(fh)
    config = dict(DEFAULT_CONFIG)
    config.update(user_config)
    return config


def discover_chrome_user_data_root() -> str:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        local / "Google" / "Chrome" / "User Data",
        local / "Chromium" / "User Data",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def discover_chrome_executable() -> str:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def list_local_profiles(root: str | None = None) -> list[tuple[str, str]]:
    root_path = Path(root or discover_chrome_user_data_root())
    if not root_path.exists():
        return []
    profiles: list[tuple[str, str]] = []
    for child in sorted(root_path.iterdir()):
        if child.is_dir() and PROFILE_NAME_PATTERN.match(child.name):
            profiles.append((child.name, str(child)))
    return profiles


def ensure_directories(config: dict[str, Any]) -> None:
    for key in ("screenshots_dir", "debug_dir", "results_json", "results_csv"):
        path = Path(config[key])
        target = path if path.suffix == "" else path.parent
        target.mkdir(parents=True, exist_ok=True)


def wait_for_any(page: Page, selectors: list[str], timeout_ms: int) -> Locator:
    last_error: Exception | None = None
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if locator.first.is_visible(timeout=350):
                    return locator.first
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        time.sleep(0.2)
    if last_error:
        raise last_error
    raise TimeoutError(f"No selectors matched within {timeout_ms}ms: {selectors}")


class YouTubeStudioTemplateBot:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.chrome_user_data_root = config.get("chrome_user_data_root") or discover_chrome_user_data_root()
        self.chrome_executable = config.get("chrome_executable") or discover_chrome_executable()
        self.chrome_profile_directory = str(config.get("chrome_profile_directory", "")).strip()

        if not self.chrome_user_data_root:
            raise ConfigError("Could not find Chrome user data root. Set chrome_user_data_root in config.json.")
        if not self.chrome_executable:
            raise ConfigError("Could not find Chrome executable. Set chrome_executable in config.json.")
        if not self.chrome_profile_directory:
            raise ConfigError(
                "chrome_profile_directory is required. Run `python main.py --list-profiles` and choose one."
            )

        # If chrome_user_data_root is a relative path like "bot_profile", resolve it absolutely relative to cwd
        root_path = Path(self.chrome_user_data_root)
        if not root_path.is_absolute():
            root_path = Path.cwd() / root_path
        self.chrome_user_data_root = str(root_path)

        self.profile_dir_path = str(root_path / self.chrome_profile_directory)
        
        # Auto-create the profile directory if it doesn't exist to avoid startup crashes
        profile_path = Path(self.profile_dir_path)
        if not profile_path.exists():
            LOGGER.info("Creating new isolated profile directory at: %s", profile_path)
            profile_path.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Entry point
    # ─────────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        ensure_directories(self.config)
        all_results: list[RunResult] = []

        with sync_playwright() as playwright:
            launch_options: dict[str, Any] = {
                "user_data_dir": self.chrome_user_data_root,
                "executable_path": self.chrome_executable,
                "headless": bool(self.config["headless"]),
                "slow_mo": int(self.config["slow_mo"]),
                "timeout": int(self.config.get("browser_launch_timeout_ms", 30000)),
                "args": [
                    f"--profile-directory={self.chrome_profile_directory}",
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--start-maximized",
                    "--test-type",
                ],
            }
            if bool(self.config.get("ignore_automation_flag", True)):
                launch_options["ignore_default_args"] = ["--enable-automation"]

            is_cdp = False
            cdp_url = self.config.get("connect_over_cdp_url", "")
            if cdp_url:
                try:
                    browser = playwright.chromium.connect_over_cdp(cdp_url)
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    is_cdp = True
                    LOGGER.info("Connected to open browser via CDP url: %s", cdp_url)
                except Exception as e:
                    LOGGER.warning("Could not connect to CDP url %s: %s. Launching new browser instead.", cdp_url, e)

            if not is_cdp:
                context = playwright.chromium.launch_persistent_context(**launch_options, no_viewport=True)

            try:
                context.set_default_navigation_timeout(int(self.config["navigation_timeout_ms"]))
                context.set_default_timeout(int(self.config["action_timeout_ms"]))
                main_page = context.pages[0] if context.pages else context.new_page()

                # Call login check first
                wait_for_manual_login(main_page)

                self.wait_for_user_signal(main_page)

                LOGGER.info("Active page URL: %s", main_page.url)
                print("\n[INFO] Scanning all pages for ALL videos...", flush=True)
                videos = self.collect_all_videos(main_page)

                if not videos:
                    LOGGER.warning("No video IDs found. Make sure you are on the Studio videos page.")
                else:
                    # Export session cookies for async parallel phase
                    storage_state_path = str(Path(self.chrome_user_data_root) / "storage_state.json")
                    try:
                        context.storage_state(path=storage_state_path)
                    except Exception as e:
                        LOGGER.warning("Could not save storage state: %s", e)
                        storage_state_path = None

                    print(f"\n[INFO] Found {len(videos)} video(s). Starting TRUE PARALLEL processing...", flush=True)

                    # ── TRUE PARALLEL via asyncio in isolated thread ──
                    _async_results: list = []
                    _async_error: list = []

                    def _run_async() -> None:
                        _loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(_loop)
                        try:
                            _res = _loop.run_until_complete(
                                self._run_batches_async(videos, storage_state_path)
                            )
                            _async_results.extend(_res)
                        except Exception as _e:
                            _async_error.append(_e)
                        finally:
                            _loop.close()

                    _t = threading.Thread(target=_run_async, daemon=False)
                    _t.start()
                    _t.join()

                    if _async_error:
                        raise _async_error[0]

                    all_results.extend(_async_results)

            finally:
                self.write_results(all_results)
                self._print_summary(all_results)
                if not is_cdp:
                    try:
                        context.close()
                    except Exception:
                        pass

    # ─────────────────────────────────────────────────────────────────────────
    # Async parallel processing
    # ─────────────────────────────────────────────────────────────────────────

    async def _run_batches_async(
        self, videos: list[VideoRecord], storage_state_path: str | None
    ) -> list[RunResult]:
        """Process all video batches truly in parallel using asyncio."""
        results: list[RunResult] = []
        max_tabs = int(self.config.get("max_concurrent_tabs", 15))
        batches = [videos[i:i + max_tabs] for i in range(0, len(videos), max_tabs)]

        async with _async_playwright() as pw:
            for batch_idx, batch_videos in enumerate(batches, start=1):
                if stop_event.is_set():
                    break
                if not pause_event.is_set():
                    print("- Paused. Waiting to resume...", flush=True)
                    while not pause_event.is_set():
                        await asyncio.sleep(0.5)
                        if stop_event.is_set():
                            break
                if stop_event.is_set():
                    break

                print(f"\n[INFO] === Batch {batch_idx}/{len(batches)}: {len(batch_videos)} videos ===", flush=True)

                browser = await pw.chromium.launch(
                    headless=False,
                    slow_mo=0,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-background-timer-throttling",
                        "--disable-backgrounding-occluded-windows",
                        "--disable-renderer-backgrounding",
                        "--no-sandbox",
                        "--start-maximized",
                        "--test-type",
                    ],
                    ignore_default_args=["--enable-automation"],
                )

                ctx_kwargs: dict[str, Any] = {}
                if storage_state_path and Path(storage_state_path).exists():
                    ctx_kwargs["storage_state"] = storage_state_path

                ctx = await browser.new_context(**ctx_kwargs, no_viewport=True)
                ctx.set_default_timeout(8000)
                ctx.set_default_navigation_timeout(15000)

                # Open ALL tabs simultaneously (fire & continue)
                pages = []
                for video in batch_videos:
                    try:
                        page = await ctx.new_page()
                        url = VIDEO_DETAILS_URL.format(video_id=video.video_id)
                        await page.goto(url, wait_until="commit")
                        pages.append((video, page))
                        print(f"  [open] {video.title[:70]}", flush=True)
                    except Exception as e:
                        print(f"  [error] {video.title[:60]}: {e}", flush=True)

                # Wait 15 seconds for all pages to load
                print("\n[INFO] Waiting 15 seconds for all tabs to load...", flush=True)
                await asyncio.sleep(15)

                # Process ALL pages TRULY SIMULTANEOUSLY via gather()
                print(f"\n[INFO] Processing {len(pages)} tabs in TRUE PARALLEL...", flush=True)
                tasks = [self._process_tab_async(page, video) for video, page in pages]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in batch_results:
                    if isinstance(r, Exception):
                        print(f"  ✗ [ERROR] {r}", flush=True)
                    elif r is not None:
                        results.append(r)
                        icon = {"success": "✓", "skipped": "→", "failed": "✗"}.get(r.status, "?")
                        print(f"  {icon} [{r.status.upper()}] {r.title[:60]} | {r.detail}", flush=True)

                await ctx.close()
                await browser.close()
                print(f"\n[INFO] Batch {batch_idx}/{len(batches)} done.", flush=True)

        return results

    async def _process_tab_async(self, page: Any, video: VideoRecord) -> RunResult:
        """Process one tab asynchronously: dismiss → skip-check → premiere → save → close."""
        try:
            # Dismiss dialogs via JS
            try:
                await page.evaluate("""
                    () => {
                      const btns = Array.from(document.querySelectorAll('button, tp-yt-paper-button'));
                      for (const b of btns) {
                        const t = (b.textContent || '').trim().toLowerCase();
                        if (['ok','got it','dismiss','close'].includes(t)) b.click();
                      }
                    }
                """)
            except Exception:
                pass

            # Check if premiere already set (via aria-label + aria-checked)
            already_set = await page.evaluate("""
                () => {
                  const cb = document.querySelector('[aria-label="Set as Premiere"][role="checkbox"]');
                  if (!cb) return false;
                  return cb.getAttribute('aria-checked') === 'true';
                }
            """)
            if already_set:
                try: await page.close()
                except Exception: pass
                return RunResult(video_id=video.video_id, title=video.title,
                                 status="skipped", detail="Premiere already set.")

            # Expand visibility card
            try:
                await page.locator("div#content.style-scope.ytcp-video-metadata-visibility").first.click(timeout=5000)
                await asyncio.sleep(0.5)
            except Exception:
                pass

            # Click Premiere checkbox via aria-label
            clicked = await page.evaluate("""
                () => {
                  const cb = document.querySelector('[aria-label="Set as Premiere"][role="checkbox"]');
                  if (cb && cb.getAttribute('aria-checked') !== 'true') {
                    cb.click();
                    return true;
                  }
                  return false;
                }
            """)

            if not clicked:
                try: await page.close()
                except Exception: pass
                return RunResult(video_id=video.video_id, title=video.title,
                                 status="success", detail="No premiere checkbox found.")

            await asyncio.sleep(0.5)

            # Click "Done" button (appears after ticking premiere checkbox)
            await page.evaluate("""
                () => {
                  const buttons = Array.from(document.querySelectorAll(
                    'ytcp-button, button, tp-yt-paper-button'
                  ));
                  for (const b of buttons) {
                    const t = (b.textContent || '').trim().toLowerCase();
                    if (t === 'done' || t === 'تم') {
                      b.click();
                      return true;
                    }
                  }
                  return false;
                }
            """)

            await asyncio.sleep(1)  # wait for Done panel to close

            # Click "Save" button
            saved = await page.evaluate("""
                () => {
                  const buttons = Array.from(document.querySelectorAll(
                    'ytcp-button, button, tp-yt-paper-button'
                  ));
                  for (const b of buttons) {
                    const t = (b.textContent || '').trim().toLowerCase();
                    if (t === 'save' || t === 'حفظ') {
                      b.click();
                      return true;
                    }
                  }
                  return false;
                }
            """)

            await asyncio.sleep(10)  # 10 seconds after Save before closing
            try: await page.close()
            except Exception: pass

            return RunResult(
                video_id=video.video_id, title=video.title,
                status="success",
                detail="Applied premiere successfully." if saved else "Premiere clicked but save not found.",
            )

        except Exception as exc:
            return RunResult(video_id=video.video_id, title=video.title,
                             status="failed", detail=f"Async error: {exc}")



    def wait_for_user_signal(self, page: Page) -> None:
        print("\n[INFO] Navigating to YouTube Studio videos list...", flush=True)
        if "studio.youtube.com" not in page.url or "/videos" not in page.url:
            try:
                page.goto("https://studio.youtube.com/videos", wait_until="domcontentloaded", timeout=40000)
            except Exception:
                pass
        try:
            page.wait_for_selector('ytcp-video-row', timeout=15000)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Video collection (all videos, user-specified count)
    # ─────────────────────────────────────────────────────────────────────────

    def collect_all_videos(self, page: Page) -> list[VideoRecord]:
        """
        Collect only SCHEDULED videos across multiple pages.
        Navigates page-by-page. Stops when a public video is found.
        """
        max_to_collect = int(self.config.get("max_to_collect", 0))
        all_scheduled: list[VideoRecord] = []
        page_num = 1

        while True:
            check_pause()
            print(f"\n[INFO] Scanning page {page_num} for scheduled videos...", flush=True)
            self.dismiss_possible_dialogs(page)

            try:
                page.wait_for_selector('ytcp-video-row', timeout=int(self.config.get('action_timeout_ms', 12000)))
            except Exception as e:
                LOGGER.warning("Video rows not found on page %d: %s", page_num, e)
                break

            self.scroll_content_list(page)

            raw_rows = self.evaluate_with_retries(
                page,
                r"""
                () => {
                  const rows = Array.from(document.querySelectorAll('ytcp-video-row'));
                  return rows.map((row) => {
                    const titleNode = row.querySelector('#video-title');
                    const anchor    = row.querySelector('a[href*="/video/"]');
                    const href      = anchor ? anchor.href : '';
                    const match     = href.match(/\/video\/([^/]+)/);
                    const badge = row.querySelector(
                      'ytcp-video-list-cell-visibility, [class*="visibility"], ytcp-badge, .ytcp-video-list-cell-visibility'
                    );
                    const badgeText = badge ? badge.textContent.trim().toLowerCase() : '';
                    const isScheduled = badgeText.includes('scheduled') || badgeText.includes('مجدول') || badgeText.includes('premiere');
                    const isPublic    = badgeText.includes('public') || badgeText.includes('علني');
                    return {
                      video_id:     match ? match[1] : '',
                      title:        titleNode ? titleNode.textContent.trim() : '',
                      href,
                      is_scheduled: isScheduled,
                      is_public:    isPublic,
                      badge_text:   badgeText,
                    };
                  }).filter(r => r.video_id);
                }
                """,
            )

            if not raw_rows:
                LOGGER.warning("No rows found on page %d.", page_num)
                break

            found_public = False
            page_scheduled: list[VideoRecord] = []

            for row in raw_rows:
                if row.get("is_public"):
                    print(f"  [STOP] Public video: '{row.get('title','')[:60]}' — stopping.", flush=True)
                    found_public = True
                    break
                if row.get("is_scheduled"):
                    page_scheduled.append(VideoRecord(
                        video_id=row["video_id"],
                        title=row["title"],
                        href=row.get("href", ""),
                    ))
                    print(f"  [scheduled] {row['title'][:70]}", flush=True)
                    if max_to_collect > 0 and (len(all_scheduled) + len(page_scheduled)) >= max_to_collect:
                        all_scheduled.extend(page_scheduled)
                        print(f"\n[INFO] Reached requested count of {max_to_collect}. Total: {len(all_scheduled)}", flush=True)
                        return all_scheduled
                else:
                    print(f"  [skip] '{row.get('title','?')[:55]}' (badge: {row.get('badge_text','?')})", flush=True)

            all_scheduled.extend(page_scheduled)
            print(f"  ✓ Page {page_num}: {len(page_scheduled)} scheduled video(s).", flush=True)

            if found_public:
                break

            went_next = self._click_next_page(page)
            if not went_next:
                print("  [INFO] No next-page button. Extraction complete.", flush=True)
                break

            page_num += 1
            time.sleep(2)

        print(f"\n[INFO] Total scheduled videos collected: {len(all_scheduled)}", flush=True)
        return all_scheduled

    def _click_next_page(self, page: Page) -> bool:
        """
        Click the chevron-right (next page) button in YouTube Studio pagination.
        Returns True if clicked successfully, False if not found or disabled.
        """
        try:
            clicked = page.evaluate(r"""
                () => {
                  const btn = document.querySelector('#navigate-after');
                  if (btn) {
                    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') {
                      return false; // Button exists but disabled = last page
                    }
                    btn.click();
                    return true;
                  }
                  
                  // Fallback: The next-page button contains an SVG with this specific path
                  const svgPath = 'M8.793 5.293a1 1 0 000 1.414L14.086 12';
                  const buttons = Array.from(document.querySelectorAll(
                    'button, tp-yt-paper-icon-button, ytcp-icon-button'
                  ));
                  for (const fallbackBtn of buttons) {
                    const path = fallbackBtn.querySelector('path');
                    if (path && path.getAttribute('d') && path.getAttribute('d').startsWith('M8.793 5.293')) {
                      if (fallbackBtn.disabled || fallbackBtn.getAttribute('aria-disabled') === 'true') {
                        return false;  // Button exists but disabled = last page
                      }
                      fallbackBtn.click();
                      return true;
                    }
                  }
                  return false;
                }
            """)
            if clicked:
                # Wait for new page rows to load
                try:
                    page.wait_for_selector('ytcp-video-row', timeout=8000)
                except Exception:
                    pass
            return bool(clicked)
        except Exception as e:
            LOGGER.warning("_click_next_page error: %s", e)
            return False

    def collect_video_ids_from_page(self, page: Page) -> list[VideoRecord]:
        """Scroll the Studio content page and collect all video IDs (legacy)."""
        self.dismiss_possible_dialogs(page)
        try:
            page.wait_for_selector('ytcp-video-row', timeout=int(self.config.get('action_timeout_ms', 12000)))
        except Exception as e:
            LOGGER.warning("Video rows not found after waiting: %s", e)
        self.scroll_content_list(page)

        raw_rows = self.evaluate_with_retries(
            page,
            r"""
            () => {
              const rows = Array.from(document.querySelectorAll('ytcp-video-row'));
              return rows.map((row) => {
                const titleNode = row.querySelector('#video-title');
                const anchor    = row.querySelector('a[href*="/video/"]');
                const href      = anchor ? anchor.href : '';
                const match     = href.match(/\/video\/([^/]+)/);
                return {
                  video_id: match ? match[1] : '',
                  title:    titleNode ? titleNode.textContent.trim() : '',
                  href,
                };
              }).filter(r => r.video_id);
            }
            """,
        )

        max_videos = int(self.config.get("max_videos", 0) or 0)
        videos = [VideoRecord(**item) for item in raw_rows if item.get("video_id")]
        if max_videos > 0:
            videos = videos[:max_videos]
        return videos

    # ─────────────────────────────────────────────────────────────────────────
    # Tab management
    # ─────────────────────────────────────────────────────────────────────────

    def open_video_tabs(
        self, context: BrowserContext, videos: list[VideoRecord]
    ) -> list[tuple[VideoRecord, Page]]:
        """
        Open all passed video detail pages in new tabs.
        Navigations use `wait_until='commit'` to fire and continue without waiting.
        """
        tabs: list[tuple[VideoRecord, Page]] = []
        for idx, video in enumerate(videos, start=1):
            try:
                tab = context.new_page()
                url = VIDEO_DETAILS_URL.format(video_id=video.video_id)
                tab.goto(url, wait_until="commit")
                tabs.append((video, tab))
                print(f"  [open] ({idx}/{len(videos)}) {video.title[:70]}", flush=True)
            except Exception as e:
                print(f"  [error] Could not open tab for {video.title[:60]}: {e}", flush=True)
        return tabs

    # ─────────────────────────────────────────────────────────────────────────
    # Per-video processing
    # ─────────────────────────────────────────────────────────────────────────

    def process_video_tab(self, page: Page, video: VideoRecord) -> RunResult:
        """
        Wait for the tab to finish loading, run skip-checks, then apply
        premiere + end-screen template if needed.
        """
        LOGGER.info("Processing: %s (%s)", video.title, video.video_id)

        # Wait for the page that was opened in the background
        try:
            page.wait_for_load_state("networkidle", timeout=int(self.config["navigation_timeout_ms"]))
        except Exception:  # noqa: BLE001
            pass
        self.dismiss_possible_dialogs(page)

        # ── Skip check (premiere OR end-screen already configured) ──
        skip_reason = self.check_should_skip(page, video)
        if skip_reason:
            return RunResult(
                video_id=video.video_id,
                title=video.title,
                status="skipped",
                detail=skip_reason,
            )

        # ── Apply premiere (only for scheduled videos) ──
        premiere_applied = self.try_enable_premiere(page, video)

        # ── Apply premiere (only for scheduled videos) ──
        if premiere_applied:
            # Save changes only when premiere was enabled
            self.save_changes(page)
            detail = "Applied premiere (first view) successfully."
        else:
            detail = "No changes needed."




        return RunResult(
            video_id=video.video_id,
            title=video.title,
            status="success",
            detail=detail,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Skip detection
    # ─────────────────────────────────────────────────────────────────────────

    def check_should_skip(self, page: Page, video: VideoRecord) -> str:
        """
        Returns a non-empty reason string when the video should be skipped.
        Criteria (either is enough):
          • Premiere checkbox is already checked.
          • End-screen section already has at least one element/preview.
        """
        if self._is_premiere_already_set(page):
            LOGGER.info("Skip %s — premiere already set.", video.video_id)
            return "Skipped: premiere already set."

        if self._is_end_screen_already_set(page):
            LOGGER.info("Skip %s — end screen already configured.", video.video_id)
            return "Skipped: end screen already configured."

        return ""

    def _is_premiere_already_set(self, page: Page) -> bool:
        """True when the 'Set as Premiere' checkbox is already checked."""
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                      const allEls = Array.from(document.querySelectorAll('*'));
                      for (const el of allEls) {
                        const text = (el.textContent || '').trim().toLowerCase();
                        if (text === 'set as premiere' || text === 'جعله عرض أول') {
                          const cb = el.closest('tp-yt-paper-checkbox, [role="checkbox"]')
                            || el.parentElement?.closest('tp-yt-paper-checkbox, [role="checkbox"]');
                          if (cb) {
                            return cb.getAttribute('aria-checked') === 'true'
                              || cb.hasAttribute('checked');
                          }
                        }
                      }
                      return false;
                    }
                    """
                )
            )
        except Exception:  # noqa: BLE001
            return False

    def _is_end_screen_already_set(self, page: Page) -> bool:
        """
        True when the End Screen section already contains at least one element preview.
        Checks for known element containers used by YouTube Studio.
        """
        try:
            return bool(
                page.evaluate(
                    r"""
                    () => {
                      // Direct element nodes in the editor
                      const elementNodes = document.querySelectorAll(
                        'ytcp-endscreen-element-preview, ytve-end-screen-element, .endscreen-element-renderer'
                      );
                      if (elementNodes.length > 0) return true;

                      // Count badge in the end-screen card (e.g. "2 elements")
                      const allText = Array.from(document.querySelectorAll('*'));
                      for (const el of allText) {
                        const t = (el.textContent || '').trim();
                        if (/\d+\s+(elements?|عنصر|عناصر)/.test(t)) return true;
                      }
                      return false;
                    }
                    """
                )
            )
        except Exception:  # noqa: BLE001
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Premiere
    # ─────────────────────────────────────────────────────────────────────────

    def try_enable_premiere(self, page: Page, video: VideoRecord) -> bool:
        """
        Attempt to tick 'Set as Premiere' on the visibility panel.
        Returns True if applied, False if not found or already set.
        """
        if not bool(self.config.get("enable_premiere_for_scheduled", True)):
            return False

        LOGGER.info("Trying to enable Premiere for %s", video.video_id)

        visibility_card = page.locator("div#content.style-scope.ytcp-video-metadata-visibility").first
        try:
            visibility_card.scroll_into_view_if_needed(timeout=3000)
            visibility_card.click(timeout=5000)
        except Exception:  # noqa: BLE001
            LOGGER.info("Visibility card not found for %s. Skipping premiere step.", video.video_id)
            return False

        page.wait_for_timeout(800)

        premiere_label = (
            page.locator("div.label.style-scope.ytcp-checkbox-lit")
            .filter(has_text="Set as Premiere")
            .first
        )
        if premiere_label.count() == 0:
            premiere_label = (
                page.locator("div.label.style-scope.ytcp-checkbox-lit")
                .filter(has_text="جعله عرض أول")
                .first
            )
        if premiere_label.count() == 0:
            LOGGER.info("Premiere checkbox not found for %s.", video.video_id)
            return False

        # Check current state before clicking
        try:
            checkbox_host = premiere_label.locator(
                "xpath=ancestor::*[@role='checkbox' or self::tp-yt-paper-checkbox][1]"
            ).first
            checked_state = checkbox_host.get_attribute("aria-checked")
        except Exception:  # noqa: BLE001
            checked_state = None

        if checked_state == "true":
            LOGGER.info("Premiere already checked for %s.", video.video_id)
            return False

        premiere_label.click(timeout=5000)
        page.wait_for_timeout(700)

        # Close the visibility panel (Done / تم)
        done_button = (
            page.locator("div.ytcpButtonShapeImpl__button-text-content")
            .filter(has_text="Done")
            .first
        )
        if done_button.count() == 0:
            done_button = (
                page.locator("div.ytcpButtonShapeImpl__button-text-content")
                .filter(has_text="تم")
                .first
            )
        if done_button.count() > 0:
            done_button.click(timeout=5000)
            page.wait_for_timeout(1200)

        return True

    # ─────────────────────────────────────────────────────────────────────────
    # End-screen template
    # ─────────────────────────────────────────────────────────────────────────

    def apply_end_screen_template(self, page: Page) -> None:
        LOGGER.info("Applying the '1 video, 1 subscribe' end-screen template.")

        # Open the End screen panel
        trigger = (
            page.locator("span.dropdown-trigger-text.style-scope.ytcp-text-dropdown-trigger")
            .filter(has_text="End screen")
            .first
        )
        if trigger.count() == 0:
            trigger = (
                page.locator("span.dropdown-trigger-text.style-scope.ytcp-text-dropdown-trigger")
                .filter(has_text="شاشة النهاية")
                .first
            )
        if trigger.count() == 0:
            self.write_debug_snapshot(page, "end-screen-card-missing", {"url": page.url})
            raise RuntimeError("Could not find the End screen card on the details page.")

        trigger.click(timeout=5000)
        page.wait_for_timeout(1200)

        # Pick template card
        template_cards = page.locator(
            "div.template-preview.style-scope.ytcp-endscreen-template-picker"
        )
        count = template_cards.count()
        if count == 0:
            self.write_debug_snapshot(page, "end-screen-templates-missing", {"url": page.url})
            raise RuntimeError("Could not find the End screen template cards.")

        template_index = int(self.config.get("template_card_index", 3) or 3) - 1
        if not (0 <= template_index < count):
            template_index = 0

        chosen = template_cards.nth(template_index)
        chosen.scroll_into_view_if_needed(timeout=3000)
        chosen.click(timeout=5000)
        page.wait_for_timeout(1200)

        # Save button inside the modal
        modal_save = page.locator("ytcp-button#save-button.style-scope.ytve-modal-host").first
        if modal_save.count() == 0:
            self.write_debug_snapshot(
                page, "end-screen-modal-save-missing", {"url": page.url}
            )
            raise RuntimeError("Could not find the Save button inside the End screen modal.")
        modal_save.click(timeout=5000)
        page.wait_for_timeout(1500)

    def enable_card_teaser_outline(self, page: Page) -> None:
        if not bool(self.config.get("enable_card_teaser_outline", True)):
            return
        LOGGER.info("Trying to enable card teaser/outline display option.")
        selectors = [
            "label:has-text('عرض مخطط البطاقة')",
            "label:has-text('مخطط البطاقة')",
            "label:has-text('Card teaser')",
            "label:has-text('Show card teaser')",
            "label:has-text('Show card outline')",
            "tp-yt-paper-checkbox:has-text('عرض مخطط البطاقة')",
            "tp-yt-paper-checkbox:has-text('Card teaser')",
            "[aria-label*='عرض مخطط البطاقة']",
            "[aria-label*='Card teaser']",
        ]
        option = self.find_first_visible(page, selectors)
        if option is None:
            return
        try:
            checked = option.get_attribute("aria-checked")
        except Exception:  # noqa: BLE001
            checked = None
        if checked == "true":
            return
        option.click()
        page.wait_for_timeout(700)

    def save_changes(self, page: Page) -> None:
        save_button = self.find_first_visible(
            page,
            [
                "button:has-text('Save')",
                "ytcp-button[id='save'] button",
                "[aria-label='Save']",
            ],
        )
        if save_button is None:
            raise RuntimeError("Save button not found after applying the template.")
        if save_button.is_disabled():
            LOGGER.info("Save button is disabled; the page may have already saved.")
            return
        save_button.click()
        page.wait_for_timeout(2000)

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def capture_failure(self, page: Page, video_id: str) -> str:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output = Path(self.config["screenshots_dir"]) / f"{video_id}-{timestamp}.png"
        try:
            page.screenshot(path=str(output), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        return str(output)

    def write_debug_snapshot(self, page: Page, label: str, payload: dict[str, Any]) -> None:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        base = Path(self.config["debug_dir"]) / f"{label}-{timestamp}"
        for ext, writer in (
            (".png",  lambda p: page.screenshot(path=str(p), full_page=True)),
            (".html", lambda p: p.write_text(page.content(), encoding="utf-8")),
            (".json", lambda p: p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")),
        ):
            try:
                writer(Path(str(base) + ext))
            except Exception:  # noqa: BLE001
                pass

    def write_results(self, results: list[RunResult]) -> None:
        csv_path = Path(self.config["results_csv"])
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["video_id", "title", "status", "detail", "screenshot"],
            )
            writer.writeheader()
            for r in results:
                writer.writerow(asdict(r))
        LOGGER.info("Results written to %s", csv_path)

    def evaluate_with_retries(
        self, page: Page, script: str, arg: Any = None, retries: int = 3
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                return page.evaluate(script) if arg is None else page.evaluate(script, arg)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if "Execution context was destroyed" not in str(exc):
                    raise
                LOGGER.warning(
                    "Page context rebuilt during evaluation. Retrying (%s/%s)...",
                    attempt + 1,
                    retries,
                )
                page.wait_for_timeout(1200)
                page.wait_for_load_state("domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle")
                except Exception:  # noqa: BLE001
                    pass
        if last_error:
            raise last_error
        raise RuntimeError("Evaluation failed unexpectedly with no captured exception.")

    def dismiss_possible_dialogs(self, page: Page) -> None:
        for selector in [
            "button:has-text('Got it')",
            "button:has-text('Not now')",
            "button:has-text('Close')",
            "button:has-text('Skip')",
        ]:
            locator = page.locator(selector)
            try:
                if locator.first.is_visible(timeout=300):
                    locator.first.click()
                    page.wait_for_timeout(400)
            except Exception:  # noqa: BLE001
                continue

    def scroll_content_list(self, page: Page) -> None:
        max_videos = int(self.config.get("max_videos", 0) or 0)
        iterations = (max_videos + 4) if max_videos > 0 else 30
        iterations = min(max(iterations, 10), 80)
        for _ in range(iterations):
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(350)

    def find_first_visible(self, page: Page, selectors: list[str]) -> Locator | None:
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if locator.first.is_visible(timeout=800):
                    return locator.first
            except Exception:  # noqa: BLE001
                continue
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Output helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _print_video_list(self, videos: list[VideoRecord]) -> None:
        print("\nVideos found on this page:")
        print("-" * 80)
        for idx, v in enumerate(videos, start=1):
            print(f"  {idx:>2}. {v.title} | {v.video_id}")
        print("-" * 80)

    def _print_summary(self, results: list[RunResult]) -> None:
        success = sum(1 for r in results if r.status == "success")
        skipped = sum(1 for r in results if r.status == "skipped")
        failed  = sum(1 for r in results if r.status == "failed")
        print("\n" + "=" * 80)
        print(f"  Done.  ✓ success={success}  → skipped={skipped}  ✗ failed={failed}")
        print("=" * 80 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply the '1 video, 1 subscribe' end-screen template to YouTube Studio videos."
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config JSON file. Defaults to config.json.",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List detected local Chrome profiles and exit.",
    )
    return parser.parse_args()


def print_profiles() -> int:
    profiles = list_local_profiles()
    if not profiles:
        print("No Chrome profiles were detected.")
        return 1
    print("Detected Chrome profiles:")
    print("-" * 80)
    for idx, (name, path) in enumerate(profiles, start=1):
        print(f"  {idx:>2}. {name} | {path}")
    print("-" * 80)
    print("Use one of these names in config.json as chrome_profile_directory.")
    return 0


def main() -> int:
    args = parse_args()
    try:
        if args.list_profiles:
            return print_profiles()
        config = load_config(Path(args.config))
        bot = YouTubeStudioTemplateBot(config)
        bot.run()
        return 0
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130
    except ConfigError as exc:
        LOGGER.error(str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
