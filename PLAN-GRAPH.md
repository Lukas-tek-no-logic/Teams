# Teams TUI — plan migracji na Microsoft Graph

Cel: czat, kanały, obecność i kalendarz działają natywnie przez oficjalny Microsoft Graph
(własna rejestracja aplikacji Entra ID), bez ukrytego Chromium. Rozmowy A/V i screen share
są przekazywane do teams-for-linux / przeglądarki przez link dołączenia.

Źródło decyzji: `~/Projects/claude/reports/Natywny klient Teams bez przeglądarki.md`.
Obecny wrapper (PLAN.md) zostaje jako backend `browser` do czasu zakończenia etapu 3.

## Decyzje

| Temat | Decyzja |
|---|---|
| Backend danych | Microsoft Graph v1.0, uprawnienia delegowane, własna aplikacja single-tenant |
| Logowanie | MSAL Python, auth code + PKCE z loopbackiem `http://localhost` (nie device code — nie spełnia CA device compliance) |
| Tokeny | `msal-extensions`, cache w keyringu (libsecret); deklaracja `cp1` (CAE), obsługa wyzwania claims przy 401 |
| HTTP | `httpx.AsyncClient`, respektowanie `Retry-After`, backoff na 429/503 |
| Real-time | etap przejściowy: adaptacyjny polling; docelowo subskrypcja `getAllMessages` przez Azure Event Hubs |
| Rozmowy | `xdg-open` linku `joinUrl` / deep linku `msteams:` lub teams-for-linux; brak własnego stosu A/V |
| ACS | odrzucone (koszt, Azure, wygaszanie 30.09.2028, mniej funkcji niż Teams) |
| Wewnętrzne API Teams (Trouter, chatsvc) | nie w tym planie (AUP Microsoft) — osobna analiza |
| Język | Python + Textual (istniejący TUI); Rust ewentualnie po etapie 5 |
| Funkcje bez odpowiednika w Graph | usunąć z UI (typing, read receipts innych, edycja treści, dzwonek), nie emulować |

## Etap 0 — Zgody i rejestracja (bez kodu)

Rejestracja aplikacji:
- Nazwa: „Terminal Teams client – Łukasz Okarmus”, single-tenant.
- Platforma „Mobile and desktop applications”, redirect `http://localhost`, „Allow public client flows” = off (PKCE wystarcza).
- Uprawnienia delegowane:

| Zakres | Po co | Admin consent |
|---|---|---|
| `User.Read`, `offline_access` | profil, refresh token | nie |
| `Chat.ReadWrite` | lista czatów, wiadomości, reakcje, stan odczytu, usuwanie | nie |
| `ChatMessage.Send` | wysyłanie w czatach | nie |
| `Team.ReadBasic.All`, `Channel.ReadBasic.All` | zespoły i kanały | nie |
| `ChannelMessage.Read.All` | odczyt postów w kanałach | **tak** |
| `ChannelMessage.Send` | pisanie w kanałach | nie |
| `User.ReadBasic.All`, `People.Read` | nazwy, wyszukiwanie osób | nie |
| `Presence.Read.All`, `Presence.ReadWrite` | obecność | nie |
| `Calendars.Read`, `OnlineMeetings.Read` | kalendarz, linki do spotkań | nie |
| `Files.ReadWrite` | załączniki (OneDrive) — dopiero w etapie 2 | nie |

Pytania do IT (odpowiedzi przed etapem 1):
1. Polityka zgody użytkowników (legacy / low-impact / wyłączona)? Czy potrzebny admin consent workflow?
2. Admin consent dla `ChannelMessage.Read.All`.
3. Czy CA wymaga zgodnego urządzenia dla Graph / „All resources”? Moja dystrybucja vs Intune (wspiera tylko Ubuntu).
4. Token protection dla Teams/Exchange/SharePoint — włączone/planowane? Jak traktuje Linuksa? Test w report-only.
5. CAE (tryb ścisły?), polityki lokalizacji IP, sign-in frequency.
6. Subskrypcja Azure + Event Hub + RBAC „Azure Event Hubs Data Sender” dla „Microsoft Graph Change Tracking”.
7. Akceptacja pollingu 15–30 s jako etapu przejściowego.
8. Lokalny zaszyfrowany cache wiadomości — dopuszczalny? Retencja?
9. teams-for-linux dopuszczony, czy tylko Teams w przeglądarce?

Kryterium wyjścia: logowanie PKCE działa na moim laptopie, w sign-in logs brak blokad CA.

## Etap 1 — Auth i odczyt (read-only)

Nowe pliki:
```
src/teams_tui/graph/
├── __init__.py
├── auth.py       # MSAL PublicClientApplication, PKCE+loopback, keyring cache, cp1, claims challenge
├── client.py     # httpx wrapper: base URL, auth header, paging (@odata.nextLink), Retry-After, błędy
├── mappers.py    # JSON Graph → data.models (Chat, Message, User, CalendarEvent)
└── backend.py    # GraphBackend: emituje te same Event-y na EventBus co interceptor
```

Zmiany:
- `config.py`: `backend: Literal["browser", "graph"]`, `client_id`, `tenant_id` (z `~/.config/teams-tui/config.toml`).
- `__main__.py`: rozgałęzienie startu — dla `graph` bez Playwright, `AuthManager` → `graph.auth`.
- CLI: `teams-tui --backend graph`, `teams-tui login`, `teams-tui logout`.

Endpointy:
- `GET /me` — własne id (do rozpoznawania „Ty”).
- `GET /me/chats?$expand=lastMessagePreview&$top=50` — lista czatów; tytuł 1:1 z `members` gdy `topic` puste.
- `GET /chats/{id}/messages?$top=50` — historia (najnowsze pierwsze, odwrócić).
- `GET /me/calendarView?startDateTime=…&endDateTime=…` — spotkania z `onlineMeeting.joinUrl`.
- Unread: `chat.viewpoint.lastMessageReadDateTime` < `lastMessagePreview.createdDateTime`.

Mapowanie treści: `body.contentType == "html"` → HTML→tekst (`html.unescape`, `<br>`/`<p>` → nowe linie,
mentions `<at>` → `@Imię`, załączniki → `[plik: nazwa]`).

Testy (`tests/`): fixture'y JSON z dokumentacji Graph → mappers; mock httpx dla paging i 429.

Kryterium wyjścia: `teams-tui --backend graph` pokazuje czaty, wiadomości i kalendarz bez Chromium.
Do usunięcia z backendu graph: parser dat PL/EN z `command_executor.py`.

## Etap 2 — Zapis

Nowy plik `graph/commands.py` — odpowiednik `command_executor.py`, ten sam zestaw `Command`:

| Komenda | Graph |
|---|---|
| `SendMessage` | `POST /chats/{id}/messages` (`body.contentType=html`, escape treści) |
| `CreateNewChat` | `POST /chats` (`oneOnOne` / `group` z `members`), potem wysyłka pierwszej wiadomości przez użytkownika — bez automatycznej „.” |
| `AddParticipant` | `POST /chats/{id}/members` (grupa); dla 1:1 → nowy czat grupowy |
| `SearchPeople` | `GET /me/people?$search="…"` + fallback `GET /users?$search="displayName:…"` (nagłówek `ConsistencyLevel: eventual`) |
| `NavigateToChat` | `GET /chats/{id}/messages` + `POST /chats/{id}/markChatReadForUser` |
| nowe: `React` | `POST /chats/{id}/messages/{mid}/setReaction` |
| nowe: `DeleteMessage` | `POST …/softDelete` |
| nowe: `Reply` | wiadomość z `attachments` typu `messageReference` |

Zmiany w TUI:
- `SearchPeople` zwraca listę `User` (id + nazwa), nie same stringi — wybór po id, nie po nazwie.
- Escape markup Rich we wszystkich widgetach (`rich.markup.escape`).
- Usunąć skróty i elementy bez odpowiednika (typing w status barze).
- Limit wysyłki: 1 msg/s na użytkownika — kolejka z throttlingiem w `client.py`.

Kryterium wyjścia: codzienna praca na czatach bez przeglądarki.

## Etap 3 — Czas rzeczywisty

`graph/realtime.py` z dwiema strategiami za wspólnym interfejsem:

**A. Adaptacyjny polling (start):**
- Lista czatów co 20 s; po 5 min bezczynności użytkownika → 60 s; po 30 min → 5 min.
- Otwarty czat co 5 s (tylko gdy TUI ma fokus).
- Porównanie `lastMessagePreview.id` — pobieranie wiadomości tylko dla czatów ze zmianą.
- Respektowanie `Retry-After`; licznik 429 w logu.

**B. Change notifications przez Event Hubs (docelowo):**
- Subskrypcja `POST /subscriptions` na `/users/{id}/chats/getAllMessages`, `changeType=created,updated,deleted`,
  `notificationUrl=EventHub:https://<ns>.servicebus.windows.net/eventhubname/<hub>?tenantId=<tid>`, bez resource data.
- Odbiór: `azure-eventhub` (async consumer, wychodzące AMQP/WebSocket, bez publicznego endpointu).
- Odnawianie przed upływem 3 dni; po starcie / przerwie sieci → pełna resynchronizacja przez polling.
- Obecność: subskrypcja `/communications/presences?$filter=id in (…)` (≤650 osób, odnawianie co godzinę).
- Do sprawdzenia w prototypie: czy lifecycle notifications mogą trafiać do Event Huba.

Powiadomienia D-Bus: tylko dla wiadomości nowszych niż start aplikacji i nie od siebie.

Kryterium wyjścia: opóźnienie < 10 s (B) lub < 20 s (A), brak 429 w normalnej pracy.

## Etap 4 — Rozmowy

- Spotkanie z kalendarza: `Enter` → `xdg-open <joinUrl>` (albo `teams-for-linux <joinUrl>` gdy skonfigurowane).
- Rozmowa z czatu 1:1: deep link `https://teams.microsoft.com/l/call/0/0?users=<upn>&withVideo=true|false`.
- Spotkanie czatu typu `meeting`: `onlineMeetingInfo.joinWebUrl`.
- `config.toml`: `call_handler = "xdg-open" | "teams-for-linux" | "<komenda>"`.
- Usunąć `browser/call_manager.py`, `tui/screens/call.py` → krótka notyfikacja „Otwarto rozmowę w …”.
- Połączenia przychodzące: obsługuje teams-for-linux / karta Teams w tle (Graph nie daje tego klientowi użytkownika).
- Screen share / wideo: w całości po stronie teams-for-linux / przeglądarki (portal PipeWire na Wayland).

Kryterium wyjścia: dołączenie do spotkania jednym klawiszem.

## Etap 5 — Sprzątanie

- Usunąć `browser/`, zależność `playwright`, profil `~/.local/share/teams-tui/chromium-profile/`.
- `data/cache.py`: klucz `(chat_id, id)`, szyfrowanie (SQLCipher lub szyfrowanie pól kluczem z keyringu),
  trigger update dla FTS5, `teams-tui cache clear`, retencja wg odpowiedzi IT.
- Podpiąć wyszukiwanie w treści wiadomości (cache FTS5) do `/`.
- Log: bez treści wiadomości na INFO, plik logu 0600.
- README: nowa architektura, rejestracja aplikacji, konfiguracja.
- Zależności docelowe: `textual`, `msal`, `msal-extensions`, `httpx`, `aiosqlite`, `dbus-next`, `platformdirs`, (opc.) `azure-eventhub`.

## Etap 6 — Opcje (decyzja po kilku tygodniach używania)

- Przepisanie na Rust (ratatui + reqwest + oauth2/MSAL-like PKCE + keyring), wzór: rvben/teams-cli.
- Kanały: wątki, odpowiedzi w wątku, filtrowanie ulubionych.
- Załączniki: upload do OneDrive (`/me/drive/root:/Microsoft Teams Chat Files/<plik>:/content`) + wiadomość z referencją.

## Ryzyka

| Ryzyko | Skutek | Mitygacja |
|---|---|---|
| Token protection bez wsparcia Linuksa | aplikacja się nie zaloguje | etap 0: test report-only z IT |
| CA device compliance | blokada; device code nie działa | PKCE + przeglądarka; Ubuntu w Intune |
| Brak Event Hubs | opóźnienia 20–60 s | polling adaptacyjny, zgoda IT |
| Wytyczna „poll once per day” | throttling, uwagi IT | niski interwał tylko dla otwartego czatu, backoff |
| Brak edycji / typing / read receipts | gorsze UX niż wrapper | jawnie usunięte z UI |
| Lokalny cache firmowych danych | zgodność | szyfrowanie, retencja, czyszczenie |
