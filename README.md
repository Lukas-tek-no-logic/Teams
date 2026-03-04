# Teams TUI

Terminal client for Microsoft Teams inspired by NeoMutt. Navigate chats, read and send messages, browse your calendar, and join meetings — all from the terminal with vim-style keybindings.

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

## How it works

Teams TUI is a **hybrid application**: a Textual-based TUI runs in your terminal while a hidden Playwright-controlled Chromium browser maintains a real Teams session in the background. The browser handles authentication, API interception, and WebRTC calls. The TUI provides a fast, keyboard-driven interface for everyday messaging.

```
┌──────────────────────────────────────────────────┐
│  Terminal (TUI)              Textual Framework    │
│  ┌────────────┬─────────────────────────────┐    │
│  │ Chat List  │  Messages                   │    │
│  │            │                             │    │
│  │  Alice (2) │  Bob          14:32         │    │
│  │  Bob       │  Hey, check the PR          │    │
│  │  #general  │                             │    │
│  │            │  Ty            14:33         │    │
│  │            │  On it!                     │    │
│  │            ├─────────────────────────────│    │
│  │            │  > compose...               │    │
│  └────────────┴─────────────────────────────┘    │
│  [NORMAL] | Bob [51 czatów] | j/k:nav  i:compose │
└──────────────────┬───────────────────────────────┘
                   │ asyncio.Queue bridge
                   ▼
┌──────────────────────────────────────────────────┐
│  Hidden Chromium Browser     Playwright           │
│  - Teams web session with full auth              │
│  - API interception for real-time data           │
│  - DOM scraping for chats, messages, calendar    │
│  - WebRTC calls (window shown when needed)       │
└──────────────────────────────────────────────────┘
```

## Features

### Messaging
- **Chat navigation** — browse all chats with unread counts and message previews
- **Read & send messages** — view conversation history, compose and send
- **New chats** — create 1:1 or group conversations via people search
- **Add participants** — add people to existing chats
- **Real-time updates** — new messages appear instantly via API interception
- **Search** — fuzzy, diacritic-insensitive chat search (e.g. "dudzinski" matches "Dudziński")
- **Desktop notifications** — D-Bus notifications for messages in non-active chats
- **Offline cache** — SQLite with FTS5 full-text search for message history

### Calendar
- **Calendar view** — agenda of upcoming meetings scraped from Teams calendar
- **Meeting details** — title, date, time, organizer, recurring indicator
- **Join meetings** — press Enter to join directly (opens pre-join screen, then browser)
- **Active meeting highlight** — currently running meetings shown in green

### Calls
- **Audio/video calls** — initiate from any chat
- **Incoming call detection** — notification + call overlay screen
- **Call management** — browser window shown automatically during calls

### Interface
- **Vim keybindings** — `j/k` navigation, `i` insert mode, `/` search, `Escape` normal mode
- **3-pane layout** — chat list | messages | compose (inspired by NeoMutt)
- **Mode system** — NORMAL, INSERT, CALENDAR modes with context-appropriate key hints
- **Status bar** — current mode, active chat, chat count, typing indicators

## Requirements

- **Python** 3.11+
- **uv** (recommended) or pip
- **Linux** with X11 or Wayland
- A Microsoft Teams account (work/school)

## Installation

```bash
# Clone the repository
git clone https://github.com/lokarmus/Teams.git
cd Teams

# Install with uv (recommended)
uv sync

# Install Playwright browsers
uv run playwright install chromium

# Run
uv run teams-tui
```

### Alternative: pip

```bash
pip install -e .
playwright install chromium
teams-tui
```

## First run

On first launch, the browser window will appear with the Microsoft Teams login page. Log in normally — Teams TUI will save the session and reuse it for subsequent launches. After login, the browser hides and the TUI takes over.

Session data is stored in `~/.local/share/teams-tui/chromium-profile/`.

## Keybindings

### Normal mode

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate up/down in chat list |
| `Enter` / `l` | Open selected chat |
| `i` | Compose mode (focus message input) |
| `h` | Focus chat list |
| `/` | Search chats |
| `n` | New chat |
| `a` | Add participant to current chat |
| `Ctrl+A` | Audio call |
| `Ctrl+V` | Video call |
| `Ctrl+Tab` | Toggle calendar view |
| `Escape` | Return to normal mode |
| `q` | Quit |

### Insert mode (compose)

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Escape` | Back to normal mode |

### Calendar mode

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate meetings |
| `Enter` | Join selected meeting |
| `Ctrl+Tab` | Back to chat view |
| `Escape` | Back to chat view |
| `q` | Quit |

### Message view

| Key | Action |
|-----|--------|
| `Ctrl+U` | Page up |
| `Ctrl+D` | Page down |
| `G` | Jump to bottom |

## Architecture

### Project structure

```
src/teams_tui/
├── app.py                    # Root Textual application
├── config.py                 # XDG paths & display server detection
├── teams_tui.tcss            # Textual CSS stylesheet
├── browser/                  # Playwright automation layer
│   ├── engine.py             # Browser lifecycle, window show/hide (CDP)
│   ├── auth.py               # Login flow & session persistence
│   ├── interceptor.py        # API response interception + DOM scraping
│   ├── dom_observer.py       # MutationObserver for typing, calls, unread
│   ├── call_manager.py       # Call detection & state management
│   └── command_executor.py   # Execute TUI commands in browser
├── data/                     # Domain models & state
│   ├── models.py             # Chat, Message, User, CalendarEvent, CallInfo
│   ├── store.py              # In-memory reactive DataStore
│   └── cache.py              # SQLite + FTS5 persistent cache
├── bridge/                   # Async communication layer
│   ├── event_bus.py          # asyncio.Queue bidirectional bus
│   └── commands.py           # Typed command objects (SendMessage, JoinCall, etc.)
├── tui/                      # Terminal UI
│   ├── keybindings.py        # Mode enum (NORMAL, INSERT, CALENDAR)
│   ├── screens/
│   │   ├── main.py           # 3-pane main screen
│   │   └── call.py           # Call overlay screen
│   └── widgets/
│       ├── chat_list.py      # Left panel — scrollable chat list
│       ├── message_view.py   # Center panel — message history
│       ├── compose.py        # Bottom — message input
│       ├── calendar_view.py  # Calendar agenda view
│       ├── status_bar.py     # Bottom status bar
│       ├── search.py         # Modal search overlay
│       └── person_picker.py  # Modal people search
└── notifications/
    └── notifier.py           # D-Bus desktop notifications
```

### Data flow

```
Browser (Playwright)
  │
  ├─ API Interception ──→ EventBus ──→ DataStore ──→ TUI Widgets
  │  (page.on("response"))    │          (reactive)    (subscribe)
  │                           │
  ├─ DOM Observer ───────────→│
  │  (MutationObserver)       │
  │                           │
  └─ Command Executor ←──────┘
     (SendMessage, JoinCall,   ← TUI Actions
      RefreshCalendar, etc.)
```

**TUI → Browser**: User actions create typed `Command` objects sent via `EventBus`. The `CommandExecutor` reads commands and drives Playwright to click, type, and navigate in Teams.

**Browser → TUI**: The `APIInterceptor` listens to network responses. The `DOMObserver` watches for DOM mutations (typing indicators, call popups, unread badges). Both emit `Event` objects to the `EventBus`, which the TUI consumes to update the `DataStore` and refresh widgets.

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| `launch_persistent_context` | Preserves HttpOnly cookies, ServiceWorker, IndexedDB across sessions |
| Headed mode (not headless) | WebRTC requires a GUI context; window is hidden via CDP |
| Passive `page.on("response")` | Non-invasive API interception; doesn't break Teams functionality |
| Same asyncio loop | Textual and Playwright share one event loop — no thread sync needed |
| `asyncio.Queue` bridge | Decouples TUI from browser; either side can produce/consume independently |

## Configuration

All data is stored under XDG directories:

| Path | Purpose |
|------|---------|
| `~/.local/share/teams-tui/chromium-profile/` | Browser session (cookies, cache) |
| `~/.local/share/teams-tui/cache.db` | SQLite message cache with FTS5 |
| `~/.local/share/teams-tui/teams-tui.log` | Application log |

## Troubleshooting

### Browser window doesn't appear on login
Make sure Playwright Chromium is installed: `uv run playwright install chromium`

### "No chats scraped from DOM" warning
Teams might be slow to load. The app waits up to 10 seconds for the chat list to render. Try restarting.

### Calendar shows wrong dates
The calendar parser supports both Polish and English locale date formats. If your Teams uses a different language, dates may fall back to today's date.

### Can't join a meeting
Meeting join requires the browser window to be visible (Teams needs a rendered window for clicks in the calendar iframe). The window will appear automatically.

### Notifications not working
D-Bus notifications require a notification daemon (e.g. dunst, mako). If D-Bus connection fails, the app continues without notifications.

## Tech stack

- [Textual](https://textual.textualize.io/) — TUI framework
- [Playwright](https://playwright.dev/python/) — Browser automation
- [aiosqlite](https://aiosqlite.omnilib.dev/) — Async SQLite
- [dbus-next](https://github.com/altdesktop/python-dbus-next) — D-Bus notifications
- [platformdirs](https://github.com/platformdirs/platformdirs) — XDG paths

## License

MIT
