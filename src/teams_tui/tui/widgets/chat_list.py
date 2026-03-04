"""Chat list widget — left panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message as TMsg
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from teams_tui.data.models import Chat


class ChatSelected(TMsg):
    """Posted when user selects a chat."""

    def __init__(self, chat: Chat) -> None:
        super().__init__()
        self.chat = chat


class ChatListWidget(OptionList):
    """Scrollable list of chats. j/k navigation in NORMAL mode."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("up", "cursor_up", "Up"),
        ("enter", "select", "Open"),
        ("l", "select", "Open"),
    ]

    def __init__(self) -> None:
        super().__init__(id="chat-list")
        self._chats: list[Chat] = []

    def load_chats(self, chats: list[Chat]) -> None:
        """Replace the chat list with new data."""
        self._chats = chats
        self.clear_options()
        for i, chat in enumerate(chats):
            unread = f" ({chat.unread_count})" if chat.unread_count else ""
            preview = chat.last_message_preview[:40] if chat.last_message_preview else ""
            label = f"[bold]{chat.title}[/bold]{unread}"
            if preview:
                label += f"\n  [dim]{preview}[/dim]"
            label += "\n"
            self.add_option(Option(label, id=chat.id))

    def action_select(self) -> None:
        idx = self.highlighted
        if idx is None:
            return
        option = self.get_option_at_index(idx)
        if option.id is not None:
            chat = self.get_chat_by_id(str(option.id))
            if chat:
                self.post_message(ChatSelected(chat))

    def get_chat_by_id(self, chat_id: str) -> Chat | None:
        for chat in self._chats:
            if chat.id == chat_id:
                return chat
        return None
