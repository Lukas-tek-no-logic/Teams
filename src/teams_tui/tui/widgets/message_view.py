"""Message view widget — center panel showing conversation messages."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from teams_tui.data.models import Message


class MessageView(VerticalScroll):
    """Scrollable message history for the selected chat."""

    BINDINGS = [
        ("ctrl+u", "scroll_up", "Page up"),
        ("ctrl+d", "scroll_down", "Page down"),
        ("G", "scroll_end", "Bottom"),
    ]

    def __init__(self) -> None:
        super().__init__(id="message-view")
        self._chat_id: str = ""

    def load_messages(self, chat_id: str, messages: list[Message]) -> None:
        """Display messages for a chat."""
        self._chat_id = chat_id
        self.remove_children()

        if not messages:
            self.mount(Static("Brak wiadomości.", classes="empty-hint"))
            return

        for msg in messages:
            time_str = msg.timestamp.strftime("%H:%M")
            sender = msg.sender_name or "?"
            sender_color = "cyan" if sender == "Ty" else "#87ceeb"
            widget = Static(
                f"[bold {sender_color}]{sender}[/bold {sender_color}]  [dim]{time_str}[/dim]\n{msg.content}",
                classes="message",
                markup=True,
            )
            self.mount(widget)

        # Scroll to bottom
        self.scroll_end(animate=False)

    def append_message(self, msg: Message) -> None:
        """Add a single new message at the bottom."""
        time_str = msg.timestamp.strftime("%H:%M")
        sender = msg.sender_name or "?"
        sender_color = "cyan" if sender == "Ty" else "#87ceeb"
        widget = Static(
            f"[bold {sender_color}]{sender}[/bold {sender_color}]  [dim]{time_str}[/dim]\n{msg.content}",
            classes="message",
            markup=True,
        )
        self.mount(widget)
        self.scroll_end(animate=False)

    @property
    def current_chat_id(self) -> str:
        return self._chat_id

    def action_scroll_up(self) -> None:
        self.scroll_page_up()

    def action_scroll_down(self) -> None:
        self.scroll_page_down()
