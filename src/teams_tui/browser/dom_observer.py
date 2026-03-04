"""DOMObserver — MutationObserver bridge from browser JS to Python.

Watches for:
- Typing indicators (someone is typing in current chat)
- Incoming call toasts
- Unread badge changes
- New message DOM nodes (backup for API interception)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from teams_tui.bridge.event_bus import Event

if TYPE_CHECKING:
    from playwright.async_api import Page

    from teams_tui.bridge.event_bus import EventBus

log = logging.getLogger(__name__)

# JavaScript injected into the page to observe DOM mutations.
_OBSERVER_JS = """
() => {
    if (window.__teamsTuiObserver) return 'already_installed';

    const dispatch = (type, data) => {
        window.__teamsTuiDomChange(JSON.stringify({ type, data }));
    };

    // --- Typing indicator ---
    const watchTyping = () => {
        const obs = new MutationObserver((mutations) => {
            for (const m of mutations) {
                for (const node of m.addedNodes) {
                    if (!(node instanceof HTMLElement)) continue;
                    const ti = node.matches?.('[data-tid*="typing"]')
                        ? node
                        : node.querySelector?.('[data-tid*="typing"]');
                    if (ti) {
                        dispatch('typing_started', {
                            text: ti.textContent?.trim() || ''
                        });
                    }
                }
                for (const node of m.removedNodes) {
                    if (!(node instanceof HTMLElement)) continue;
                    const ti = node.matches?.('[data-tid*="typing"]')
                        ? node
                        : node.querySelector?.('[data-tid*="typing"]');
                    if (ti) {
                        dispatch('typing_stopped', {});
                    }
                }
            }
        });
        obs.observe(document.body, { childList: true, subtree: true });
        return obs;
    };

    // --- Incoming call toast ---
    const watchCalls = () => {
        const obs = new MutationObserver((mutations) => {
            for (const m of mutations) {
                for (const node of m.addedNodes) {
                    if (!(node instanceof HTMLElement)) continue;
                    const callEl = node.matches?.('[data-tid*="call-toast"], [data-tid*="incoming-call"]')
                        ? node
                        : node.querySelector?.('[data-tid*="call-toast"], [data-tid*="incoming-call"]');
                    if (callEl) {
                        dispatch('incoming_call', {
                            text: callEl.textContent?.trim() || ''
                        });
                    }
                }
            }
        });
        obs.observe(document.body, { childList: true, subtree: true });
        return obs;
    };

    // --- Unread badge changes ---
    const watchUnread = () => {
        const obs = new MutationObserver((mutations) => {
            // Debounce: collect all badge changes, send once
            const badges = document.querySelectorAll(
                '[data-tid*="unread"], .unread-badge, [aria-label*="nieprzeczyt"], [aria-label*="unread"]'
            );
            if (badges.length > 0) {
                const items = [];
                for (const b of badges) {
                    const parent = b.closest('[role="treeitem"]');
                    if (parent) {
                        const link = parent.querySelector('a[href]');
                        const href = link?.getAttribute('href') || '';
                        const m = href.match(/(?:chat|conversations?)\\/([^/?]+)/);
                        items.push({
                            chatId: m ? decodeURIComponent(m[1]) : '',
                            text: b.textContent?.trim() || '',
                        });
                    }
                }
                if (items.length) dispatch('unread_changed', { items });
            }
        });
        obs.observe(document.body, {
            childList: true, subtree: true,
            attributes: true, attributeFilter: ['aria-label', 'class']
        });
        return obs;
    };

    window.__teamsTuiTypingObs = watchTyping();
    window.__teamsTuiCallObs = watchCalls();
    window.__teamsTuiUnreadObs = watchUnread();
    window.__teamsTuiObserver = true;

    return 'installed';
}
"""


class DOMObserver:
    """Injects MutationObserver into Teams page and bridges events to Python."""

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus
        self._page: Page | None = None

    async def install(self, page: Page) -> None:
        """Set up the JS→Python bridge and inject observers."""
        self._page = page

        # Expose a Python function to JS so MutationObserver can call us
        await page.expose_function(
            "__teamsTuiDomChange", self._on_dom_change,
        )

        # Inject the observer script
        result = await page.evaluate(_OBSERVER_JS)
        log.info("DOM observer: %s", result)

        # Re-inject after navigation (Teams SPA may re-render)
        page.on("load", self._reinstall)

    async def _reinstall(self, _page=None) -> None:
        """Re-inject observers after page navigation."""
        if self._page:
            try:
                result = await self._page.evaluate(_OBSERVER_JS)
                if result != "already_installed":
                    log.info("DOM observer re-installed: %s", result)
            except Exception as e:
                log.debug("DOM observer re-install failed: %s", e)

    async def _on_dom_change(self, payload_json: str) -> None:
        """Callback from JS MutationObserver."""
        import json

        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            return

        event_type = payload.get("type", "")
        data = payload.get("data", {})

        if event_type == "typing_started":
            log.debug("Typing: %s", data.get("text", ""))
            await self._bus.emit(Event("typing_started", data))

        elif event_type == "typing_stopped":
            await self._bus.emit(Event("typing_stopped", data))

        elif event_type == "incoming_call":
            log.info("Incoming call detected: %s", data.get("text", ""))
            await self._bus.emit(Event("incoming_call", data))

        elif event_type == "unread_changed":
            await self._bus.emit(Event("unread_changed", data))
