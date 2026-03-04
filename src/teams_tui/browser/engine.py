"""BrowserEngine — Playwright lifecycle, window show/hide via CDP."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from playwright.async_api import async_playwright, Page, BrowserContext

if TYPE_CHECKING:
    from teams_tui.config import Config

log = logging.getLogger(__name__)

# Off-screen coordinates used to hide the browser window on X11.
_OFFSCREEN = -32000


class BrowserEngine:
    """Manages a headed Chromium instance with persistent context.

    Must be headed from the start — WebRTC (calls) requires a real GUI.
    The window is hidden by moving it off-screen (X11) or minimizing (Wayland).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._pw = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # -- public properties ---------------------------------------------------

    @property
    def page(self) -> Page:
        assert self._page is not None, "BrowserEngine not started"
        return self._page

    @property
    def context(self) -> BrowserContext:
        assert self._context is not None, "BrowserEngine not started"
        return self._context

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Launch Playwright Chromium in headed mode with a persistent profile.

        Uses Playwright's bundled Chromium (not the system browser) because
        browsers like Vivaldi crash with Playwright's automation flags.
        This runs as a separate process — it does not interfere with the
        user's daily browser.
        """
        self._pw = await async_playwright().start()
        log.info("Launching Playwright Chromium (headed, persistent context)…")

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--use-fake-ui-for-media-stream",  # auto-allow mic/cam prompts
        ]
        # Start off-screen on X11; Wayland ignores window-position.
        if not self._config.is_wayland:
            launch_args.append(f"--window-position={_OFFSCREEN},{_OFFSCREEN}")

        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self._config.browser_profile_dir),
            headless=False,
            args=launch_args,
            viewport={"width": 1280, "height": 900},
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
        )

        # Re-use the default page that Chromium opens.
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        log.info("Chromium started (profile: %s)", self._config.browser_profile_dir)

    async def stop(self) -> None:
        """Close the browser and Playwright."""
        if self._context:
            await self._context.close()
            self._context = None
            self._page = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        log.info("Chromium stopped.")

    # -- window visibility ---------------------------------------------------

    async def show_window(self) -> None:
        """Bring the browser window on-screen."""
        if self._config.is_wayland:
            await self._set_window_state("normal")
        else:
            await self._set_window_bounds(
                left=100, top=100, width=1280, height=900, state="normal",
            )
        log.info("Browser window shown.")

    async def hide_window(self) -> None:
        """Minimize the browser window."""
        await self._set_window_state("minimized")
        log.info("Browser window hidden.")

    # -- CDP helpers ---------------------------------------------------------

    async def _get_window_id(self):
        """Return (cdp_session, window_id) for the current page."""
        cdp = await self._context.new_cdp_session(self._page)
        result = await cdp.send("Browser.getWindowForTarget")
        return cdp, result["windowId"]

    async def _set_window_bounds(
        self,
        *,
        left: int | None = None,
        top: int | None = None,
        width: int | None = None,
        height: int | None = None,
        state: str | None = None,
    ) -> None:
        cdp, wid = await self._get_window_id()
        bounds: dict = {}
        if left is not None:
            bounds["left"] = left
        if top is not None:
            bounds["top"] = top
        if width is not None:
            bounds["width"] = width
        if height is not None:
            bounds["height"] = height
        if state is not None:
            bounds["windowState"] = state
        await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": bounds})
        await cdp.detach()

    async def _set_window_state(self, state: str) -> None:
        cdp, wid = await self._get_window_id()
        await cdp.send(
            "Browser.setWindowBounds",
            {"windowId": wid, "bounds": {"windowState": state}},
        )
        await cdp.detach()
