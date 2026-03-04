"""CallManager — detect and manage voice/video calls.

Two detection strategies:
1. DOM Observer events (primary) — instant notification of incoming call toasts
2. Polling (safety net) — periodically check for call UI elements

When a call is detected:
- Emit call events to EventBus
- Show/hide the browser window as needed
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from teams_tui.bridge.event_bus import Event
from teams_tui.data.models import CallInfo

if TYPE_CHECKING:
    from playwright.async_api import Page

    from teams_tui.bridge.event_bus import EventBus
    from teams_tui.browser.engine import BrowserEngine

log = logging.getLogger(__name__)

# Selectors for call-related UI elements in new Teams
_CALL_SELECTORS = {
    "calling_screen": '[data-tid="calling-screen"], [data-tid="call-monitor"]',
    "hangup_button": '[data-tid="hangup-button"], [data-tid="call-hangup"]',
    "incoming_toast": '[data-tid="call-toast"], [data-tid="incoming-call-notification"]',
    "accept_button": '[data-tid="call-accept-button"], [data-tid="accept-call"]',
    "caller_name": '[data-tid="caller-name"], [data-tid="call-notification-name"]',
}

_POLL_INTERVAL = 2.0  # seconds


class CallManager:
    """Monitors for calls and manages browser window visibility."""

    def __init__(self, engine: BrowserEngine, bus: EventBus) -> None:
        self._engine = engine
        self._bus = bus
        self._in_call = False
        self._call_info: CallInfo | None = None
        self._task: asyncio.Task | None = None

    @property
    def in_call(self) -> bool:
        return self._in_call

    @property
    def call_info(self) -> CallInfo | None:
        return self._call_info

    async def start(self) -> None:
        """Start the polling loop for call detection."""
        self._task = asyncio.create_task(self._poll_loop())
        log.info("Call manager started.")

    async def stop(self) -> None:
        """Stop monitoring."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        """Periodically check for call UI elements (safety net)."""
        try:
            while True:
                await asyncio.sleep(_POLL_INTERVAL)
                try:
                    await self._check_call_state()
                except Exception as e:
                    log.debug("Call poll error: %s", e)
        except asyncio.CancelledError:
            pass

    async def _check_call_state(self) -> None:
        """Check DOM for call indicators."""
        page = self._engine.page

        # Check if call UI is visible
        is_in_call = await page.locator(
            _CALL_SELECTORS["calling_screen"]
        ).count() > 0

        has_hangup = await page.locator(
            _CALL_SELECTORS["hangup_button"]
        ).count() > 0

        call_active = is_in_call or has_hangup

        if call_active and not self._in_call:
            # Call just started
            await self._on_call_started(page)
        elif not call_active and self._in_call:
            # Call just ended
            await self._on_call_ended()

    async def _on_call_started(self, page: Page) -> None:
        """Handle call start — show browser, emit event."""
        self._in_call = True

        # Try to get caller name
        caller = ""
        try:
            name_el = page.locator(_CALL_SELECTORS["caller_name"]).first
            if await name_el.count() > 0:
                caller = await name_el.text_content(timeout=2000) or ""
                caller = caller.strip()
        except Exception:
            pass

        self._call_info = CallInfo(
            call_id="active",
            caller_name=caller or "Rozmowa",
            is_incoming=False,
            is_video=False,
            state="connected",
        )

        log.info("Call started: %s", self._call_info.caller_name)

        # Show browser window
        await self._engine.show_window()

        # Notify TUI
        await self._bus.emit(Event("call_started", {
            "caller_name": self._call_info.caller_name,
            "is_video": self._call_info.is_video,
        }))

    async def _on_call_ended(self) -> None:
        """Handle call end — hide browser, emit event."""
        self._in_call = False
        caller = self._call_info.caller_name if self._call_info else ""
        self._call_info = None

        log.info("Call ended: %s", caller)

        # Hide browser window
        await self._engine.hide_window()

        # Notify TUI
        await self._bus.emit(Event("call_ended", {
            "caller_name": caller,
        }))

    async def handle_incoming_call(self, caller_text: str) -> None:
        """Called by DOMObserver when an incoming call toast is detected."""
        if self._in_call:
            return  # Already in a call

        self._call_info = CallInfo(
            call_id="incoming",
            caller_name=caller_text or "Połączenie przychodzące",
            is_incoming=True,
            is_video=False,
            state="ringing",
        )

        log.info("Incoming call: %s", self._call_info.caller_name)

        # Show browser so user can accept/decline
        await self._engine.show_window()

        await self._bus.emit(Event("call_ringing", {
            "caller_name": self._call_info.caller_name,
            "is_incoming": True,
        }))
