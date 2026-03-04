"""Compose widget — bottom panel for typing messages."""

from __future__ import annotations

from textual.message import Message as TMsg
from textual.widgets import TextArea


class MessageSubmitted(TMsg):
    """Posted when user sends a message."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ComposeWidget(TextArea):
    """Message input area. Enter sends, Shift+Enter for newline."""

    BINDINGS = [
        ("escape", "exit_insert", "Normal mode"),
    ]

    def __init__(self) -> None:
        super().__init__(id="compose", language=None)
        self.show_line_numbers = False

    async def _on_key(self, event) -> None:
        # Shift+Enter is reported as "shift+enter" in Textual
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text:
                self.post_message(MessageSubmitted(text))
                self.clear()
            return
        await super()._on_key(event)

    def action_exit_insert(self) -> None:
        self.app.action_normal_mode()
