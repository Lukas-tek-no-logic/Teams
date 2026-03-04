"""DataStore — in-memory reactive store, single source of truth."""

from __future__ import annotations

import logging
from typing import Any, Callable

from teams_tui.data.models import CalendarEvent, Chat, Message, User

log = logging.getLogger(__name__)

Listener = Callable[[str, dict[str, Any]], None]


class DataStore:
    """Holds all chats, messages, and users.

    The interceptor feeds data in; the TUI reads and subscribes to changes.
    """

    def __init__(self) -> None:
        self.chats: dict[str, Chat] = {}              # chat_id → Chat
        self.messages: dict[str, list[Message]] = {}   # chat_id → [Message]
        self.users: dict[str, User] = {}               # user_id → User
        self.calendar_events: list[CalendarEvent] = []
        self._listeners: list[Listener] = []

    # -- mutations -----------------------------------------------------------

    def update_chats(self, chats: list[Chat]) -> None:
        for chat in chats:
            self.chats[chat.id] = chat
        log.debug("Store: %d chats total.", len(self.chats))
        self._notify("chats_changed")

    def add_messages(self, chat_id: str, messages: list[Message]) -> None:
        existing = self.messages.setdefault(chat_id, [])
        seen_ids = {m.id for m in existing}
        new = [m for m in messages if m.id not in seen_ids]
        if not new:
            return
        existing.extend(new)
        existing.sort(key=lambda m: m.timestamp)
        log.debug("Store: +%d messages for %s.", len(new), chat_id[:20])
        self._notify("messages_changed", chat_id=chat_id)

    def update_calendar_events(self, events: list[CalendarEvent]) -> None:
        self.calendar_events = events
        log.debug("Store: %d calendar events.", len(events))
        self._notify("calendar_changed")

    def update_user(self, user: User) -> None:
        self.users[user.id] = user

    # -- subscriptions -------------------------------------------------------

    def subscribe(self, callback: Listener) -> None:
        self._listeners.append(callback)

    def _notify(self, event: str, **kwargs: Any) -> None:
        for cb in self._listeners:
            try:
                cb(event, kwargs)
            except Exception:
                log.exception("Store listener error")
