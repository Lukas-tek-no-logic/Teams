"""TeamsTUIApp — root Textual application."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from textual.app import App

from teams_tui.bridge.commands import AddParticipant, CreateNewChat, JoinCall, JoinMeeting, NavigateToChat, RefreshCalendar, SendMessage
from teams_tui.bridge.event_bus import Event
from teams_tui.tui.keybindings import Mode
from teams_tui.tui.screens.call import CallScreen
from teams_tui.tui.screens.main import MainScreen
from teams_tui.tui.widgets.calendar_view import CalendarView, EventSelected
from teams_tui.tui.widgets.chat_list import ChatListWidget, ChatSelected
from teams_tui.tui.widgets.compose import ComposeWidget, MessageSubmitted
from teams_tui.tui.widgets.message_view import MessageView
from teams_tui.tui.widgets.person_picker import PersonPickerOverlay
from teams_tui.tui.widgets.search import SearchOverlay
from teams_tui.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from teams_tui.bridge.event_bus import EventBus
    from teams_tui.browser.engine import BrowserEngine
    from teams_tui.data.cache import Cache
    from teams_tui.data.store import DataStore
    from teams_tui.notifications.notifier import Notifier

log = logging.getLogger(__name__)


class TeamsTUIApp(App):
    CSS_PATH = "teams_tui.tcss"
    TITLE = "Teams TUI"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("i", "insert_mode", "Compose"),
        ("/", "search_mode", "Search"),
        ("h", "focus_chats", "Chats"),
        ("escape", "normal_mode", "Normal"),
        ("ctrl+a", "audio_call", "Audio call"),
        ("ctrl+v", "video_call", "Video call"),
        ("n", "new_chat", "New chat"),
        ("a", "add_participant", "Add person"),
        ("ctrl+tab", "toggle_view", "Calendar/Chat"),
    ]

    def __init__(
        self,
        engine: BrowserEngine,
        bus: EventBus,
        store: DataStore,
        *,
        cache: Cache | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._bus = bus
        self._store = store
        self._cache = cache
        self._notifier = notifier
        self._mode = Mode.NORMAL
        self._current_chat_id: str = ""
        self._call_screen: CallScreen | None = None

    # Actions that should only work in NORMAL mode on MainScreen (blocked in INSERT & CALENDAR)
    _NORMAL_ONLY_ACTIONS = frozenset({
        "insert_mode", "search_mode", "focus_chats",
        "audio_call", "video_call", "new_chat", "add_participant",
    })

    # Actions allowed in NORMAL and CALENDAR modes (blocked only in INSERT)
    _NAV_ACTIONS = frozenset({"toggle_view", "quit"})

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Block normal-mode keybindings when modal is active or in INSERT mode."""
        if action in self._NORMAL_ONLY_ACTIONS:
            if not isinstance(self.screen, MainScreen):
                return False
            if self._mode == Mode.INSERT:
                return False
            if self._mode == Mode.CALENDAR:
                return False
        if action in self._NAV_ACTIONS:
            if not isinstance(self.screen, MainScreen):
                return False
            if self._mode == Mode.INSERT:
                return False
        return True

    # -- lifecycle -----------------------------------------------------------

    def on_mount(self) -> None:
        self.push_screen(MainScreen())
        self.run_worker(self._bridge_loop(), exclusive=True)

    # -- bridge loop: events from browser → TUI ------------------------------

    async def _bridge_loop(self) -> None:
        """Read events from the EventBus and update the UI."""
        try:
            while True:
                event = await self._bus.get_event()
                try:
                    await self._handle_event(event)
                except Exception as e:
                    log.warning("Error handling event %s: %s", event.type, e)
        except asyncio.CancelledError:
            pass

    async def _handle_event(self, event: Event) -> None:
        if event.type == "chats_updated":
            self._store.update_chats(event.data)
            self._refresh_chat_list()

        elif event.type == "messages_received":
            chat_id = event.data["chat_id"]
            messages = event.data["messages"]
            self._store.add_messages(chat_id, messages)

            # Update chat preview and timestamp in store
            if messages:
                last = messages[-1]
                chat = self._store.chats.get(chat_id)
                if chat:
                    chat.last_message_preview = last.content[:120]
                    chat.last_message_time = last.timestamp
                    self._refresh_chat_list()

            # Cache messages to SQLite
            if self._cache and messages:
                try:
                    await self._cache.store_messages(messages)
                except Exception as e:
                    log.debug("Cache store failed: %s", e)

            # Update view if MainScreen is active
            if isinstance(self.screen, MainScreen):
                msg_view = self.screen.query_one(MessageView)
                if msg_view.current_chat_id == chat_id:
                    for msg in messages:
                        msg_view.append_message(msg)

            # Desktop notification for messages in non-active chats
            if chat_id != self._current_chat_id and self._notifier and messages:
                last = messages[-1]
                try:
                    chat = self._store.chats.get(chat_id)
                    title = chat.title if chat else chat_id[:20]
                    await self._notifier.notify(
                        summary=f"{last.sender_name} w {title}",
                        body=last.content[:100],
                    )
                except Exception as e:
                    log.debug("Notification failed: %s", e)

        elif event.type == "chat_created":
            chat_id = event.data.get("chat_id", "")
            if chat_id:
                # Store pending chat ID — will be selected once MainScreen is active
                self._pending_chat_id = chat_id
                self._try_select_pending_chat()

        elif event.type == "users_fetched":
            for user in event.data:
                self._store.update_user(user)

        elif event.type == "typing_started":
            text = event.data.get("text", "")
            if isinstance(self.screen, MainScreen):
                self.screen.query_one(StatusBar).typing_text = text

        elif event.type == "typing_stopped":
            if isinstance(self.screen, MainScreen):
                self.screen.query_one(StatusBar).typing_text = ""

        elif event.type == "calendar_updated":
            events = event.data.get("events", [])
            self._store.update_calendar_events(events)
            if isinstance(self.screen, MainScreen):
                self.screen.query_one(CalendarView).load_events(events)

        elif event.type == "unread_changed":
            items = event.data.get("items", [])
            for item in items:
                chat_id = item.get("chatId", "")
                badge = item.get("text", "")
                if chat_id:
                    chat = self._store.chats.get(chat_id)
                    if chat:
                        try:
                            chat.unread_count = int(badge) if badge.isdigit() else (1 if badge else 0)
                        except (ValueError, TypeError):
                            pass
            self._refresh_chat_list()

        # -- call events ---
        elif event.type == "incoming_call":
            caller = event.data.get("text", "Połączenie przychodzące")
            self.notify(f"Incoming call: {caller}", severity="information")

        elif event.type == "call_ringing":
            caller = event.data.get("caller_name", "")
            self._show_call_screen(caller, is_incoming=True)

        elif event.type == "call_started":
            caller = event.data.get("caller_name", "")
            is_video = event.data.get("is_video", False)
            self._show_call_screen(caller, is_video=is_video)

        elif event.type == "call_ended":
            self._dismiss_call_screen()
            caller = event.data.get("caller_name", "")
            self.notify(f"Rozmowa zakończona: {caller}", severity="information")

    # -- call screen management -----------------------------------------------

    def _show_call_screen(
        self,
        caller_name: str,
        is_incoming: bool = False,
        is_video: bool = False,
    ) -> None:
        if self._call_screen is not None:
            # Already showing a call screen, update status
            status = "Dzwoni..." if is_incoming else "Połączono"
            self._call_screen.update_status(status)
            return
        self._call_screen = CallScreen(
            caller_name=caller_name,
            is_incoming=is_incoming,
            is_video=is_video,
        )
        self.push_screen(self._call_screen, callback=self._on_call_screen_dismissed)

    def _on_call_screen_dismissed(self, _result=None) -> None:
        """Called when CallScreen is dismissed (by user or call_ended)."""
        self._call_screen = None

    def _dismiss_call_screen(self) -> None:
        if self._call_screen is not None:
            try:
                self._call_screen.dismiss()
            except Exception:
                pass
            self._call_screen = None

    # -- chat list helpers ---------------------------------------------------

    def _refresh_chat_list(self) -> None:
        if not isinstance(self.screen, MainScreen):
            return
        chat_list = self.screen.query_one(ChatListWidget)
        from datetime import datetime, timezone
        chats = sorted(
            self._store.chats.values(),
            key=lambda c: c.last_message_time or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        chat_list.load_chats(chats)
        self.screen.query_one(StatusBar).chat_count = len(chats)

    # -- chat selection ------------------------------------------------------

    def on_chat_selected(self, event: ChatSelected) -> None:
        """User selected a chat — load its messages."""
        chat = event.chat
        self._current_chat_id = chat.id
        log.info("Opening chat: %s", chat.title)

        self.screen.query_one(StatusBar).chat_title = chat.title
        self.screen.query_one(StatusBar).typing_text = ""

        messages = self._store.messages.get(chat.id, [])
        self.screen.query_one(MessageView).load_messages(chat.id, messages)

        self.run_worker(self._send_command(NavigateToChat(chat_id=chat.id)))

    # -- message sending -----------------------------------------------------

    def on_message_submitted(self, event: MessageSubmitted) -> None:
        """User submitted a message from compose."""
        if not self._current_chat_id:
            self.notify("Wybierz czat najpierw.", severity="warning")
            return
        text = event.text
        log.info("Sending message to %s: %s", self._current_chat_id[:20], text[:40])
        self.run_worker(
            self._send_command(SendMessage(chat_id=self._current_chat_id, text=text))
        )

    async def _send_command(self, command) -> None:
        """Send a command to the browser via EventBus."""
        await self._bus.send_command(command)

    # -- mode switching ------------------------------------------------------

    def action_normal_mode(self) -> None:
        if self._mode == Mode.CALENDAR:
            # Escape from calendar returns to normal chat view
            self._mode = Mode.NORMAL
            if isinstance(self.screen, MainScreen):
                self.screen.remove_class("calendar-mode")
                self.screen.query_one(StatusBar).mode = Mode.NORMAL
                self.screen.query_one(ChatListWidget).focus()
            return
        self._mode = Mode.NORMAL
        if isinstance(self.screen, MainScreen):
            self.screen.query_one(StatusBar).mode = Mode.NORMAL
            self.screen.query_one(ChatListWidget).focus()

    def action_insert_mode(self) -> None:
        if not self._current_chat_id:
            self.notify("Wybierz czat najpierw.", severity="warning")
            return
        self._mode = Mode.INSERT
        if isinstance(self.screen, MainScreen):
            self.screen.query_one(StatusBar).mode = Mode.INSERT
            self.screen.query_one(ComposeWidget).focus()

    def action_search_mode(self) -> None:
        self.push_screen(SearchOverlay(self._store), callback=self._on_search_result)

    def _on_search_result(self, chat_id: str | None) -> None:
        """Handle search result selection."""
        if not chat_id:
            return
        chat = self._store.chats.get(chat_id)
        if chat:
            from teams_tui.tui.widgets.chat_list import ChatSelected
            self.post_message(ChatSelected(chat))

    def action_focus_chats(self) -> None:
        self._mode = Mode.NORMAL
        if isinstance(self.screen, MainScreen):
            self.screen.query_one(StatusBar).mode = Mode.NORMAL
            self.screen.query_one(ChatListWidget).focus()

    def action_toggle_view(self) -> None:
        """Toggle between chat and calendar views."""
        if not isinstance(self.screen, MainScreen):
            return
        if self._mode == Mode.CALENDAR:
            # Switch back to chat
            self._mode = Mode.NORMAL
            self.screen.remove_class("calendar-mode")
            self.screen.query_one(StatusBar).mode = Mode.NORMAL
            self.screen.query_one(ChatListWidget).focus()
        else:
            # Switch to calendar
            self._mode = Mode.CALENDAR
            self.screen.add_class("calendar-mode")
            self.screen.query_one(StatusBar).mode = Mode.CALENDAR
            self.screen.query_one(CalendarView).focus()
            # Refresh calendar data
            self.run_worker(self._send_command(RefreshCalendar()))

    def on_event_selected(self, event: EventSelected) -> None:
        """User selected a calendar event — join the meeting."""
        cal_event = event.event
        log.info("Joining meeting: %s", cal_event.title)
        self.notify(f"Dołączanie do: {cal_event.title}…", severity="information")
        self.run_worker(
            self._send_command(JoinMeeting(event_title=cal_event.title))
        )

    # -- call actions --------------------------------------------------------

    def action_audio_call(self) -> None:
        if not self._current_chat_id:
            self.notify("Wybierz czat najpierw.", severity="warning")
            return
        log.info("Initiating audio call in %s", self._current_chat_id[:20])
        self.run_worker(
            self._send_command(JoinCall(chat_id=self._current_chat_id, video=False))
        )

    def action_video_call(self) -> None:
        if not self._current_chat_id:
            self.notify("Wybierz czat najpierw.", severity="warning")
            return
        log.info("Initiating video call in %s", self._current_chat_id[:20])
        self.run_worker(
            self._send_command(JoinCall(chat_id=self._current_chat_id, video=True))
        )

    # -- new chat actions ----------------------------------------------------

    def action_new_chat(self) -> None:
        self.push_screen(
            PersonPickerOverlay(self._bus),
            callback=self._on_persons_selected,
        )

    def _on_persons_selected(self, result: list[str] | None) -> None:
        """Handle person picker result — picker is now dismissed."""
        if not result:
            return
        log.info("Creating new chat with: %s", result)
        self.run_worker(
            self._send_command(CreateNewChat(user_names=result))
        )
        # If chat_created event arrived before picker closed, select it now
        self._try_select_pending_chat()

    def _try_select_pending_chat(self) -> None:
        """Select pending chat if MainScreen is active."""
        chat_id = getattr(self, "_pending_chat_id", "")
        if not chat_id:
            return
        if not isinstance(self.screen, MainScreen):
            return
        chat = self._store.chats.get(chat_id)
        if chat:
            self._pending_chat_id = ""
            self.post_message(ChatSelected(chat))
            log.info("Auto-selected new chat: %s", chat.title)

    # -- add participant -----------------------------------------------------

    def action_add_participant(self) -> None:
        if not self._current_chat_id:
            self.notify("Wybierz czat najpierw.", severity="warning")
            return
        self.push_screen(
            PersonPickerOverlay(self._bus),
            callback=self._on_add_participant,
        )

    def _on_add_participant(self, result: list[str] | None) -> None:
        if not result:
            return
        name = result[0]
        log.info("Adding participant '%s' to chat %s", name, self._current_chat_id[:20])
        self.run_worker(
            self._send_command(AddParticipant(chat_id=self._current_chat_id, user_name=name))
        )
