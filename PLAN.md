# Teams TUI — Pełny Plan Implementacji

## Kontekst

Budujemy terminalowego klienta Microsoft Teams (styl NeoMutt) z podejściem hybrydowym: TUI do chatu/kanałów + ukryta przeglądarka Playwright do auth i rozmów audio/video. Projekt jest greenfield — istnieje tylko CONCEPT.md.

---

## Struktura projektu

```
teams-tui/
├── pyproject.toml
├── CONCEPT.md
├── .gitignore
├── src/
│   └── teams_tui/
│       ├── __init__.py
│       ├── __main__.py              # Entry point: asyncio.run()
│       ├── app.py                   # Textual App — root, keymaps, workers
│       ├── config.py                # Ścieżki XDG, ustawienia
│       │
│       ├── browser/                 # Playwright
│       │   ├── __init__.py
│       │   ├── engine.py            # BrowserEngine: launch, show/hide (CDP)
│       │   ├── auth.py              # Login flow, session persistence
│       │   ├── interceptor.py       # Przechwytywanie API responses
│       │   ├── selectors.py         # Centralne selektory DOM
│       │   ├── dom_observer.py      # MutationObserver bridge
│       │   ├── call_manager.py      # Detekcja i zarządzanie rozmowami
│       │   └── command_executor.py  # Wysyłanie wiadomości via DOM
│       │
│       ├── data/                    # Modele i stan
│       │   ├── __init__.py
│       │   ├── models.py            # dataclasses: Chat, Message, User, CallInfo
│       │   ├── store.py             # In-memory reactive store
│       │   └── cache.py             # SQLite + FTS5 cache
│       │
│       ├── bridge/                  # Async komunikacja Playwright ↔ TUI
│       │   ├── __init__.py
│       │   ├── event_bus.py         # asyncio.Queue event bus (dwukierunkowy)
│       │   └── commands.py          # SendMessage, JoinCall, NavigateToChat
│       │
│       ├── tui/                     # Widgety i ekrany Textual
│       │   ├── __init__.py
│       │   ├── screens/
│       │   │   ├── login.py         # Ekran statusu logowania
│       │   │   ├── main.py          # Główny 3-panelowy ekran
│       │   │   └── call.py          # Overlay statusu rozmowy
│       │   ├── widgets/
│       │   │   ├── chat_list.py     # Lista czatów (lewy panel)
│       │   │   ├── message_view.py  # Historia wiadomości (środek)
│       │   │   ├── compose.py       # Pole wpisywania (dół)
│       │   │   ├── status_bar.py    # Pasek statusu: tryb, połączenie
│       │   │   └── search.py        # Overlay wyszukiwania
│       │   ├── keybindings.py       # Vim-mode keymaps (NORMAL/INSERT/COMMAND)
│       │   └── theme.py             # Kolory i style
│       │
│       └── notifications/
│           └── notifier.py          # D-Bus desktop notifications
│
├── tests/
│   ├── conftest.py
│   ├── test_interceptor.py
│   ├── test_store.py
│   └── test_event_bus.py
│
└── assets/
    └── teams_tui.tcss               # Textual CSS stylesheet
```

### Packaging: `pyproject.toml` (uv)

```toml
[project]
name = "teams-tui"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "textual>=0.82.0",
    "playwright>=1.45.0",
    "aiosqlite>=0.20.0",
    "platformdirs>=4.0.0",
    "dbus-next>=0.2.3",
]

[project.scripts]
teams-tui = "teams_tui.__main__:main"

[tool.uv]
dev-dependencies = ["pytest>=8.0", "pytest-asyncio>=0.24", "textual-dev>=1.0"]
```

---

## Faza 1: Fundament — Setup + Login przez Playwright

**Cel:** Uruchomić Playwright (headed), pozwolić userowi się zalogować, zapisać sesję, schować okno.

### Pliki do stworzenia
- `pyproject.toml`, `.gitignore`, `src/teams_tui/__init__.py`
- `src/teams_tui/__main__.py` — entry point `asyncio.run()`
- `src/teams_tui/config.py` — ścieżki: `~/.local/share/teams-tui/chromium-profile/`, `cache.db`
- `src/teams_tui/browser/engine.py` — `BrowserEngine`
- `src/teams_tui/browser/auth.py` — `AuthManager`

### Kluczowe decyzje techniczne

1. **`launch_persistent_context`** zamiast `storage_state.json` — zachowuje HttpOnly cookies, service workers, IndexedDB. Nie trzeba ręcznie eksportować/importować tokenów.
2. **Headed mode od startu** — Playwright nie może przełączyć headless↔headed w runtime. WebRTC wymaga headed. Okno chowamy przez CDP `Browser.setWindowBounds` (off-screen / minimize).
3. **Login detection** — `wait_for_selector('[data-tid="app-layout"]')` + fallback na URL pattern.
4. **Show/hide window** — CDP na X11. Na Wayland fallback na minimize/restore.

### Schemat startu
```
__main__.py → Config.load() → BrowserEngine.start() → AuthManager.ensure_authenticated()
  → Jeśli nie zalogowany: show_window() → user loguje się → hide_window()
  → Jeśli zalogowany (persistent context): kontynuuj
```

---

## Faza 2: Warstwa danych — Intercepting Teams API

**Cel:** Pasywnie przechwytywać HTTP responses Teams i budować store z czatami/wiadomościami.

### Pliki do stworzenia
- `src/teams_tui/browser/interceptor.py` — `APIInterceptor`
- `src/teams_tui/browser/selectors.py` — centralne selektory DOM i URL patterns
- `src/teams_tui/data/models.py` — `Chat`, `Message`, `User`, `CallInfo`, `ChatType`, `Presence`
- `src/teams_tui/data/store.py` — `DataStore` (reactive, observable)
- `src/teams_tui/data/cache.py` — `Cache` (SQLite + FTS5)

### Podejście
- **Pasywne nasłuchiwanie** `page.on("response", callback)` — NIE `page.route()`. Nie blokujemy ani nie modyfikujemy requestów.
- **URL patterns do interceptu:**
  - `*/users/ME/conversations*` — lista konwersacji
  - `*/conversations/*/messages*` — wiadomości
  - `*recentchats*` / `*recentConversations*` — ostatnie czaty
  - `*presence*` — status obecności
  - `*graph.microsoft.com*` — Graph API calls
- **Defensywny parsing** — `.get()` z fallbackami, ignorowanie nieznanych pól
- **Triggering data** — gdy user wybierze czat bez wiadomości, nawigujemy przeglądarkę do `teams.microsoft.com/v2/chat/{id}` → Teams fetchuje dane → interceptor je łapie

---

## Faza 3: TUI Core — Textual z panelami i vim-bindings

**Cel:** Trójpanelowy layout (czaty | wiadomości | compose) z nawigacją vim-style.

### Pliki do stworzenia
- `src/teams_tui/app.py` — `TeamsTUIApp(App)`: keymaps, workers, store subscription
- `src/teams_tui/tui/screens/main.py` — `MainScreen`: layout horizontal
- `src/teams_tui/tui/widgets/chat_list.py` — `ChatListWidget`: ListView, j/k/Enter
- `src/teams_tui/tui/widgets/message_view.py` — `MessageViewWidget`: virtual scroll
- `src/teams_tui/tui/widgets/compose.py` — `ComposeWidget`: TextArea, Enter=send
- `src/teams_tui/tui/widgets/status_bar.py` — tryb (NORMAL/INSERT), connection status
- `src/teams_tui/tui/keybindings.py` — NORMAL_KEYMAP, INSERT_KEYMAP, COMMAND_KEYMAP
- `assets/teams_tui.tcss` — layout CSS

### Vim-mode keybindings

| Tryb | Klawisze | Akcja |
|------|----------|-------|
| NORMAL | `j/k` | Nawigacja po liście czatów |
| NORMAL | `Enter/l` | Otwórz czat |
| NORMAL | `h/Esc` | Wróć do listy |
| NORMAL | `i` | INSERT mode (compose) |
| NORMAL | `/` | Szukaj |
| NORMAL | `:` | Tryb komend |
| NORMAL | `Ctrl+U/D` | Scroll wiadomości |
| NORMAL | `G` / `gg` | Koniec/początek |
| INSERT | `Enter` | Wyślij wiadomość |
| INSERT | `Shift+Enter` | Nowa linia |
| INSERT | `Esc` | Powrót do NORMAL |

### Layout CSS
```
ChatListWidget { width: 30; border-right: tall $accent; }
MessageViewWidget { height: 1fr; }
ComposeWidget { height: auto; max-height: 8; border-top: tall $accent; }
StatusBarWidget { dock: bottom; height: 1; }
```

---

## Faza 4: Real-time sync — Event Bus + DOM Observer

**Cel:** Dwukierunkowa komunikacja async między Playwright a Textual. Detekcja nowych wiadomości, typing indicators.

### Pliki do stworzenia
- `src/teams_tui/bridge/event_bus.py` — `EventBus`: dwa `asyncio.Queue` (events + commands)
- `src/teams_tui/bridge/commands.py` — `SendMessageCommand`, `NavigateToChatCommand`, `JoinCallCommand`
- `src/teams_tui/browser/dom_observer.py` — `DOMObserver`: MutationObserver via `expose_function`
- `src/teams_tui/browser/command_executor.py` — `CommandExecutor`: typing into CKEditor compose box

### Architektura
```
Playwright (interceptor, dom_observer) ──events──> asyncio.Queue ──> Textual (worker reads)
Textual (user actions) ──commands──> asyncio.Queue ──> Playwright (command_executor)
```

**Kluczowy insight:** Playwright async API i Textual dzielą ten sam asyncio event loop → brak problemów z thread safety. Tylko `asyncio.Queue` do decouplingu.

### DOMObserver — detekcja typing indicators i incoming calls
- Inject `MutationObserver` via `page.evaluate()`
- `expose_function("onTeamsDomChange", callback)` do bridge JS→Python
- Obserwowane selektory: `[data-tid="typing-indicator"]`, `[data-tid="call-toast"]`, `[data-tid="unread-badge"]`

### Wysyłanie wiadomości
- Nawigacja do czatu via URL jeśli trzeba
- `page.locator('[data-tid="ckeditor"]').fill(content)` + `Enter`
- Fallback na `page.keyboard.type()` jeśli `fill()` nie działa z CKEditor

---

## Faza 5: Obsługa rozmów

**Cel:** Detekcja incoming/outgoing calls, show/hide okna przeglądarki, status call w TUI.

### Pliki do stworzenia
- `src/teams_tui/browser/call_manager.py` — `CallManager`: polling + event-driven detekcja
- `src/teams_tui/tui/screens/call.py` — `CallScreen`: overlay z timerem i statusem

### Strategia detekcji
1. **DOM Observer** (primary) — `[data-tid="call-toast"]`, `[data-tid="incoming-call-notification"]`
2. **Polling** (safety net) — co 2s sprawdzaj `[data-tid="calling-screen"]`, `[data-tid="hangup-button"]`
3. **Call started** → `engine.show_window()` → TUI pokazuje CallScreen overlay
4. **Call ended** → `engine.hide_window()` → CallScreen dismiss

### Inicjowanie rozmowy z TUI
- `Ctrl+C` (voice) / `Ctrl+V` (video) w NORMAL mode
- CommandExecutor nawiguje do czatu → klika `[data-tid="chat-header-audio-call-button"]`
- Show browser window

---

## Faza 6: Notyfikacje, search, polish

**Cel:** Desktop notifications, wyszukiwanie, unread counts, presence indicators.

### Pliki do stworzenia
- `src/teams_tui/notifications/notifier.py` — D-Bus notifications (`dbus-next`)
- `src/teams_tui/tui/widgets/search.py` — `SearchOverlay`: FTS5 local + browser search
- Aktualizacja `cache.py` — FTS5 virtual table na messages

### Notyfikacje
- D-Bus `org.freedesktop.Notifications` — async via `dbus-next`
- Tylko dla czatów nie w focusie, respektuje mute
- Graceful degradation jeśli brak notification daemon

### Search
- `/` otwiera SearchOverlay
- Instant: szukanie po nazwach czatów (local store)
- Fast: FTS5 query na cached messages (SQLite)
- Slow: trigger browser search dla pełnych wyników

### Unread counts i presence
- Z API interception (recent chats response zawiera unread count)
- Rendering w ChatListWidget: `* John Doe (busy)  3` (gwiazdka=unread, liczba=count)

---

## Kolejność wiring w `__main__.py`

```
1. Config.load()
2. Cache.initialize() + DataStore()
3. EventBus()
4. BrowserEngine.start()
5. AuthManager.ensure_authenticated()
6. engine.hide_window()
7. APIInterceptor.attach(page)
8. DOMObserver.install()
9. CallManager.start_monitoring()
10. Notifier.connect() (try/except — non-fatal)
11. TeamsTUIApp.run_async() — blokuje do wyjścia
12. Cleanup: engine.stop(), cache.close()
```

---

## Ryzyka i mitigacje

| Ryzyko | Mitigacja |
|--------|-----------|
| Selektory DOM się zmieniają | Centralny `selectors.py`, logowanie niezidentyfikowanych wzorców |
| Format API responses się zmienia | Defensywny parsing `.get()`, logowanie nieznanych kształtów |
| Sesja wygasa / trzeba re-auth | Detekcja 401 w interceptorze → show browser → re-login |
| Wayland — brak pozycjonowania okien | Detect `$XDG_SESSION_TYPE`, fallback na minimize/restore |
| CKEditor — `fill()` nie działa | Fallback na `keyboard.type()` + `keyboard.press("Enter")` |
| Performance z tysiącami wiadomości | Virtual scrolling, lazy-load starszych wiadomości |
| FTS5 niedostępne | Fallback na LIKE queries |

---

## Weryfikacja

1. **Faza 1:** `uv run teams-tui` → otwiera się przeglądarka → login → okno znika → "Logged in" w terminalu
2. **Faza 2:** Po zalogowaniu, logi pokazują przechwycone czaty i wiadomości z API
3. **Faza 3:** TUI się uruchamia, lista czatów widoczna, nawigacja j/k działa, Enter otwiera czat
4. **Faza 4:** Nowa wiadomość w Teams → pojawia się w TUI w <2s. Wysłana z TUI → widoczna w Teams web
5. **Faza 5:** Incoming call → okno przeglądarki się pojawia → po zakończeniu znika → TUI wraca
6. **Faza 6:** Notification popup na nową wiadomość, `/` otwiera search, unread counts się aktualizują
