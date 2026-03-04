"""Bidirectional async event bus between Playwright and TUI."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str
    data: Any = None
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """Two async queues: events (browser→TUI) and commands (TUI→browser).

    Both Playwright and Textual share the same asyncio loop,
    so no thread-safety concerns — just asyncio.Queue for decoupling.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._events: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._commands: asyncio.Queue[Any] = asyncio.Queue(maxsize=100)

    # -- browser → TUI -------------------------------------------------------

    async def emit(self, event: Event) -> None:
        """Push an event from browser side."""
        await self._events.put(event)

    async def get_event(self) -> Event:
        """Wait for the next event (TUI side)."""
        return await self._events.get()

    @property
    def event_count(self) -> int:
        return self._events.qsize()

    # -- TUI → browser -------------------------------------------------------

    async def send_command(self, command: Any) -> None:
        """Push a typed command from TUI side."""
        await self._commands.put(command)

    async def get_command(self) -> Any:
        """Wait for the next command (browser side)."""
        return await self._commands.get()
