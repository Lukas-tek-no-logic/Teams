"""Status bar widget — bottom bar showing mode and info."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static

from teams_tui.tui.keybindings import Mode


class StatusBar(Static):
    """Shows current mode, active chat, and connection status."""

    mode: reactive[Mode] = reactive(Mode.NORMAL)
    chat_title: reactive[str] = reactive("")
    chat_count: reactive[int] = reactive(0)
    typing_text: reactive[str] = reactive("")

    def render(self) -> str:
        mode_str = f" {self.mode.value} "
        chat = f" {self.chat_title}" if self.chat_title else ""
        count = f" [{self.chat_count} czatów]" if self.chat_count else ""
        typing = f" | {self.typing_text}" if self.typing_text else ""
        if self.mode == Mode.CALENDAR:
            keys = " j/k:nav  Enter:dołącz  Tab:czat  q:quit"
        else:
            keys = " j/k:nav  Enter:open  i:compose  /:search  q:quit"
        return f"{mode_str}|{chat}{count}{typing} |{keys}"
