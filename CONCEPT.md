# Teams TUI - Terminal Client for Microsoft Teams

## Idea

Terminalowy klient Microsoft Teams (styl NeoMutt) z pełną funkcjonalnością - w tym audio/video/screenshare.

## Architektura

```
┌─────────────────────────────────────────┐
│  Terminal (TUI)                         │
│  - lista czatów/kanałów                 │
│  - pisanie wiadomości                   │
│  - nawigacja klawiaturowa               │
│  - notyfikacje                          │
│  - [Enter] na rozmowę → otwiera okno   │
└────────────┬────────────────────────────┘
             │ Playwright
             ▼
┌─────────────────────────────────────────┐
│  Ukryta przeglądarka (Teams web)        │
│  - pełna sesja Teams                    │
│  - audio/video/screenshare gdy trzeba    │
│  - okno pojawia się TYLKO na rozmowy    │
└─────────────────────────────────────────┘
```

## Kluczowe decyzje

### Dlaczego nie Graph API bezpośrednio?
- Brak możliwości rejestracji Azure AD app (admin restrictions)
- Jedyna droga auth to przez przeglądarkę/PWA
- Playwright jako proxy do autentykacji i odświeżania tokenów

### Dlaczego nie czysty terminal?
- Audio/video/screenshare wymagają WebRTC → potrzebują GUI przeglądarki
- Kamera, mikrofon, pipewire - niedostępne z terminala

### Podejście hybrydowe
- **Chat, kanały, notyfikacje** → TUI (scrape z DOM lub intercepted API calls)
- **Rozmowy audio/video** → Playwright pokazuje okno przeglądarki z Teams call UI
- **Screen share** → okno przeglądarki (WebRTC wymaga GUI)
- **Po zakończeniu rozmowy** → okno znika, powrót do TUI

## Rozważane opcje (historia decyzji)

### Opcja 1: Token extraction z przeglądarki
- Wyciągnięcie OAuth tokenów z IndexedDB/cookies przeglądarki
- Problem: tokeny wygasają co ~1h, refresh skomplikowany
- Odrzucona jako zbyt krucha

### Opcja 2: Playwright jako proxy do token extraction
- Playwright otwiera login → user się loguje → interceptuje tokeny
- Tokeny używane z Graph API bezpośrednio
- Przeglądarka w tle odnawia sesję
- Dobra dla samego chatu, nie obsługuje audio/video

### Opcja 3 (wybrana): Pełny hybrid - TUI + ukryta przeglądarka
- Playwright kontroluje pełną sesję Teams web
- TUI jako główny interfejs (nawigacja, chat)
- Przeglądarka pojawia się tylko na rozmowy
- 100% funkcjonalności Teams

## Stack technologiczny (propozycja)

- **Język**: Python
- **TUI framework**: textual (lub urwid)
- **Browser automation**: Playwright
- **Komunikacja TUI ↔ Playwright**: asyncio + event queue

## Schemat działania

1. **Pierwszy start** - Playwright otwiera Teams web, user loguje się normalnie
2. **Po zalogowaniu** - okno przeglądarki się chowa, uruchamia się TUI
3. **Playwright interceptuje** requesty API i scrapuje DOM → dane do TUI
4. **Chat/kanały** - obsługiwane w TUI (klawiatura, vim-like bindings)
5. **Przychodzący call** - Playwright wykrywa → pokazuje okno przeglądarki z call UI
6. **Koniec rozmowy** - okno znika → powrót do TUI
7. **Token refresh** - przeglądarka w tle automatycznie odświeża sesję

## TODO

- [ ] PoC: Playwright logowanie + token interception
- [ ] PoC: TUI z textual - lista czatów
- [ ] Integracja: TUI steruje Playwright
- [ ] Obsługa przychodzących rozmów (detekcja call UI w DOM)
- [ ] Notyfikacje (nowe wiadomości)
- [ ] Nawigacja klawiaturowa (vim-style)
