"""AuthManager — Teams login detection and session persistence."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from playwright.async_api import TimeoutError as PwTimeout

if TYPE_CHECKING:
    from teams_tui.browser.engine import BrowserEngine

log = logging.getLogger(__name__)

TEAMS_URL = "https://teams.cloud.microsoft"

# Selectors that indicate successful login (tried in order).
APP_SHELL_SELECTORS = [
    '[data-tid="app-layout"]',
    '[data-tid="teams-app-bar"]',
    '[data-tid="left-rail"]',
    '[data-tid="chat-list"]',
    '[data-app-section="main"]',
    '#app main',
    'main[role="main"]',
]

# Generous timeout — user may need to complete MFA.
LOGIN_TIMEOUT_MS = 5 * 60 * 1000  # 5 minutes

# Quick probe to check if an existing session is still valid.
PROBE_TIMEOUT_MS = 7_000


class AuthManager:
    """Handles the Teams login flow.

    Session persistence comes for free from launch_persistent_context —
    Chromium keeps cookies, localStorage, IndexedDB, and service workers
    across restarts.  We only need to detect whether the session is still
    valid and, if not, show the browser window for manual login.
    """

    def __init__(self, engine: BrowserEngine) -> None:
        self._engine = engine

    async def ensure_authenticated(self) -> bool:
        """Navigate to Teams and ensure the user is logged in.

        Returns True once auth is confirmed.
        Raises TimeoutError if the user does not complete login in time.
        """
        page = self._engine.page
        log.info("Navigating to Teams…")
        await page.goto(TEAMS_URL, wait_until="domcontentloaded")

        # 1. Fast probe — maybe the persistent session is still valid.
        if await self._probe_logged_in():
            log.info("Existing session valid — already logged in.")
            return True

        # 2. Not logged in — show window so user can authenticate.
        log.info("Session expired or first run — showing browser for login.")
        await self._engine.show_window()
        print(
            "\n╔══════════════════════════════════════════════════╗\n"
            "║  Zaloguj się do Microsoft Teams w oknie          ║\n"
            "║  przeglądarki. Okno zamknie się automatycznie.   ║\n"
            "╚══════════════════════════════════════════════════╝\n"
        )

        # Wait for any sign of successful login.
        if not await self._wait_for_app_shell(LOGIN_TIMEOUT_MS):
            if self._url_looks_authenticated(page.url):
                log.info("Login detected via URL pattern.")
            else:
                await self._dump_diagnostics()
                log.error("Login timed out after 5 minutes.")
                raise PwTimeout("Login timed out")

        await self._engine.hide_window()
        log.info("Login successful — browser window hidden.")
        return True

    # -- helpers -------------------------------------------------------------

    async def _probe_logged_in(self) -> bool:
        """Quick check whether the app shell is already rendered."""
        if await self._wait_for_app_shell(PROBE_TIMEOUT_MS):
            return True
        return self._url_looks_authenticated(self._engine.page.url)

    async def _wait_for_app_shell(self, timeout_ms: int) -> bool:
        """Try multiple selectors — return True as soon as any matches."""
        page = self._engine.page
        combined = ", ".join(APP_SHELL_SELECTORS)
        try:
            await page.wait_for_selector(combined, timeout=timeout_ms)
            log.info("App shell detected (selector match).")
            return True
        except PwTimeout:
            # Also check if URL already looks post-login
            return self._url_looks_authenticated(page.url)

    @staticmethod
    def _url_looks_authenticated(url: str) -> bool:
        """Heuristic: post-login Teams URLs."""
        return (
            "teams.cloud.microsoft" in url
            and ("login" not in url and "oauth" not in url)
        )

    async def _dump_diagnostics(self) -> None:
        """Log current URL and candidate selectors to help debug login detection."""
        page = self._engine.page
        log.warning("=== AUTH DIAGNOSTICS ===")
        log.warning("Current URL: %s", page.url)

        candidates = [
            '[data-tid="app-layout"]',
            '[data-app-section="main"]',
            '#app',
            '[data-tid="teams-app-bar"]',
            '[data-tid="left-rail"]',
            '[data-tid="chat-list"]',
            'main[role="main"]',
            '[class*="app-"]',
        ]
        for sel in candidates:
            try:
                el = await page.query_selector(sel)
                log.warning("  %-40s  %s", sel, "FOUND" if el else "—")
            except Exception:
                log.warning("  %-40s  ERROR", sel)

        # Dump top-level data-tid attributes
        tids = await page.evaluate("""
            () => [...document.querySelectorAll('[data-tid]')]
                .slice(0, 20)
                .map(el => el.getAttribute('data-tid'))
        """)
        log.warning("Top data-tid values: %s", tids)
