"""Search overlay — triggered by / in NORMAL mode."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message as TMsg
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from teams_tui.data.models import Chat
    from teams_tui.data.store import DataStore


class SearchResultSelected(TMsg):
    """Posted when user selects a search result."""

    def __init__(self, chat_id: str) -> None:
        super().__init__()
        self.chat_id = chat_id


class SearchOverlay(ModalScreen):
    """Modal search screen. Searches chat names instantly, messages via cache."""

    BINDINGS = [
        ("escape", "close_search", "Zamknij"),
    ]

    DEFAULT_CSS = """
    SearchOverlay {
        align: center middle;
    }
    #search-box {
        width: 70;
        max-height: 24;
        border: tall $accent;
        background: $surface;
        padding: 1 2;
    }
    #search-input {
        margin-bottom: 1;
    }
    #search-results {
        height: auto;
        max-height: 18;
    }
    #search-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self._store = store
        self._result_chats: list[str] = []  # chat IDs matching results

    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Input(placeholder="Szukaj czatów...", id="search-input")
            yield OptionList(id="search-results")
            yield Label("Enter: otwórz | Esc: zamknij", id="search-hint")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    @staticmethod
    def _normalize(text: str) -> str:
        """Strip diacritics so 'dudzinski' matches 'Dudziński'."""
        nfkd = unicodedata.normalize("NFKD", text.lower())
        return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter chats as user types."""
        query = self._normalize(event.value.strip())
        results = self.query_one("#search-results", OptionList)
        results.clear_options()
        self._result_chats.clear()

        if not query:
            return

        for chat in self._store.chats.values():
            if query in self._normalize(chat.title):
                preview = chat.last_message_preview[:50] if chat.last_message_preview else ""
                label = chat.title
                if preview:
                    label += f"\n  {preview}"
                results.add_option(Option(label, id=chat.id))
                self._result_chats.append(chat.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User selected a search result."""
        idx = event.option_index
        if 0 <= idx < len(self._result_chats):
            chat_id = self._result_chats[idx]
            self.dismiss(chat_id)

    def action_close_search(self) -> None:
        self.dismiss(None)
