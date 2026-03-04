"""Calendar view widget — agenda list of meetings."""

from __future__ import annotations

from datetime import datetime, timezone

from textual.message import Message as TMsg
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from teams_tui.data.models import CalendarEvent


class EventSelected(TMsg):
    """Posted when user selects a calendar event."""

    def __init__(self, event: CalendarEvent) -> None:
        super().__init__()
        self.event = event


class CalendarView(OptionList):
    """Scrollable agenda of calendar events. j/k navigation, Enter to join."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("up", "cursor_up", "Up"),
        ("enter", "select", "Join"),
    ]

    def __init__(self) -> None:
        super().__init__(id="calendar-view")
        self._events: list[CalendarEvent] = []

    def load_events(self, events: list[CalendarEvent]) -> None:
        """Replace the event list with new data."""
        self._events = events
        self.clear_options()
        now = datetime.now(timezone.utc)
        for event in events:
            date_str = event.start_time.strftime("%a %d.%m")
            time_range = f"{event.start_time.strftime('%H:%M')}-{event.end_time.strftime('%H:%M')}"
            organizer = f" | {event.organizer}" if event.organizer else ""
            recurring = " [dim]↻[/dim]" if event.is_recurring else ""

            # Highlight currently active meetings
            is_now = event.start_time <= now <= event.end_time
            if is_now:
                label = f"[bold green]● {event.title}[/bold green]{recurring}\n  [green]{date_str}  {time_range}{organizer}[/green]"
            else:
                label = f"[bold]{event.title}[/bold]{recurring}\n  [dim]{date_str}[/dim]  {time_range}{organizer}"

            label += "\n"
            self.add_option(Option(label, id=event.id))

        if not events:
            self.add_option(Option("[dim]Brak spotkań.[/dim]", id="__empty__"))

    def action_select(self) -> None:
        idx = self.highlighted
        if idx is None:
            return
        option = self.get_option_at_index(idx)
        if option.id is not None and str(option.id) != "__empty__":
            event = self._get_event_by_id(str(option.id))
            if event:
                self.post_message(EventSelected(event))

    def _get_event_by_id(self, event_id: str) -> CalendarEvent | None:
        for event in self._events:
            if event.id == event_id:
                return event
        return None
