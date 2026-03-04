"""CommandExecutor — reads commands from EventBus and drives Playwright.

Runs as an async loop alongside the TUI, executing commands sent from
the UI (send message, navigate to chat, join call, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from teams_tui.bridge.commands import (
    AddParticipant,
    CreateNewChat,
    JoinCall,
    JoinMeeting,
    NavigateToChat,
    RefreshCalendar,
    RefreshChatList,
    SearchPeople,
    SendMessage,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

    from teams_tui.bridge.event_bus import EventBus
    from teams_tui.browser.engine import BrowserEngine

log = logging.getLogger(__name__)


class CommandExecutor:
    """Consumes commands from the EventBus and executes them via Playwright."""

    def __init__(self, engine: BrowserEngine, bus: EventBus) -> None:
        self._engine = engine
        self._bus = bus

    @property
    def _page(self) -> Page:
        return self._engine.page

    async def run(self) -> None:
        """Main loop — read commands and dispatch them."""
        try:
            while True:
                command = await self._bus.get_command()
                try:
                    await self._dispatch(command)
                except Exception as e:
                    log.warning("Command failed (%s): %s", type(command).__name__, e)
        except asyncio.CancelledError:
            pass

    async def _dispatch(self, command) -> None:
        if isinstance(command, SendMessage):
            await self._send_message(command)
        elif isinstance(command, NavigateToChat):
            await self._navigate_to_chat(command)
        elif isinstance(command, JoinCall):
            await self._join_call(command)
        elif isinstance(command, RefreshChatList):
            await self._refresh_chat_list(command)
        elif isinstance(command, CreateNewChat):
            await self._create_new_chat(command)
        elif isinstance(command, SearchPeople):
            await self._search_people(command)
        elif isinstance(command, AddParticipant):
            await self._add_participant(command)
        elif isinstance(command, RefreshCalendar):
            await self._refresh_calendar(command)
        elif isinstance(command, JoinMeeting):
            await self._join_meeting(command)
        else:
            log.warning("Unknown command type: %s", type(command))

    async def _send_message(self, cmd: SendMessage) -> None:
        """Type the message into Teams compose box in the browser."""
        page = self._page

        # Try CKEditor contenteditable first
        selectors = [
            '[data-tid="chat-pane-compose-message-footer"] [contenteditable="true"]',
            '[data-tid="ckeditor"] [contenteditable="true"]',
            '[role="textbox"][contenteditable="true"]',
        ]

        for selector in selectors:
            compose = page.locator(selector).first
            try:
                await compose.click(timeout=3000)
                break
            except Exception:
                continue
        else:
            log.warning("Could not find compose box for chat %s", cmd.chat_id[:20])
            return

        # Try fill() first, fallback to keyboard.type() for CKEditor
        try:
            await compose.fill(cmd.text)
        except Exception:
            log.debug("fill() failed, falling back to keyboard.type()")
            await compose.clear()
            await page.keyboard.type(cmd.text, delay=10)

        await page.keyboard.press("Enter")
        log.info("Message sent to %s", cmd.chat_id[:20])

    async def _navigate_to_chat(self, cmd: NavigateToChat) -> None:
        """Click on the chat in the sidebar to load it, then scrape messages."""
        page = self._page
        chat_id = cmd.chat_id

        try:
            # Escape special chars for CSS attribute selector
            escaped = chat_id.replace('"', '\\"')
            selector = f'[role="treeitem"][data-fui-tree-item-value*="{escaped}"]'
            item = page.locator(selector).first

            if await item.count() > 0:
                await item.click(timeout=5000)
                # Wait for messages to render in DOM
                await page.wait_for_timeout(3000)
                log.info("Clicked chat in sidebar: …%s", chat_id[-30:])

                # Scrape messages from DOM
                messages = await self._scrape_messages(chat_id)
                if messages:
                    from teams_tui.bridge.event_bus import Event
                    await self._bus.emit(Event("messages_received", {
                        "chat_id": chat_id,
                        "messages": messages,
                    }))
            else:
                log.warning("Chat not found in sidebar: %s", chat_id[-30:])
        except Exception as e:
            log.warning("Failed to navigate to chat: %s", e)

    async def _scrape_messages(self, chat_id: str) -> list:
        """Scrape messages from the currently visible chat DOM."""
        from teams_tui.data.models import Message
        from datetime import datetime, timezone

        raw = await self._page.evaluate("""
            () => {
                const results = [];
                const msgs = document.querySelectorAll('.fui-ChatMessage, .fui-ChatMyMessage');

                for (const el of msgs) {
                    const isMyMsg = el.classList.contains('fui-ChatMyMessage');
                    const authorEl = el.querySelector('[data-tid="message-author-name"]');
                    const authorName = authorEl?.textContent?.trim() || '';
                    const sender = isMyMsg ? 'Ty' : (authorName || '');

                    // Timestamp from any element with id "author-{unix_ms}"
                    let timestamp = '';
                    const tsEls = el.querySelectorAll('[id*="author-"]');
                    for (const e of tsEls) {
                        const m = e.id.match(/author-(\\d+)/);
                        if (m) { timestamp = m[1]; break; }
                    }

                    // Extract message body — try specific content selectors first
                    const bodyEl = el.querySelector('.fui-ChatMessage__body, .fui-ChatMyMessage__body, [data-tid="message-body"]');
                    let content = '';
                    if (bodyEl) {
                        content = bodyEl.textContent?.trim() || '';
                    } else {
                        // Fallback: full text minus author and time
                        const fullText = el.textContent?.trim() || '';
                        content = fullText;
                        // Remove author name (could appear anywhere)
                        if (authorName) {
                            content = content.replace(authorName, '').trim();
                        }
                        // Remove time prefix
                        content = content.replace(/^(?:poniedziałek|wtorek|środa|czwartek|piątek|sobota|niedziela|wczoraj|yesterday|pon\\.|wt\\.|śr\\.|czw\\.|pt\\.|sob\\.|niedz\\.)?\\s*\\d{1,2}:\\d{2}\\s*/, '');
                    }

                    if (!content || content.length < 1) continue;

                    results.push({
                        id: timestamp || ('dom-' + results.length),
                        sender: sender,
                        content: content.substring(0, 2000),
                        timestamp: timestamp,
                    });
                }
                return results;
            }
        """)

        messages = []
        for item in raw:
            ts = datetime.now(timezone.utc)
            ts_str = item.get("timestamp", "")
            if ts_str and ts_str.isdigit():
                try:
                    ts = datetime.fromtimestamp(int(ts_str) / 1000, tz=timezone.utc)
                except (ValueError, OSError):
                    pass

            messages.append(Message(
                id=item.get("id", ""),
                chat_id=chat_id,
                sender_id="",
                sender_name=item.get("sender", ""),
                content=item.get("content", ""),
                timestamp=ts,
            ))

        log.info("Scraped %d messages from DOM for chat …%s", len(messages), chat_id[-20:])
        return messages

    async def _join_call(self, cmd: JoinCall) -> None:
        """Click the call button in the chat header."""
        page = self._page

        # First make sure we're in the right chat
        await self._navigate_to_chat(NavigateToChat(chat_id=cmd.chat_id))

        # Click audio or video call button
        if cmd.video:
            btn_sel = '[data-tid="chat-header-video-call-button"]'
        else:
            btn_sel = '[data-tid="chat-header-audio-call-button"]'

        try:
            btn = page.locator(btn_sel).first
            await btn.click(timeout=5000)
            log.info("Call initiated (%s) in %s", "video" if cmd.video else "audio", cmd.chat_id[:20])

            # Show browser window for the call
            await self._engine.show_window()
        except Exception as e:
            log.warning("Failed to initiate call: %s", e)

    async def _search_people(self, cmd: SearchPeople) -> None:
        """Search people via Teams search bar (lupka)."""
        page = self._page
        log.info("People search starting for '%s'", cmd.query)
        try:
            # 1. Click search bar
            search_input = page.locator('[data-tid="AUTOSUGGEST_INPUT"]').first
            await search_input.click(timeout=3000)
            await search_input.fill(cmd.query)
            log.info("Typed '%s' in search bar", cmd.query)

            await page.wait_for_timeout(2500)

            # 2. Scrape PEOPLE suggestions from autosuggest
            results = await page.evaluate("""
                () => {
                    const people = [];
                    const options = document.querySelectorAll('[role="option"]');
                    for (const opt of options) {
                        const tid = opt.dataset?.tid || '';
                        if (!tid.includes('PEOPLE') && !tid.includes('TOPHITS8:orgid:'))
                            continue;
                        const text = opt.textContent?.trim() || '';
                        if (!text) continue;
                        // Text: "INITIALSFull Name(ALIAS) JOB TITLE"
                        // or:   "INITIALSFull NameJOB TITLE" (no alias)
                        let clean = text;
                        // Strip 1-3 uppercase ASCII initials prefix (e.g. "WG")
                        clean = clean.replace(/^[A-Z]{1,3}(?=[A-Z][a-z])/, '');
                        // Cut at first "(" (alias) or first all-uppercase word 4+ chars (job title)
                        const cutParen = clean.indexOf('(');
                        const cutUpper = clean.search(/[A-Z]{4,}/);
                        let cutAt = clean.length;
                        if (cutParen > 0) cutAt = Math.min(cutAt, cutParen);
                        if (cutUpper > 0) cutAt = Math.min(cutAt, cutUpper);
                        const displayName = clean.substring(0, cutAt).trim();
                        if (displayName) people.push(displayName);
                    }
                    return people;
                }
            """)

            log.info("People search '%s': %d results: %s", cmd.query, len(results), results[:5])

            # 3. Close search
            await page.keyboard.press("Escape")

            cmd.future.set_result(results or [])

        except Exception as e:
            log.warning("People search failed: %s", e)
            if not cmd.future.done():
                cmd.future.set_result([])

    async def _create_new_chat(self, cmd: CreateNewChat) -> None:
        """Create a new chat by searching for the person in Teams search bar."""
        page = self._page

        if len(cmd.user_names) == 1:
            # 1:1 chat — search for person, click their PEOPLE result
            name = cmd.user_names[0]
            log.info("Creating 1:1 chat with '%s' via search", name)

            search_input = page.locator('[data-tid="AUTOSUGGEST_INPUT"]').first
            await search_input.click(timeout=3000)
            await search_input.fill(name)
            await page.wait_for_timeout(2500)

            # Click first PEOPLE result (person, not chat/meeting/file)
            people_option = page.locator('[role="option"][data-tid*="PEOPLE"]').first
            try:
                await people_option.click(timeout=3000)
                log.info("Clicked person result for '%s'", name)
            except Exception:
                # Fallback: try TOPHITS with orgid (also a person)
                tophit = page.locator('[role="option"][data-tid*="TOPHITS8:orgid:"]').first
                try:
                    await tophit.click(timeout=3000)
                    log.info("Clicked tophit person for '%s'", name)
                except Exception:
                    log.warning("No person result found for '%s'", name)
                    await page.keyboard.press("Escape")
                    return

            await page.wait_for_timeout(3000)
        else:
            # Group chat — open 1:1 with first person, then add others via participants panel
            first = cmd.user_names[0]
            rest = cmd.user_names[1:]
            log.info("Creating group chat: first 1:1 with '%s', then adding %s", first, rest)

            # Open 1:1 chat with first person via search
            search_input = page.locator('[data-tid="AUTOSUGGEST_INPUT"]').first
            await search_input.click(timeout=3000)
            await search_input.fill(first)
            await page.wait_for_timeout(2500)

            people_option = page.locator('[role="option"][data-tid*="PEOPLE"]').first
            try:
                await people_option.click(timeout=3000)
            except Exception:
                tophit = page.locator('[role="option"][data-tid*="TOPHITS8:orgid:"]').first
                try:
                    await tophit.click(timeout=3000)
                except Exception:
                    log.warning("No person result found for '%s'", first)
                    await page.keyboard.press("Escape")
                    return

            await page.wait_for_timeout(3000)

            # Now add remaining people via participants panel
            await page.locator('[data-tid="chat-header-participant-count"]').first.click(timeout=3000)
            await page.wait_for_timeout(1000)
            await page.locator('[data-tid="chat-add-members"]').first.click(timeout=3000)
            await page.wait_for_timeout(1000)

            # Type each name in the "Enter a name, email, or tag" input
            add_input = page.locator('input[aria-label="Enter a name, email, or tag"]').first
            for name in rest:
                await add_input.click(timeout=2000)
                await add_input.fill(name)
                await page.wait_for_timeout(2500)
                # Click first people-picker suggestion (li element)
                try:
                    suggestion = page.locator('li[data-tid*="people-picker-entry-"]').first
                    await suggestion.click(timeout=3000)
                    log.info("Added '%s' to group", name)
                except Exception:
                    log.warning("No suggestion for '%s'", name)
                await page.wait_for_timeout(500)

            # Confirm — wait for button to be enabled, then click
            confirm_btn = page.locator('[data-tid="chat-add-members-confirm"]')
            for _ in range(10):
                disabled = await confirm_btn.is_disabled()
                if not disabled:
                    break
                await page.wait_for_timeout(300)
            try:
                await confirm_btn.click(timeout=3000)
            except Exception:
                await page.keyboard.press("Escape")

            await page.wait_for_timeout(2000)

            # Set group name if provided
            if cmd.group_name:
                try:
                    name_btn = page.locator('[data-tid="chat-title-name-group-chat"]').first
                    await name_btn.click(timeout=2000)
                    await page.keyboard.type(cmd.group_name, delay=10)
                    await page.keyboard.press("Enter")
                except Exception:
                    pass

        # Send a message to materialize the phantom chat in sidebar
        await self._send_materialize_message(page)

        # Now chat exists in sidebar — refresh and detect it
        from teams_tui.browser.interceptor import scrape_chat_list
        from teams_tui.bridge.event_bus import Event

        chats = await scrape_chat_list(page)
        if chats:
            await self._bus.emit(Event("chats_updated", chats))

        # The newest chat (first in list) is the one we just created
        active_chat_id = chats[0].id if chats else ""

        if active_chat_id:
            log.info("New chat active: %s", active_chat_id[-30:])
            messages = await self._scrape_messages(active_chat_id)
            if messages:
                await self._bus.emit(Event("messages_received", {
                    "chat_id": active_chat_id,
                    "messages": messages,
                }))
            await self._bus.emit(Event("chat_created", {"chat_id": active_chat_id}))

        log.info("New chat created with: %s", ", ".join(cmd.user_names))

    async def _add_participant(self, cmd: AddParticipant) -> None:
        """Add a person to the current chat via participants panel."""
        page = self._page
        log.info("Adding '%s' to chat %s", cmd.user_name, cmd.chat_id[-20:])
        try:
            # 1. Open participants panel
            await page.locator('[data-tid="chat-header-participant-count"]').first.click(timeout=3000)
            await page.wait_for_timeout(1000)

            # 2. Click "Start a group chat" / "Add members"
            await page.locator('[data-tid="chat-add-members"]').first.click(timeout=3000)
            await page.wait_for_timeout(1000)

            # 3. Type name in people picker input
            add_input = page.locator('input[aria-label="Enter a name, email, or tag"]').first
            await add_input.click(timeout=2000)
            await add_input.fill(cmd.user_name)
            await page.wait_for_timeout(2500)

            # 4. Click first person suggestion (skip the search container itself)
            suggestion = page.locator('li[data-tid*="people-picker-entry-"]').first
            await suggestion.click(timeout=3000)
            log.info("Selected suggestion for '%s'", cmd.user_name)
            await page.wait_for_timeout(1000)

            # 5. Wait for confirm button to become enabled, then click it
            confirm_btn = page.locator('[data-tid="chat-add-members-confirm"]')
            await confirm_btn.wait_for(state="visible", timeout=3000)
            # Wait until enabled (disabled while no person is selected)
            for _ in range(10):
                disabled = await confirm_btn.is_disabled()
                if not disabled:
                    break
                await page.wait_for_timeout(300)
            await confirm_btn.click(timeout=3000)
            log.info("Clicked confirm (Create/Utwórz) button")

            await page.wait_for_timeout(2000)

            # Teams creates a phantom group chat — send a message to materialize it
            await self._send_materialize_message(page)

            # Now the chat exists in sidebar — refresh and detect it
            from teams_tui.browser.interceptor import scrape_chat_list
            from teams_tui.bridge.event_bus import Event

            chats = await scrape_chat_list(page)
            if chats:
                await self._bus.emit(Event("chats_updated", chats))

            # Find the new group chat — it should be the first (most recent) chat
            active_chat_id = chats[0].id if chats else ""

            if active_chat_id and active_chat_id != cmd.chat_id:
                messages = await self._scrape_messages(active_chat_id)
                if messages:
                    await self._bus.emit(Event("messages_received", {
                        "chat_id": active_chat_id,
                        "messages": messages,
                    }))
                await self._bus.emit(Event("chat_created", {"chat_id": active_chat_id}))
                log.info("New group chat: %s", active_chat_id[-30:])

        except Exception as e:
            log.warning("Failed to add participant: %s", e)
            await page.keyboard.press("Escape")

    async def _send_materialize_message(self, page) -> None:
        """Send a short message in the currently open chat to materialize phantom chats.

        Teams creates phantom/draft group chats that don't appear in the sidebar
        until a message is sent. This sends a dot to make the chat real.
        """
        selectors = [
            '[data-tid="chat-pane-compose-message-footer"] [contenteditable="true"]',
            '[data-tid="ckeditor"] [contenteditable="true"]',
            '[role="textbox"][contenteditable="true"]',
        ]
        for selector in selectors:
            compose = page.locator(selector).first
            try:
                await compose.click(timeout=3000)
                break
            except Exception:
                continue
        else:
            log.warning("Could not find compose box to materialize chat")
            return

        try:
            await compose.fill(".")
        except Exception:
            await page.keyboard.type(".", delay=10)

        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)
        log.info("Sent materialize message ('.')")

    async def _refresh_chat_list(self, cmd: RefreshChatList) -> None:
        """Re-scrape chat list from DOM."""
        from teams_tui.browser.interceptor import scrape_chat_list
        from teams_tui.bridge.event_bus import Event

        chats = await scrape_chat_list(self._page)
        if chats:
            await self._bus.emit(Event("chats_updated", chats))

    async def _refresh_calendar(self, cmd: RefreshCalendar) -> None:
        """Click Calendar sidebar, scrape events from iframe, then return to Chat."""
        from datetime import datetime, timezone
        from teams_tui.bridge.event_bus import Event
        from teams_tui.data.models import CalendarEvent
        import re

        page = self._page
        try:
            # 1. Click Calendar button in sidebar
            cal_btn = page.locator('button[data-tid="ef56c0de-36fc-4ef8-b417-3d82ba9d073c"]').first
            await cal_btn.click(timeout=5000)
            log.info("Clicked Calendar sidebar button")
            await page.wait_for_timeout(3000)

            # 2. Get calendar iframe
            frame = page.frame("embedded-page-container")
            if not frame:
                # Fallback: try locator-based iframe access
                iframe_loc = page.locator('[data-tid="hwc-iframe"]').first
                iframe_el = await iframe_loc.element_handle(timeout=5000)
                if iframe_el:
                    frame = await iframe_el.content_frame()

            if not frame:
                log.warning("Calendar iframe not found")
                return

            # Wait for calendar content to render
            await frame.wait_for_selector('[role="main"] div[role="button"]', timeout=10000)

            # 3. Scrape events from aria-label
            raw_events = await frame.evaluate("""
                () => {
                    const results = [];
                    const buttons = document.querySelectorAll('[role="main"] div[role="button"]');
                    for (const btn of buttons) {
                        const label = btn.getAttribute('aria-label') || '';
                        if (!label || label.length < 5) continue;
                        results.push(label);
                    }
                    return results;
                }
            """)
            log.debug("Calendar aria-label samples: %s", [l[:80] for l in raw_events[:3]])

            # 4. Parse aria-label strings into CalendarEvent objects
            # Format: "tytuł, od HH:MM do HH:MM, dzień, data, [typ], [By organizator], status[, Recurring]"
            events: list[CalendarEvent] = []
            now = datetime.now(timezone.utc)

            for i, label in enumerate(raw_events):
                try:
                    event = self._parse_calendar_label(label, i, now)
                    if event:
                        events.append(event)
                except Exception as e:
                    log.debug("Failed to parse calendar label: %s — %s", label[:60], e)

            log.info("Scraped %d calendar events", len(events))

            # 5. Emit event
            await self._bus.emit(Event("calendar_updated", {"events": events}))

        except Exception as e:
            log.warning("Failed to refresh calendar: %s", e)
        finally:
            # 6. Click Chat sidebar button to go back
            try:
                chat_btn = page.locator('button[data-tid="2a84919f-59d8-4441-a975-2a8c2643b741"]').first
                if await chat_btn.count() > 0:
                    await chat_btn.click(timeout=3000)
                    log.info("Returned to Chat view")
            except Exception:
                pass

    @staticmethod
    def _parse_calendar_label(label: str, index: int, now: datetime) -> CalendarEvent | None:
        """Parse a calendar event aria-label into a CalendarEvent."""
        import re
        from datetime import datetime, timezone
        from teams_tui.data.models import CalendarEvent

        # Split by comma — first part is title
        parts = [p.strip() for p in label.split(",")]
        if len(parts) < 3:
            return None

        title = parts[0]

        # Try to extract date from parts — look for patterns like:
        # "5 marca 2026", "March 5, 2026", "5 March 2026", "05.03.2026"
        event_date = now.date()
        _MONTHS = {
            # Polish — dopełniacz (genitive)
            "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4,
            "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
            "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
            # Polish — mianownik (nominative, used in aria-labels)
            "styczeń": 1, "luty": 2, "marzec": 3, "kwiecień": 4,
            "maj": 5, "czerwiec": 6, "lipiec": 7, "sierpień": 8,
            "wrzesień": 9, "październik": 10, "listopad": 11, "grudzień": 12,
            # English
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }

        for part in parts:
            p = part.strip()
            # Pattern: "6 Marzec 2026" or "5 marca 2026"
            m = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})', p)
            if m:
                month = _MONTHS.get(m.group(2).lower())
                if month:
                    try:
                        event_date = datetime(int(m.group(3)), month, int(m.group(1))).date()
                    except ValueError:
                        pass
                    break
            # Pattern: "Piątek, 6 Marzec 2026" — day name prefix, date embedded
            m = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', p)
            if m:
                month = _MONTHS.get(m.group(2).lower())
                if month:
                    try:
                        event_date = datetime(int(m.group(3)), month, int(m.group(1))).date()
                    except ValueError:
                        pass
                    break
            # English: "March 5, 2026" or "March 5 2026"
            m = re.match(r'(\w+)\s+(\d{1,2})', p)
            if m:
                month = _MONTHS.get(m.group(1).lower())
                if month:
                    yr_match = re.search(r'(\d{4})', p)
                    year = int(yr_match.group(1)) if yr_match else now.year
                    try:
                        event_date = datetime(year, month, int(m.group(2))).date()
                    except ValueError:
                        pass
                    break

        # Try to extract time range: "od HH:MM do HH:MM" or "from HH:MM to HH:MM"
        time_match = re.search(
            r'(?:od|from)\s+(\d{1,2}:\d{2})\s+(?:do|to)\s+(\d{1,2}:\d{2})',
            label,
            re.IGNORECASE,
        )
        start_time = datetime(event_date.year, event_date.month, event_date.day, tzinfo=timezone.utc)
        end_time = start_time

        if time_match:
            try:
                sh, sm = map(int, time_match.group(1).split(":"))
                eh, em = map(int, time_match.group(2).split(":"))
                start_time = datetime(event_date.year, event_date.month, event_date.day, sh, sm, tzinfo=timezone.utc)
                end_time = datetime(event_date.year, event_date.month, event_date.day, eh, em, tzinfo=timezone.utc)
            except (ValueError, IndexError):
                pass

        # Detect organizer: "By Name" pattern
        organizer = ""
        org_match = re.search(r'\bBy\s+(.+?)(?:,|$)', label)
        if org_match:
            organizer = org_match.group(1).strip()

        # Detect status (Busy, Free, Tentative)
        status = ""
        for s in ("Busy", "Free", "Tentative", "Zajęty", "Wolny", "Wstępnie"):
            if s in label:
                status = s
                break

        is_recurring = "Recurring" in label or "Cykliczne" in label
        is_all_day = time_match is None

        return CalendarEvent(
            id=f"cal-{index}",
            title=title,
            start_time=start_time,
            end_time=end_time,
            organizer=organizer,
            is_all_day=is_all_day,
            is_recurring=is_recurring,
            is_teams_meeting=True,
            status=status,
        )

    async def _join_meeting(self, cmd: JoinMeeting) -> None:
        """Focus meeting in calendar, open context menu (Shift+F10), click Join."""
        page = self._page
        try:
            # Show browser window
            await self._engine.show_window()
            await page.wait_for_timeout(1000)

            # 1. Click Calendar sidebar button
            cal_btn = page.locator('button[data-tid="ef56c0de-36fc-4ef8-b417-3d82ba9d073c"]').first
            await cal_btn.click(timeout=5000)
            await page.wait_for_timeout(3000)

            # 2. Get calendar iframe
            frame = page.frame("embedded-page-container")
            if not frame:
                iframe_loc = page.locator('[data-tid="hwc-iframe"]').first
                iframe_el = await iframe_loc.element_handle(timeout=5000)
                if iframe_el:
                    frame = await iframe_el.content_frame()

            if not frame:
                log.warning("Calendar iframe not found for join")
                return

            # 3. Find the meeting by title
            await frame.wait_for_selector('[role="main"] div[role="button"]', timeout=10000)
            buttons = frame.locator('[role="main"] div[role="button"]')
            count = await buttons.count()
            target_btn = None
            for i in range(count):
                btn = buttons.nth(i)
                aria = await btn.get_attribute("aria-label") or ""
                if cmd.event_title in aria:
                    target_btn = btn
                    break

            if not target_btn:
                log.warning("Meeting not found in calendar: %s", cmd.event_title)
                return

            # 4. Focus meeting and open context menu with Shift+F10
            await target_btn.focus()
            await page.wait_for_timeout(500)
            await page.keyboard.press("Shift+F10")
            log.info("Opened context menu for: %s", cmd.event_title)
            await page.wait_for_timeout(1500)

            # 5. Click "Join Teams meeting" / "Dołącz do spotkania Teams" in context menu
            join_selectors = [
                '[role="menuitem"]:has-text("Join Teams meeting")',
                '[role="menuitem"]:has-text("Dołącz do spotkania Teams")',
                '[role="menuitem"]:has-text("Dołącz do spotkania")',
                '[role="menuitem"]:has-text("Join")',
                '[role="menuitem"]:has-text("Dołącz")',
            ]
            clicked_join = False
            for sel in join_selectors:
                btn = frame.locator(sel).first
                try:
                    await btn.click(timeout=3000)
                    log.info("Clicked context menu item: %s", sel)
                    clicked_join = True
                    break
                except Exception:
                    continue

            if not clicked_join:
                log.warning("Join menuitem not found in context menu")
                return

            # 6. Wait for pre-join screen
            await page.wait_for_timeout(5000)
            prejoin = page.locator('[data-tid="calling-prejoin-screen"]').first
            try:
                await prejoin.wait_for(state="visible", timeout=15000)
                log.info("Pre-join screen loaded")
            except Exception:
                log.info("Pre-join not found — may have joined directly")
                return

            # 6. Set mic/camera based on cmd.with_video
            try:
                mic_toggle = page.locator('input[role="switch"][data-tid="toggle-mute"]').first
                if await mic_toggle.count() > 0:
                    # Mute mic by default (toggle off if currently on)
                    is_checked = await mic_toggle.is_checked()
                    if is_checked:
                        await mic_toggle.click()
                        log.info("Muted microphone")
            except Exception:
                pass

            try:
                cam_toggle = page.locator('input[role="switch"][data-tid="toggle-video"]').first
                if await cam_toggle.count() > 0:
                    is_checked = await cam_toggle.is_checked()
                    if cmd.with_video and not is_checked:
                        await cam_toggle.click()
                        log.info("Enabled camera")
                    elif not cmd.with_video and is_checked:
                        await cam_toggle.click()
                        log.info("Disabled camera")
            except Exception:
                pass

            # 7. Click "Join now"
            join_now = page.locator('button[data-tid="prejoin-join-button"]').first
            await join_now.click(timeout=5000)
            log.info("Clicked Join now — entering meeting")

            # 8. Wait for meeting to load, then ensure window is visible and raised
            await page.wait_for_timeout(3000)
            await self._engine.show_window()

        except Exception as e:
            log.warning("Failed to join meeting '%s': %s", cmd.event_title, e)
