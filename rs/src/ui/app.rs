//! UI state and key handling. Pure state transitions; side effects are returned as `Action`s
//! or sent as `Cmd`s, so the UI never blocks on the browser.

use chrono::Utc;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use tokio::sync::mpsc;

use crate::bridge::{Cmd, Ev};
use crate::html::fold;
use crate::model::{Chat, Message, SendState};
use crate::store::Store;

const HISTORY: usize = 300;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum View {
    Index,
    Pager,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Input {
    None,
    Reply(LineEdit),
    Command(LineEdit),
    Filter(LineEdit),
    ConfirmSend(String),
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LineEdit {
    pub text: String,
    /// Cursor position in chars.
    pub cursor: usize,
}

impl LineEdit {
    fn handle(&mut self, k: &KeyEvent) {
        let byte = |s: &str, i: usize| s.char_indices().nth(i).map_or(s.len(), |(b, _)| b);
        match k.code {
            KeyCode::Char('u') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                self.text.clear();
                self.cursor = 0;
            }
            KeyCode::Char('w') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                let chars: Vec<char> = self.text.chars().collect();
                let mut i = self.cursor;
                while i > 0 && chars[i - 1] == ' ' {
                    i -= 1;
                }
                while i > 0 && chars[i - 1] != ' ' {
                    i -= 1;
                }
                let (a, b) = (byte(&self.text, i), byte(&self.text, self.cursor));
                self.text.replace_range(a..b, "");
                self.cursor = i;
            }
            KeyCode::Char('a') if k.modifiers.contains(KeyModifiers::CONTROL) => self.cursor = 0,
            KeyCode::Char('e') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                self.cursor = self.text.chars().count()
            }
            KeyCode::Char(c) if !k.modifiers.contains(KeyModifiers::CONTROL) => {
                let b = byte(&self.text, self.cursor);
                self.text.insert(b, c);
                self.cursor += 1;
            }
            KeyCode::Backspace if self.cursor > 0 => {
                let (a, b) = (byte(&self.text, self.cursor - 1), byte(&self.text, self.cursor));
                self.text.replace_range(a..b, "");
                self.cursor -= 1;
            }
            KeyCode::Delete if self.cursor < self.text.chars().count() => {
                let (a, b) = (byte(&self.text, self.cursor), byte(&self.text, self.cursor + 1));
                self.text.replace_range(a..b, "");
            }
            KeyCode::Left => self.cursor = self.cursor.saturating_sub(1),
            KeyCode::Right => self.cursor = (self.cursor + 1).min(self.text.chars().count()),
            KeyCode::Home => self.cursor = 0,
            KeyCode::End => self.cursor = self.text.chars().count(),
            _ => {}
        }
    }
}

/// Things the event loop must do outside of pure state changes.
pub enum Action {
    None,
    Quit,
    /// Suspend the TUI and open $EDITOR for a reply.
    Editor,
}

pub struct App {
    pub store: Store,
    cmd: mpsc::UnboundedSender<Cmd>,
    pub view: View,
    pub input: Input,
    pub chats: Vec<Chat>,
    /// Indices into `chats` matching the current filter.
    pub visible: Vec<usize>,
    pub filter: String,
    pub sel: usize,
    pub index_top: usize,
    pub chat_id: Option<String>,
    pub messages: Vec<Message>,
    /// Lines scrolled up from the bottom of the pager.
    pub pager_offset: usize,
    pub status: String,
    pub error: bool,
    pub typing: Option<String>,
    pub connected: bool,
    pub show_help: bool,
    pub body_height: usize,
    started: chrono::DateTime<Utc>,
    local_seq: u64,
    browser_visible: bool,
}

impl App {
    pub fn new(store: Store, cmd: mpsc::UnboundedSender<Cmd>) -> Self {
        let chats = store.chats().unwrap_or_default();
        let mut app = Self {
            store,
            cmd,
            view: View::Index,
            input: Input::None,
            visible: Vec::new(),
            chats,
            filter: String::new(),
            sel: 0,
            index_top: 0,
            chat_id: None,
            messages: Vec::new(),
            pager_offset: 0,
            status: "uruchamiam…".into(),
            error: false,
            typing: None,
            connected: false,
            show_help: false,
            body_height: 20,
            started: Utc::now(),
            local_seq: 0,
            browser_visible: false,
        };
        app.apply_filter();
        app
    }

    fn send_cmd(&self, c: Cmd) {
        let _ = self.cmd.send(c);
    }

    fn set_status(&mut self, s: impl Into<String>) {
        self.status = s.into();
        self.error = false;
    }

    fn set_error(&mut self, s: impl Into<String>) {
        self.status = s.into();
        self.error = true;
    }

    pub fn current_chat(&self) -> Option<&Chat> {
        let id = self.chat_id.as_deref()?;
        self.chats.iter().find(|c| c.id == id)
    }

    pub fn selected_chat(&self) -> Option<&Chat> {
        self.visible.get(self.sel).map(|&i| &self.chats[i])
    }

    pub fn unread_count(&self) -> usize {
        self.chats.iter().filter(|c| c.unread).count()
    }

    fn apply_filter(&mut self) {
        let keep = self.selected_chat().map(|c| c.id.clone());
        let f = fold(&self.filter);
        self.visible = (0..self.chats.len())
            .filter(|&i| f.is_empty() || fold(&self.chats[i].title).contains(&f))
            .collect();
        self.sel = keep
            .and_then(|id| self.visible.iter().position(|&i| self.chats[i].id == id))
            .unwrap_or(0)
            .min(self.visible.len().saturating_sub(1));
    }

    // -- events from the browser ---------------------------------------------

    pub fn on_event(&mut self, ev: Ev) -> Vec<Notification> {
        let mut notes = Vec::new();
        match ev {
            Ev::Status(s) => self.set_status(s),
            Ev::Error(s) => self.set_error(s),
            Ev::LoginRequired => {
                self.set_error("zaloguj się do Teams w otwartym oknie przeglądarki");
            }
            Ev::Ready => {
                self.connected = true;
                if let Some(id) = self.chat_id.clone() {
                    self.send_cmd(Cmd::OpenChat(id));
                }
            }
            Ev::Chats(chats) => {
                let _ = self.store.replace_chats(&chats);
                self.chats = chats;
                self.apply_filter();
            }
            Ev::Messages { chat_id, messages } => {
                let new = self.store.add_messages(&messages).unwrap_or_default();
                for m in &new {
                    let viewing = self.view == View::Pager && self.chat_id.as_deref() == Some(&chat_id);
                    if !m.from_me && !viewing && m.time > self.started {
                        let title = self
                            .chats
                            .iter()
                            .find(|c| c.id == chat_id)
                            .map_or_else(|| "Teams".to_string(), |c| c.title.clone());
                        notes.push(Notification { title, body: m.content.clone() });
                    }
                }
                if self.chat_id.as_deref() == Some(&chat_id) {
                    self.reload_messages(!new.is_empty());
                }
            }
            Ev::SendResult { chat_id, local_id, ok, error } => {
                if ok {
                    self.set_status("wysłano");
                } else {
                    self.set_error(format!("nie wysłano: {}", error.unwrap_or_default()));
                }
                if self.chat_id.as_deref() == Some(&chat_id) {
                    if let Some(m) = self.messages.iter_mut().find(|m| m.id == local_id) {
                        m.state = if ok { SendState::Sent } else { SendState::Failed };
                    }
                    if ok {
                        self.reload_messages(true);
                    }
                }
            }
            Ev::Typing(t) => self.typing = t,
        }
        notes
    }

    /// Reload the open chat from the store, keeping unconfirmed local messages.
    fn reload_messages(&mut self, new_arrived: bool) {
        let Some(id) = self.chat_id.clone() else { return };
        let mut msgs = self.store.messages(&id, HISTORY).unwrap_or_default();
        let latest_mine = msgs.iter().rev().find(|m| m.from_me).map(|m| m.content.clone());
        for m in self.messages.iter().filter(|m| m.is_local()) {
            // Drop the optimistic copy once Teams shows the real message.
            let confirmed = m.state == SendState::Sent
                && latest_mine.as_deref().is_some_and(|c| c.split_whitespace().eq(m.content.split_whitespace()));
            if !confirmed {
                msgs.push(m.clone());
            }
        }
        self.messages = msgs;
        if new_arrived && self.pager_offset > 0 {
            // Keep the reader's position; new lines appear below.
        }
    }

    // -- keys ----------------------------------------------------------------

    pub fn on_key(&mut self, k: KeyEvent) -> Action {
        if k.code == KeyCode::Char('c') && k.modifiers.contains(KeyModifiers::CONTROL) {
            return Action::Quit;
        }
        match std::mem::replace(&mut self.input, Input::None) {
            Input::None => {}
            Input::Reply(mut e) => {
                match k.code {
                    KeyCode::Esc => self.set_status("anulowano"),
                    KeyCode::Enter => self.submit_reply(e.text),
                    _ => {
                        e.handle(&k);
                        self.input = Input::Reply(e);
                    }
                }
                return Action::None;
            }
            Input::Command(mut e) => {
                match k.code {
                    KeyCode::Esc => {}
                    KeyCode::Enter => return self.run_command(e.text.trim()),
                    KeyCode::Backspace if e.text.is_empty() => {}
                    _ => {
                        e.handle(&k);
                        self.input = Input::Command(e);
                    }
                }
                return Action::None;
            }
            Input::Filter(mut e) => {
                match k.code {
                    KeyCode::Esc => {
                        self.filter.clear();
                        self.apply_filter();
                    }
                    KeyCode::Enter => {
                        if self.visible.len() == 1 {
                            self.open_selected();
                        }
                    }
                    KeyCode::Down | KeyCode::Up => {
                        self.move_sel(if k.code == KeyCode::Down { 1 } else { -1 });
                        self.input = Input::Filter(e);
                    }
                    _ => {
                        e.handle(&k);
                        self.filter = e.text.clone();
                        self.apply_filter();
                        self.input = Input::Filter(e);
                    }
                }
                return Action::None;
            }
            Input::ConfirmSend(text) => {
                match k.code {
                    KeyCode::Char('y') | KeyCode::Char('t') | KeyCode::Enter => self.submit_reply(text),
                    KeyCode::Char('e') => {
                        self.input = Input::ConfirmSend(text);
                        return Action::Editor;
                    }
                    _ => self.set_status("anulowano"),
                }
                return Action::None;
            }
        }

        if self.show_help {
            self.show_help = false;
            return Action::None;
        }

        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        let page = self.body_height.max(2) as isize - 1;
        match (self.view, k.code) {
            (_, KeyCode::Char('?')) => self.show_help = true,
            (_, KeyCode::Char(':')) => self.input = Input::Command(LineEdit::default()),
            (_, KeyCode::Tab) => self.next_unread(),
            (View::Index, KeyCode::Char('q')) => return Action::Quit,
            (View::Index, KeyCode::Char('j') | KeyCode::Down) => self.move_sel(1),
            (View::Index, KeyCode::Char('k') | KeyCode::Up) => self.move_sel(-1),
            (View::Index, KeyCode::Char('d')) if ctrl => self.move_sel(page / 2),
            (View::Index, KeyCode::Char('u')) if ctrl => self.move_sel(-page / 2),
            (View::Index, KeyCode::PageDown) => self.move_sel(page),
            (View::Index, KeyCode::PageUp) => self.move_sel(-page),
            (View::Index, KeyCode::Char('g') | KeyCode::Home) => self.sel = 0,
            (View::Index, KeyCode::Char('G') | KeyCode::End) => self.sel = self.visible.len().saturating_sub(1),
            (View::Index, KeyCode::Enter | KeyCode::Char('l') | KeyCode::Right) => self.open_selected(),
            (View::Index, KeyCode::Char('/')) => {
                self.input = Input::Filter(LineEdit { cursor: self.filter.chars().count(), text: self.filter.clone() })
            }
            (View::Index, KeyCode::Esc) => {
                self.filter.clear();
                self.apply_filter();
            }
            (View::Index, KeyCode::Char('R')) => {
                self.send_cmd(Cmd::RefreshChats);
                self.set_status("odświeżam listę…");
            }
            (View::Pager, KeyCode::Char('q' | 'h') | KeyCode::Esc | KeyCode::Left) => self.view = View::Index,
            (View::Pager, KeyCode::Char('j') | KeyCode::Down) => self.scroll(-1),
            (View::Pager, KeyCode::Char('k') | KeyCode::Up) => self.scroll(1),
            (View::Pager, KeyCode::Char('d')) if ctrl => self.scroll(-page / 2),
            (View::Pager, KeyCode::Char('u')) if ctrl => self.scroll(page / 2),
            (View::Pager, KeyCode::PageDown | KeyCode::Char(' ')) => self.scroll(-page),
            (View::Pager, KeyCode::PageUp | KeyCode::Char('b')) => self.scroll(page),
            (View::Pager, KeyCode::Char('G') | KeyCode::End) => self.pager_offset = 0,
            (View::Pager, KeyCode::Char('g') | KeyCode::Home) => self.pager_offset = usize::MAX / 2,
            (View::Pager, KeyCode::Char('i' | 'r')) => self.input = Input::Reply(LineEdit::default()),
            (View::Pager, KeyCode::Char('E' | 'R')) => return Action::Editor,
            (View::Pager, KeyCode::Char('o')) => self.send_cmd(Cmd::OpenChat(self.chat_id.clone().unwrap_or_default())),
            _ => {}
        }
        Action::None
    }

    fn move_sel(&mut self, delta: isize) {
        if self.visible.is_empty() {
            return;
        }
        let max = self.visible.len() as isize - 1;
        self.sel = (self.sel as isize + delta).clamp(0, max) as usize;
    }

    fn scroll(&mut self, up: isize) {
        self.pager_offset = (self.pager_offset as isize + up).max(0) as usize;
    }

    fn open_selected(&mut self) {
        let Some(chat) = self.selected_chat().cloned() else { return };
        self.open_chat(chat);
    }

    fn open_chat(&mut self, chat: Chat) {
        let changed = self.chat_id.as_deref() != Some(&chat.id);
        self.chat_id = Some(chat.id.clone());
        if changed {
            self.messages.clear();
            self.typing = None;
        }
        self.view = View::Pager;
        self.pager_offset = 0;
        // Instant render from cache; the browser fills in fresh messages.
        self.reload_messages(false);
        self.send_cmd(Cmd::OpenChat(chat.id));
        if let Some(c) = self.chats.iter_mut().find(|c| c.id == chat.id) {
            c.unread = false;
        }
    }

    fn next_unread(&mut self) {
        let start = self.visible.iter().position(|&i| Some(self.chats[i].id.as_str()) == self.chat_id.as_deref());
        let n = self.visible.len();
        let from = start.map_or(0, |s| s + 1);
        let found = (0..n).map(|k| (from + k) % n).find(|&p| self.chats[self.visible[p]].unread);
        match found {
            Some(p) => {
                self.sel = p;
                self.open_selected();
            }
            None => self.set_status("brak nieprzeczytanych"),
        }
    }

    fn submit_reply(&mut self, text: String) {
        let text = text.trim_end().to_string();
        if text.trim().is_empty() {
            self.set_status("pusta wiadomość — nie wysłano");
            return;
        }
        let Some(chat_id) = self.chat_id.clone() else {
            self.set_error("najpierw otwórz czat");
            return;
        };
        self.local_seq += 1;
        let local_id = format!("local-{}", self.local_seq);
        self.messages.push(Message {
            id: local_id.clone(),
            chat_id: chat_id.clone(),
            sender: String::new(),
            from_me: true,
            content: text.clone(),
            time: Utc::now(),
            state: SendState::Pending,
        });
        self.pager_offset = 0;
        self.set_status("wysyłam…");
        self.send_cmd(Cmd::Send { chat_id, text, local_id });
    }

    /// Text returned from $EDITOR: ask before sending, like mutt.
    pub fn editor_done(&mut self, text: Option<String>) {
        match text {
            Some(t) if !t.trim().is_empty() => self.input = Input::ConfirmSend(t),
            Some(_) => self.set_status("pusta wiadomość — nie wysłano"),
            None => self.set_error("nie udało się uruchomić edytora ($EDITOR)"),
        }
    }

    fn run_command(&mut self, line: &str) -> Action {
        let (cmd, arg) = line.split_once(' ').unwrap_or((line, ""));
        match cmd {
            "q" | "quit" | "wyjdź" => return Action::Quit,
            "show" | "pokaż" => {
                self.browser_visible = true;
                self.send_cmd(Cmd::ShowBrowser);
            }
            "hide" | "ukryj" => {
                self.browser_visible = false;
                self.send_cmd(Cmd::HideBrowser);
            }
            "browser" => {
                self.browser_visible = !self.browser_visible;
                self.send_cmd(if self.browser_visible { Cmd::ShowBrowser } else { Cmd::HideBrowser });
            }
            "refresh" | "r" => self.send_cmd(Cmd::RefreshChats),
            "chat" | "c" | "open" => {
                let f = fold(arg.trim());
                match self.chats.iter().find(|c| fold(&c.title).contains(&f)).cloned() {
                    Some(c) if !f.is_empty() => self.open_chat(c),
                    _ => self.set_error(format!("nie znaleziono czatu: {arg}")),
                }
            }
            "help" | "h" => self.show_help = true,
            "" => {}
            other => self.set_error(format!("nieznana komenda: {other} (:q :chat :show :hide :refresh :help)")),
        }
        Action::None
    }
}

pub struct Notification {
    pub title: String,
    pub body: String,
}
