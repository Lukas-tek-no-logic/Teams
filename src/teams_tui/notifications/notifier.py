"""Desktop notifications via D-Bus (org.freedesktop.Notifications).

Uses dbus-next for async D-Bus communication.
Gracefully degrades if no notification daemon is available.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class Notifier:
    """Sends desktop notifications via D-Bus."""

    def __init__(self) -> None:
        self._bus = None
        self._proxy = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to the session D-Bus. Non-fatal if unavailable."""
        try:
            from dbus_next.aio import MessageBus

            self._bus = await MessageBus().connect()
            introspection = await self._bus.introspect(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
            )
            proxy_obj = self._bus.get_proxy_object(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                introspection,
            )
            self._proxy = proxy_obj.get_interface(
                "org.freedesktop.Notifications"
            )
            self._connected = True
            log.info("D-Bus notifier connected.")
        except Exception as e:
            log.info("D-Bus notifications unavailable: %s", e)
            self._connected = False

    async def notify(
        self,
        summary: str,
        body: str = "",
        timeout_ms: int = 5000,
    ) -> None:
        """Show a desktop notification. Silently fails if not connected."""
        if not self._connected or not self._proxy:
            return
        try:
            await self._proxy.call_notify(
                "Teams TUI",       # app_name
                0,                 # replaces_id
                "",                # app_icon
                summary,           # summary
                body,              # body
                [],                # actions
                {},                # hints
                timeout_ms,        # expire_timeout
            )
        except Exception as e:
            log.debug("Notification failed: %s", e)

    async def close(self) -> None:
        """Disconnect from D-Bus."""
        if self._bus:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None
            self._proxy = None
            self._connected = False
