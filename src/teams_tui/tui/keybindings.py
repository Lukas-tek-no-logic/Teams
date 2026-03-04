"""Vim-style mode system for Teams TUI."""

from enum import Enum


class Mode(Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    COMMAND = "COMMAND"
    CALENDAR = "CALENDAR"
