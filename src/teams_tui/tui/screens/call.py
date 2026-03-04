"""Call screen — modal overlay showing call status and timer."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class CallTimer(Static):
    """Displays elapsed call time."""

    elapsed: reactive[int] = reactive(0)

    def render(self) -> str:
        mins, secs = divmod(self.elapsed, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            return f"{hours}:{mins:02d}:{secs:02d}"
        return f"{mins}:{secs:02d}"


class CallScreen(ModalScreen):
    """Modal overlay shown during an active call."""

    BINDINGS = [
        ("escape", "dismiss_call", "Zamknij overlay"),
    ]

    DEFAULT_CSS = """
    CallScreen {
        align: center middle;
    }
    #call-box {
        width: 50;
        height: 12;
        border: tall $accent;
        background: $surface;
        padding: 1 2;
    }
    #call-title {
        text-align: center;
        text-style: bold;
        width: 100%;
    }
    #call-caller {
        text-align: center;
        width: 100%;
        margin-top: 1;
    }
    #call-status {
        text-align: center;
        width: 100%;
        color: $success;
    }
    #call-timer {
        text-align: center;
        width: 100%;
        margin-top: 1;
    }
    #call-hint {
        text-align: center;
        width: 100%;
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        caller_name: str = "",
        is_incoming: bool = False,
        is_video: bool = False,
    ) -> None:
        super().__init__()
        self._caller_name = caller_name
        self._is_incoming = is_incoming
        self._is_video = is_video
        self._start_time = time.monotonic()
        self._timer_handle = None

    def compose(self) -> ComposeResult:
        call_type = "Video" if self._is_video else "Audio"
        status = "Dzwoni..." if self._is_incoming else "Połączono"

        with Middle():
            with Center():
                with Static(id="call-box"):
                    yield Label(f"--- {call_type} Call ---", id="call-title")
                    yield Label(self._caller_name or "Rozmowa", id="call-caller")
                    yield Label(status, id="call-status")
                    yield CallTimer(id="call-timer")
                    yield Label("Esc: zamknij overlay | Rozmowa w przeglądarce", id="call-hint")

    def on_mount(self) -> None:
        self._timer_handle = self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        elapsed = int(time.monotonic() - self._start_time)
        self.query_one(CallTimer).elapsed = elapsed

    def update_status(self, status: str) -> None:
        """Update the call status label."""
        try:
            self.query_one("#call-status", Label).update(status)
        except Exception:
            pass

    def action_dismiss_call(self) -> None:
        """Close the overlay (call continues in browser)."""
        self.dismiss()
