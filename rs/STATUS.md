# teams-tui (Rust) — status i plan

Przepisanie obecnego wrappera z Pythona na Rust. Architektura zostaje: Teams web działa
w ukrytym Chromium, a program w Rust steruje nim przez CDP (`chromiumoxide`)
i rysuje interfejs w stylu neomutta (`ratatui`). Profil przeglądarki jest wspólny
z wersją w Pythonie (`~/.local/share/teams-tui/chromium-profile`), więc logowanie zostaje.

Plan docelowy (przejście z wrappera na Microsoft Graph) jest w `../PLAN-GRAPH.md`.

## Status (2026-10-07)

| Element | Stan |
|---|---|
| Kod v1 (`src/`, ~1700 linii) | napisany, **jeszcze nie kompilowany** |
| `cargo build` / testy | do zrobienia |
| Uruchomienie na prawdziwym profilu Teams | do zrobienia |
| Przegląd kodu (GLM, `delegacja-glm`) + poprawki | do zrobienia |

### Co jest w v1

- Start: Chromium z profilem z Playwright (`~/.cache/ms-playwright/chromium-*`, albo `TEAMS_TUI_CHROME`),
  sprawdzenie logowania, okno widoczne tylko przy logowaniu.
- Lista czatów (scraping sidebaru Teams) z flagą nieprzeczytanych, filtr `/` bez polskich znaków.
- Otwieranie czatu: natychmiast z cache SQLite, potem świeże wiadomości ze strony.
- Na żywo: MutationObserver w stronie → binding CDP → ponowny scraping tylko tego, co się zmieniło.
- Wysyłanie: odpowiedź w linii (`i`) albo w `$EDITOR` (`E`) z potwierdzeniem.
- Powiadomienia D-Bus dla nowych wiadomości w innych czatach.
- `:show` / `:hide` / `:browser` — okno przeglądarki, `:chat <nazwa>`, `:refresh`, `:q`, pomoc `?`.

### Naprawione względem wersji w Pythonie

- Wysyłanie sprawdza otwarty czat i tekst w polu przed Enterem; brak ogólnego selektora `[role=textbox]`
  (mógł trafić w post kanału).
- Brak sztywnych `wait_for_timeout(3000)` — czekanie na konkretny stan co 100 ms.
- Właściwy przycisk „Czat” (`86fcd49b-…`; stary kod klikał „Zespoły”).
- Klucz wiadomości `(chat_id, id)` w cache — czaty się nie nadpisują; brak duplikatów w widoku.
- Brak automatycznej „.” przy tworzeniu czatu / dodawaniu osoby.
- Szerokość tekstu liczona w kolumnach (polskie znaki), brak markupu Rich (treść nie psuje widoku).
- Log i cache z uprawnieniami 0600, bez treści wiadomości w logu.

## Następne kroki

1. `cargo build`, poprawki kompilacji, `cargo test` (testy: html, store, text).
2. Uruchomienie na profilu (stara wersja w Pythonie musi być zamknięta — ten sam profil Chromium).
   Sprawdzić: logowanie, lista czatów, otwieranie, wysyłanie, observer, ukrywanie okna (Wayland/X11).
3. Przegląd kodu przez GLM (max 2 pliki na bieg) → poprawki.
4. Brakujące względem wersji w Pythonie: nowy czat i dodawanie osób (wyszukiwanie ludzi),
   kalendarz i dołączanie do spotkań, połączenia (otwarcie okna przeglądarki), przechwytywanie
   odpowiedzi sieciowych jako dodatkowe źródło wiadomości.
5. README ze zrzutem ekranu, instalacja (`cargo install --path rs`), potem wycofanie wersji w Pythonie.

## Uruchomienie

```bash
cd rs
cargo run --release
# logi: ~/.local/share/teams-tui/teams-tui-rs.log (RUST_LOG=debug dla szczegółów)
```
