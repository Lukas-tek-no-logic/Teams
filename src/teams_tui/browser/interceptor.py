"""APIInterceptor — passively intercept Teams API responses."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import unquote

from teams_tui.bridge.event_bus import Event
from teams_tui.data.models import Chat, ChatType, Message, User

if TYPE_CHECKING:
    from playwright.async_api import Page, Response

    from teams_tui.bridge.event_bus import EventBus

log = logging.getLogger(__name__)

# URL patterns for the new teams.cloud.microsoft API.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("messages", re.compile(r"/conversations/[^/]+/messages", re.I)),
    ("presence", re.compile(r"presence/getpresence", re.I)),
    ("users_fetch", re.compile(r"/users/fetch\b", re.I)),
]


class APIInterceptor:
    """Listens to all HTTP responses from Teams and extracts structured data.

    Uses page.on("response") — passive, never blocks or modifies traffic.
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus

    def attach(self, page: Page) -> None:
        page.on("response", self._on_response)
        log.info("API interceptor attached.")

    async def _on_response(self, response: Response) -> None:
        url = response.url

        if response.status < 200 or response.status >= 300:
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return

        for name, pattern in _PATTERNS:
            if pattern.search(url):
                try:
                    body = await response.json()
                except Exception:
                    return
                handler = getattr(self, f"_handle_{name}", None)
                if handler:
                    await handler(url, body)
                return

    # -- handlers ------------------------------------------------------------

    async def _handle_messages(self, url: str, body: dict) -> None:
        """Parse messages from chatsvc API."""
        raw_msgs = body.get("messages") or []
        messages: list[Message] = []
        for item in raw_msgs:
            msg = _parse_message(item)
            if msg:
                messages.append(msg)

        if messages:
            chat_id = _extract_chat_id(url)
            log.info(
                "Intercepted %d messages for chat …%s",
                len(messages), chat_id[-20:] if chat_id else "?",
            )
            await self._bus.emit(Event("messages_received", {
                "chat_id": chat_id,
                "messages": messages,
            }))

    async def _handle_presence(self, url: str, body: dict) -> None:
        await self._bus.emit(Event("presence_updated", body))

    async def _handle_users_fetch(self, url: str, body: dict) -> None:
        """Parse user profiles."""
        raw_users = body.get("value") or []
        users: list[User] = []
        for item in raw_users:
            if isinstance(item, dict) and item.get("displayName"):
                users.append(User(
                    id=item.get("objectId") or item.get("mri", ""),
                    display_name=item["displayName"],
                    email=item.get("email") or item.get("mail", ""),
                ))
        if users:
            log.info("Intercepted %d user profiles.", len(users))
            await self._bus.emit(Event("users_fetched", users))


# -- DOM scraper for chat list -----------------------------------------------


async def scrape_chat_list(page: Page) -> list[Chat]:
    """Extract chat list directly from the Teams DOM."""
    items = await page.evaluate("""
        () => {
            const results = [];

            const candidates = document.querySelectorAll('[role="treeitem"]');
            for (const el of candidates) {
                // Extract chat ID from data-fui-tree-item-value
                // Format: "OneGQL_GroupChatConversation|19:xxx@thread.skype"
                //      or "OneGQL_OneOnOneChatConversation|19:xxx@thread.v2"
                const fuiValue = el.dataset.fuiTreeItemValue || '';

                // Skip non-chat items (pure folders, copilot, slices, mentions)
                if (!fuiValue) continue;

                // fuiValue can be nested: "Folder|.../ChatConversation|19:xxx@thread.skype"
                // We want the last segment that contains "Conversation|..."
                // Skip items that are ONLY folders (no chat part)
                const segments = fuiValue.split('/');
                const chatSegment = segments[segments.length - 1];

                // Must contain "Conversation|" to be an actual chat
                if (!chatSegment.includes('Conversation|')) continue;
                // Skip folder-only, slice, metaos items
                if (chatSegment.includes('Folder|') || chatSegment.includes('Slice')
                    || chatSegment.includes('MetaOS') || chatSegment.includes('activities')) continue;

                const pipeIdx = chatSegment.indexOf('|');
                const chatId = pipeIdx >= 0 ? chatSegment.substring(pipeIdx + 1) : '';
                // Skip draft/preview chats (not yet created)
                if (chatId.startsWith('19:preview-')) continue;
                if (!chatId) continue;

                const prefix = pipeIdx >= 0 ? chatSegment.substring(0, pipeIdx) : '';
                let chatType = 'unknown';
                if (prefix.includes('OneOnOne')) chatType = 'oneOnOne';
                else if (prefix.includes('GroupChat')) chatType = 'group';
                else if (prefix.includes('Channel')) chatType = 'channel';
                else if (prefix.includes('Meeting')) chatType = 'meeting';

                let text = el.textContent?.trim() || '';
                if (!text || text.length > 500) continue;

                // Strip unread badge text (e.g. "Nieprzeczytane", "Unread")
                // that gets concatenated with the title by textContent
                text = text.replace(/^Nieprzeczytane\\s*/i, '');
                text = text.replace(/^Unread\\s*/i, '');
                text = text.replace(/Wersja robocza\\s*$/i, '');
                text = text.replace(/Draft\\s*$/i, '');

                // Parse the text: "Name HH:MM Preview text"
                const timeMatch = text.match(/^(.+?)(\\d{1,2}:\\d{2}|\\d{1,2}\\.\\d{2}|wczoraj|yesterday|pon\\.|wt\\.|śr\\.|czw\\.|pt\\.|sob\\.|niedz\\.)(.*)$/s);

                let title, preview, time;
                if (timeMatch) {
                    title = timeMatch[1].trim();
                    time = timeMatch[2].trim();
                    preview = timeMatch[3].trim();
                    preview = preview.replace(/^Ty:\\s*/, '');
                } else {
                    const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                    title = lines[0] || text;
                    preview = lines.slice(1).join(' ').substring(0, 120);
                    time = '';
                }

                if (!title) continue;

                results.push({
                    id: chatId,
                    title: title,
                    preview: preview.substring(0, 120),
                    time: time,
                    chatType: chatType,
                });
            }
            return results;
        }
    """)

    _TYPE_MAP = {
        "oneOnOne": ChatType.ONE_ON_ONE,
        "group": ChatType.GROUP,
        "channel": ChatType.CHANNEL,
        "meeting": ChatType.MEETING,
    }

    chats: list[Chat] = []
    seen = set()
    # Teams DOM is already ordered by most recent activity.
    # Assign synthetic timestamps so sorting preserves this order.
    base_ts = datetime.now(timezone.utc)
    for idx, item in enumerate(items):
        cid = item["id"]
        if cid in seen:
            continue
        seen.add(cid)
        chat_type = _TYPE_MAP.get(item.get("chatType", ""), ChatType.UNKNOWN)
        from datetime import timedelta
        chats.append(Chat(
            id=cid,
            chat_type=chat_type,
            title=item["title"],
            last_message_preview=item.get("preview", ""),
            last_message_time=base_ts - timedelta(seconds=idx),
        ))

    log.info("Scraped %d chats from DOM.", len(chats))
    return chats


# -- message parser (adapted to real chatsvc response structure) -------------


def _parse_message(raw: dict) -> Message | None:
    """Parse a message from the chatsvc API response."""
    msg_id = raw.get("id") or raw.get("clientmessageid")
    if not msg_id:
        return None

    msg_type = raw.get("messagetype") or raw.get("type") or "text"

    # Skip system/control messages.
    if msg_type in ("ThreadActivity/MemberJoined", "ThreadActivity/MemberLeft",
                    "ThreadActivity/TopicUpdate", "Event/Call",
                    "Control/Typing", "Control/ClearTyping"):
        return None

    # Content: strip HTML tags.
    content = raw.get("content") or ""
    if "<" in content:
        content = re.sub(r"<[^>]+>", "", content).strip()

    if not content:
        return None

    # Sender: in chatsvc format, "from" is a URL, name is in "imdisplayname"
    sender_name = raw.get("imdisplayname") or raw.get("fromDisplayNameInToken") or ""
    from_url = raw.get("from") or ""
    sender_id = ""
    if isinstance(from_url, str):
        # Extract user ID from URL like .../contacts/8:orgid:uuid
        m = re.search(r"orgid:([a-f0-9-]+)", from_url)
        if m:
            sender_id = m.group(1)

    # Timestamp
    raw_time = raw.get("composetime") or raw.get("originalarrivaltime")
    timestamp = _parse_time(raw_time) if raw_time else datetime.now(timezone.utc)

    # Conversation ID
    chat_id = raw.get("conversationid") or raw.get("conversationId") or ""

    return Message(
        id=str(msg_id),
        chat_id=chat_id,
        sender_id=sender_id,
        sender_name=sender_name,
        content=content,
        timestamp=timestamp,
        is_edited=bool(raw.get("edittime")),
        reply_to_id=raw.get("replyToId"),
        message_type=msg_type,
    )


def _extract_chat_id(url: str) -> str:
    """Pull conversation ID from the URL path."""
    m = re.search(r"/conversations/([^/?;]+)", url)
    if m:
        return unquote(m.group(1))
    return ""


def _parse_time(raw: str) -> datetime:
    """Parse ISO-8601 timestamp, tolerant of variations."""
    if not raw:
        return datetime.now(timezone.utc)
    raw = raw.rstrip("Z") + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _parse_unread(text: str) -> int:
    """Parse unread badge text to int."""
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 1  # badge present but no number = 1
