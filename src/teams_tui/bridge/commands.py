"""Typed commands sent from TUI to browser via EventBus."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class SendMessage:
    """Send a text message in the current chat."""
    chat_id: str
    text: str


@dataclass
class NavigateToChat:
    """Navigate the browser to a specific chat to trigger API data loading."""
    chat_id: str


@dataclass
class JoinCall:
    """Initiate or join a call in the given chat."""
    chat_id: str
    video: bool = False


@dataclass
class RefreshChatList:
    """Re-scrape the chat list from DOM."""
    pass


@dataclass
class CreateNewChat:
    """Create a new 1:1 or group chat via browser automation."""
    user_names: list[str]
    group_name: str = ""


@dataclass
class SearchPeople:
    """Search organization directory via Teams people picker. Returns results via future."""
    query: str
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


@dataclass
class AddParticipant:
    """Add a person to the current chat."""
    chat_id: str
    user_name: str


@dataclass
class RefreshCalendar:
    """Scrape calendar events from Teams DOM."""
    pass


@dataclass
class JoinMeeting:
    """Join a Teams meeting from the calendar by title."""
    event_title: str
    with_video: bool = False


# Union type for all commands
Command = SendMessage | NavigateToChat | JoinCall | RefreshChatList | CreateNewChat | SearchPeople | AddParticipant | RefreshCalendar | JoinMeeting
