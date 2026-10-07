//! Messages between the UI and the browser driver.
//!
//! The UI never waits on the browser: it sends a `Cmd` and keeps rendering;
//! results come back asynchronously as `Ev`.

use crate::model::{Chat, Message};

#[derive(Debug)]
pub enum Cmd {
    /// Open a chat in the browser so it loads fresh messages.
    OpenChat(String),
    /// Send text to a chat. `local_id` identifies the optimistic message in the UI.
    Send { chat_id: String, text: String, local_id: String },
    RefreshChats,
    ShowBrowser,
    HideBrowser,
}

#[derive(Debug)]
pub enum Ev {
    /// Short status for the bottom line.
    Status(String),
    Error(String),
    LoginRequired,
    Ready,
    Chats(Vec<Chat>),
    Messages { chat_id: String, messages: Vec<Message> },
    SendResult { chat_id: String, local_id: String, ok: bool, error: Option<String> },
    Typing(Option<String>),
    /// Display name of the logged-in user, learned from the page.
    Me(String),
}
