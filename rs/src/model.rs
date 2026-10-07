//! Domain types shared by the browser driver, the store and the UI.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ChatKind {
    OneOnOne,
    Group,
    Meeting,
    Channel,
    Unknown,
}

impl ChatKind {
    pub fn from_prefix(prefix: &str) -> Self {
        if prefix.contains("OneOnOne") {
            ChatKind::OneOnOne
        } else if prefix.contains("GroupChat") {
            ChatKind::Group
        } else if prefix.contains("Meeting") {
            ChatKind::Meeting
        } else if prefix.contains("Channel") {
            ChatKind::Channel
        } else {
            ChatKind::Unknown
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            ChatKind::OneOnOne => "1:1",
            ChatKind::Group => "group",
            ChatKind::Meeting => "meeting",
            ChatKind::Channel => "channel",
            ChatKind::Unknown => "unknown",
        }
    }

    pub fn parse(s: &str) -> Self {
        match s {
            "1:1" => ChatKind::OneOnOne,
            "group" => ChatKind::Group,
            "meeting" => ChatKind::Meeting,
            "channel" => ChatKind::Channel,
            _ => ChatKind::Unknown,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Chat {
    pub id: String,
    pub kind: ChatKind,
    pub title: String,
    pub preview: String,
    /// Time label as shown by Teams in the sidebar ("14:32", "wczoraj", "pt.").
    pub time_label: String,
    pub unread: bool,
    /// Position in the Teams sidebar (0 = most recent). Teams already sorts by activity.
    pub order: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SendState {
    Sent,
    Pending,
    Failed,
}

#[derive(Debug, Clone)]
pub struct Message {
    pub id: String,
    pub chat_id: String,
    pub sender: String,
    pub from_me: bool,
    pub content: String,
    pub time: DateTime<Utc>,
    pub state: SendState,
}

impl Message {
    pub fn is_local(&self) -> bool {
        self.id.starts_with("local-")
    }
}
