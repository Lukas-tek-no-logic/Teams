"""Person picker overlay — triggered by 'n' in NORMAL mode.

Searches Teams organization directory via browser people picker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from teams_tui.bridge.event_bus import EventBus


class PersonPickerOverlay(ModalScreen):
    """Modal screen for searching and selecting a person to chat with."""

    BINDINGS = [
        ("escape", "cancel", "Anuluj"),
    ]

    DEFAULT_CSS = """
    PersonPickerOverlay {
        align: center middle;
    }
    #picker-box {
        width: 70;
        max-height: 28;
        border: tall $accent;
        background: $surface;
        padding: 1 2;
    }
    #picker-input {
        margin-bottom: 1;
    }
    #picker-results {
        height: auto;
        max-height: 14;
    }
    #picker-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        self._result_names: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Label("Nowy czat — szukaj osób")
            yield Input(placeholder="Wpisz imię i naciśnij Enter...", id="picker-input")
            yield OptionList(id="picker-results")
            yield Label("Enter: szukaj | ↑↓ Enter: otwórz czat | Esc: anuluj", id="picker-hint")

    def on_mount(self) -> None:
        self.query_one("#picker-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """User pressed Enter in input — trigger search."""
        query = event.value.strip()
        if len(query) >= 2:
            self.run_worker(self._do_search(query), exclusive=True)
        else:
            self.notify("Wpisz min. 2 znaki.", severity="warning")

    async def _do_search(self, query: str) -> None:
        """Send SearchPeople command and update results."""
        from teams_tui.bridge.commands import SearchPeople

        results_widget = self.query_one("#picker-results", OptionList)
        results_widget.clear_options()
        results_widget.add_option(Option("Szukam..."))
        self._result_names.clear()

        cmd = SearchPeople(query=query)
        await self._bus.send_command(cmd)

        import asyncio
        try:
            names = await asyncio.wait_for(cmd.future, timeout=15.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            results_widget.clear_options()
            results_widget.add_option(Option("Timeout — spróbuj ponownie"))
            return

        results_widget.clear_options()
        self._result_names.clear()

        if not names:
            results_widget.add_option(Option("Brak wyników"))
            return

        for name in names:
            if not name:
                continue
            display = name.split("\n")[0].strip()
            if not display:
                continue
            results_widget.add_option(Option(display))
            self._result_names.append(display)

        # Focus results so user can navigate with arrows
        results_widget.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User selected a person — dismiss with their name to create chat."""
        idx = event.option_index
        if 0 <= idx < len(self._result_names):
            name = self._result_names[idx]
            self.dismiss([name])

    def action_cancel(self) -> None:
        self.dismiss(None)
