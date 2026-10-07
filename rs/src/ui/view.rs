//! Rendering, mutt style: no boxes, full-width rows, terminal palette colours,
//! a reversed status line and a one-line prompt at the bottom.

use chrono::{Datelike, Local};
use ratatui::Frame;
use ratatui::layout::{Constraint, Layout, Position, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Clear, Paragraph};
use unicode_width::UnicodeWidthStr;

use super::app::{App, Input, View};
use super::text::{fit, one_line, truncate, wrap};
use crate::model::{ChatKind, SendState};

const SENDER_W: usize = 14;
const NAME_COLORS: [Color; 6] = [Color::Cyan, Color::Green, Color::Yellow, Color::Magenta, Color::Blue, Color::Red];

pub fn draw(f: &mut Frame, app: &mut App) {
    let [top, body, status, prompt] = Layout::vertical([
        Constraint::Length(1),
        Constraint::Min(1),
        Constraint::Length(1),
        Constraint::Length(1),
    ])
    .areas(f.area());
    app.body_height = body.height as usize;

    draw_help_line(f, app, top);
    match app.view {
        View::Index => draw_index(f, app, body),
        View::Pager => draw_pager(f, app, body),
    }
    draw_status(f, app, status);
    draw_prompt(f, app, prompt);
    if app.show_help {
        draw_help(f, body);
    }
}

fn dim() -> Style {
    Style::default().add_modifier(Modifier::DIM)
}

fn draw_help_line(f: &mut Frame, app: &App, area: Rect) {
    let keys = match app.view {
        View::Index => "q:Wyjdź  Enter:Otwórz  /:Filtr  Tab:Nieprzeczytane  R:Odśwież  :Komenda  ?:Pomoc",
        View::Pager => "q:Lista  i:Odpowiedz  E:Edytor  j/k:Przewiń  Tab:Nieprzeczytane  ?:Pomoc",
    };
    f.render_widget(Paragraph::new(truncate(keys, area.width as usize)).style(dim()), area);
}

fn kind_flag(kind: ChatKind) -> char {
    match kind {
        ChatKind::Group => '+',
        ChatKind::Meeting => 'M',
        ChatKind::Channel => '#',
        _ => ' ',
    }
}

fn draw_index(f: &mut Frame, app: &mut App, area: Rect) {
    let h = area.height as usize;
    let w = area.width as usize;
    if app.sel < app.index_top {
        app.index_top = app.sel;
    } else if app.sel >= app.index_top + h {
        app.index_top = app.sel + 1 - h;
    }
    if app.visible.is_empty() {
        let msg = if app.chats.is_empty() { "  (czekam na listę czatów z Teams…)" } else { "  (brak czatów pasujących do filtra)" };
        f.render_widget(Paragraph::new(msg).style(dim()), area);
        return;
    }
    let title_w = (w / 3).clamp(16, 36);
    let lines: Vec<Line> = app
        .visible
        .iter()
        .enumerate()
        .skip(app.index_top)
        .take(h)
        .map(|(pos, &i)| {
            let c = &app.chats[i];
            let flags = format!("{}{}", if c.unread { 'N' } else { ' ' }, kind_flag(c.kind));
            let current = app.chat_id.as_deref() == Some(c.id.as_str());
            let head = format!("{:>4} {flags} {:>8}  {}  ", pos + 1, truncate(&c.time_label, 8), fit(&c.title, title_w));
            let rest = w.saturating_sub(head.width());
            let mut style = Style::default();
            if c.unread {
                style = style.add_modifier(Modifier::BOLD);
            }
            if current {
                style = style.fg(Color::Cyan);
            }
            let mut line = Line::from(vec![
                Span::styled(head, style),
                Span::styled(fit(&one_line(&c.preview), rest), if c.unread { style } else { dim() }),
            ]);
            if pos == app.sel {
                line = line.style(Style::default().add_modifier(Modifier::REVERSED));
            }
            line
        })
        .collect();
    f.render_widget(Paragraph::new(lines), area);
}

fn name_color(name: &str) -> Color {
    let h = name.bytes().fold(0u32, |a, b| a.wrapping_mul(31).wrapping_add(b as u32));
    NAME_COLORS[(h % NAME_COLORS.len() as u32) as usize]
}

fn weekday_pl(d: chrono::Weekday) -> &'static str {
    use chrono::Weekday::*;
    match d {
        Mon => "poniedziałek",
        Tue => "wtorek",
        Wed => "środa",
        Thu => "czwartek",
        Fri => "piątek",
        Sat => "sobota",
        Sun => "niedziela",
    }
}

fn pager_lines(app: &App, width: usize) -> Vec<Line<'static>> {
    let mut out = Vec::new();
    let mut last_day = None;
    let mut last_sender: Option<(bool, String)> = None;
    let text_w = width.saturating_sub(6 + SENDER_W + 1).max(10);
    let indent = " ".repeat(6 + SENDER_W + 1);
    for m in &app.messages {
        let t = m.time.with_timezone(&Local);
        let day = t.date_naive();
        if last_day != Some(day) {
            let label = format!(" {} {} ", weekday_pl(t.weekday()), t.format("%d.%m.%Y"));
            let side = width.saturating_sub(label.width()) / 2;
            out.push(Line::styled(format!("{}{label}{}", "─".repeat(side), "─".repeat(side)), dim()));
            last_day = Some(day);
            last_sender = None;
        }
        let who = (m.from_me, m.sender.clone());
        let same = last_sender.as_ref() == Some(&who);
        last_sender = Some(who);
        let name = if m.from_me { "ja".to_string() } else if m.sender.is_empty() { "?".into() } else { m.sender.clone() };
        let name_style = if m.from_me {
            Style::default().add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(name_color(&name))
        };
        let (mark, body_style) = match m.state {
            SendState::Pending => ("…", dim()),
            SendState::Failed => ("!", Style::default().fg(Color::Red)),
            SendState::Sent => (" ", Style::default()),
        };
        for (i, l) in wrap(&m.content, text_w).into_iter().enumerate() {
            if i == 0 {
                let name_cell = if same { " ".repeat(SENDER_W) } else { fit(&name, SENDER_W) };
                out.push(Line::from(vec![
                    Span::styled(format!("{} ", t.format("%H:%M")), dim()),
                    Span::styled(mark.to_string(), body_style),
                    Span::styled(name_cell, name_style),
                    Span::raw(" "),
                    Span::styled(l, body_style),
                ]));
            } else {
                out.push(Line::from(vec![Span::raw(indent.clone()), Span::styled(l, body_style)]));
            }
        }
    }
    out
}

fn draw_pager(f: &mut Frame, app: &mut App, area: Rect) {
    let lines = pager_lines(app, area.width as usize);
    let h = area.height as usize;
    if lines.is_empty() {
        f.render_widget(Paragraph::new("  (wczytuję wiadomości…)").style(dim()), area);
        return;
    }
    let max_offset = lines.len().saturating_sub(h);
    app.pager_offset = app.pager_offset.min(max_offset);
    let start = lines.len().saturating_sub(h + app.pager_offset);
    let visible: Vec<Line> = lines.into_iter().skip(start).take(h).collect();
    // Anchor short conversations to the bottom, like a chat log.
    let pad = h.saturating_sub(visible.len()) as u16;
    let area = Rect { y: area.y + pad, height: area.height - pad, ..area };
    f.render_widget(Paragraph::new(visible), area);
}

fn draw_status(f: &mut Frame, app: &App, area: Rect) {
    let w = area.width as usize;
    let where_ = match app.view {
        View::Index if app.filter.is_empty() => format!("Czaty [{}]", app.chats.len()),
        View::Index => format!("Czaty [{}/{}] filtr: {}", app.visible.len(), app.chats.len(), app.filter),
        View::Pager => app.current_chat().map_or_else(|| "Czat".into(), |c| c.title.clone()),
    };
    let unread = app.unread_count();
    let mut left = format!("-*- teams-tui: {where_}");
    if unread > 0 {
        left.push_str(&format!(" -- nowe: {unread}"));
    }
    if let (View::Pager, Some(t)) = (app.view, &app.typing) {
        left.push_str(&format!(" -- {t}"));
    }
    if app.view == View::Pager && app.pager_offset > 0 {
        left.push_str(&format!(" -- ↑{}", app.pager_offset));
    }
    left.push(' ');
    let right = format!(" {} -*-", if app.connected { "połączono" } else { "łączenie…" });
    let fill = w.saturating_sub(left.width() + right.width());
    let text = truncate(&format!("{left}{}{right}", "-".repeat(fill)), w);
    f.render_widget(Paragraph::new(text).style(Style::default().add_modifier(Modifier::REVERSED)), area);
}

fn draw_prompt(f: &mut Frame, app: &App, area: Rect) {
    let (prefix, edit) = match &app.input {
        Input::Reply(e) => {
            let to = app.current_chat().map_or("", |c| c.title.as_str());
            (format!("Do {}: ", truncate(to, 24)), Some(e))
        }
        Input::Command(e) => (":".to_string(), Some(e)),
        Input::Filter(e) => ("Filtr: ".to_string(), Some(e)),
        Input::ConfirmSend(t) => {
            let msg = format!("Wysłać {} znaków? (y)es/(e)dytuj/(n)ie", t.chars().count());
            f.render_widget(Paragraph::new(msg).style(Style::default().add_modifier(Modifier::BOLD)), area);
            return;
        }
        Input::None => {
            let style = if app.error { Style::default().fg(Color::Red) } else { Style::default() };
            f.render_widget(Paragraph::new(truncate(&app.status, area.width as usize)).style(style), area);
            return;
        }
    };
    let Some(e) = edit else { return };
    let before: String = e.text.chars().take(e.cursor).collect();
    let avail = (area.width as usize).saturating_sub(prefix.width() + 1);
    // Scroll horizontally so the cursor stays visible.
    let skip = before.width().saturating_sub(avail);
    let shown: String = {
        let mut w = 0;
        e.text.chars().skip_while(|c| {
            let skip_it = w < skip;
            w += unicode_width::UnicodeWidthChar::width(*c).unwrap_or(0);
            skip_it
        }).collect()
    };
    f.render_widget(Paragraph::new(format!("{prefix}{}", truncate(&shown, avail + 1))), area);
    let x = area.x + (prefix.width() + before.width() - skip) as u16;
    f.set_cursor_position(Position { x: x.min(area.right().saturating_sub(1)), y: area.y });
}

fn draw_help(f: &mut Frame, area: Rect) {
    const HELP: &[&str] = &[
        "Lista czatów",
        "  j/k ↓/↑        ruch            g/G        początek/koniec",
        "  C-d/C-u PgDn   strona          Enter/l    otwórz czat",
        "  /              filtr nazw      Esc        wyczyść filtr",
        "  Tab            następny nieprzeczytany",
        "  R              odśwież listę   q          wyjście",
        "",
        "Rozmowa",
        "  j/k C-d/C-u    przewijanie     g/G        najstarsze/najnowsze",
        "  i/r            odpowiedz w linii (Enter wysyła, Esc anuluje)",
        "  E/R            odpowiedz w $EDITOR (z potwierdzeniem)",
        "  o              odśwież rozmowę q/h/Esc    wróć do listy",
        "",
        "Komendy (:)",
        "  :chat <nazwa>  otwórz czat     :show/:hide/:browser  okno przeglądarki",
        "  :refresh       odśwież listę   :q                    wyjście",
        "",
        "Linia edycji: C-a/C-e początek/koniec, C-w usuń słowo, C-u wyczyść",
        "",
        "                       dowolny klawisz zamyka pomoc",
    ];
    let w = HELP.iter().map(|l| l.width()).max().unwrap_or(40) as u16 + 4;
    let h = HELP.len() as u16 + 2;
    let r = Rect {
        x: area.x + area.width.saturating_sub(w) / 2,
        y: area.y + area.height.saturating_sub(h) / 2,
        width: w.min(area.width),
        height: h.min(area.height),
    };
    f.render_widget(Clear, r);
    let lines: Vec<Line> = std::iter::once(Line::raw(""))
        .chain(HELP.iter().map(|l| Line::raw(format!("  {l}"))))
        .collect();
    f.render_widget(Paragraph::new(lines).style(Style::default().add_modifier(Modifier::REVERSED)), r);
}
