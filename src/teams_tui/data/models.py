"""Domain models for Teams data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ChatType(Enum):
    ONE_ON_ONE = "oneOnOne"
    GROUP = "group"
    CHANNEL = "channel"
    MEETING = "meeting"
    UNKNOWN = "unknown"


class Presence(Enum):
    AVAILABLE = "Available"
    BUSY = "Busy"
    DO_NOT_DISTURB = "DoNotDisturb"
    AWAY = "Away"
    OFFLINE = "Offline"
    UNKNOWN = "unknown"


@dataclass
class User:
    id: str
    display_name: str
    email: str = ""
    presence: Presence = Presence.UNKNOWN


@dataclass
class Message:
    id: str
    chat_id: str
    sender_id: str
    sender_name: str
    content: str
    timestamp: datetime
    is_edited: bool = False
    reply_to_id: str | None = None
    message_type: str = "text"


@dataclass
class Chat:
    id: str
    chat_type: ChatType
    title: str
    participants: list[User] = field(default_factory=list)
    last_message_preview: str = ""
    last_message_time: datetime | None = None
    unread_count: int = 0
    is_muted: bool = False


@dataclass
class CalendarEvent:
    """A single calendar event (meeting) scraped from Teams calendar."""
    id: str
    title: str
    start_time: datetime
    end_time: datetime
    organizer: str = ""
    is_all_day: bool = False
    is_recurring: bool = False
    is_teams_meeting: bool = False
    status: str = ""  # Busy, Free, Tentative


@dataclass
class CallInfo:
    call_id: str
    caller_name: str
    is_incoming: bool
    is_video: bool
    state: str = "ringing"  # ringing, connected, ended
