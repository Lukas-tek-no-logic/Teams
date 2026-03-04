"""Main screen — 3-pane layout: chat list | messages | compose."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen

from teams_tui.tui.widgets.calendar_view import CalendarView
from teams_tui.tui.widgets.chat_list import ChatListWidget
from teams_tui.tui.widgets.compose import ComposeWidget
from teams_tui.tui.widgets.message_view import MessageView
from teams_tui.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from teams_tui.app import TeamsTUIApp


class MainScreen(Screen):

    def compose(self) -> ComposeResult:
        yield ChatListWidget()
        yield CalendarView()
        with Vertical(id="right-pane"):
            yield MessageView()
            yield ComposeWidget()
        yield StatusBar()

    def on_mount(self) -> None:
        app: TeamsTUIApp = self.app  # type: ignore[assignment]
        store = app._store
        chats = sorted(
            store.chats.values(),
            key=lambda c: c.last_message_time or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        self.query_one(ChatListWidget).load_chats(chats)
        self.query_one(StatusBar).chat_count = len(chats)
