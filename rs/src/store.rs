//! Local SQLite cache: the UI renders from here instantly, the browser fills it in.

use std::path::Path;

use anyhow::Result;
use chrono::{DateTime, Utc};
use rusqlite::{Connection, params};

use crate::model::{Chat, ChatKind, Message, SendState};

pub struct Store {
    db: Connection,
}

impl Store {
    pub fn open(path: &Path) -> Result<Self> {
        let db = Connection::open(path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
        }
        db.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             CREATE TABLE IF NOT EXISTS chats (
                 id TEXT PRIMARY KEY,
                 kind TEXT NOT NULL,
                 title TEXT NOT NULL,
                 preview TEXT NOT NULL DEFAULT '',
                 time_label TEXT NOT NULL DEFAULT '',
                 unread INTEGER NOT NULL DEFAULT 0,
                 ord INTEGER NOT NULL DEFAULT 0
             );
             CREATE TABLE IF NOT EXISTS messages (
                 chat_id TEXT NOT NULL,
                 id TEXT NOT NULL,
                 sender TEXT NOT NULL,
                 from_me INTEGER NOT NULL,
                 content TEXT NOT NULL,
                 ts INTEGER NOT NULL,
                 PRIMARY KEY (chat_id, id)
             );
             CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts);
             CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
        )?;
        Ok(Self { db })
    }

    pub fn chats(&self) -> Result<Vec<Chat>> {
        let mut st = self
            .db
            .prepare("SELECT id, kind, title, preview, time_label, unread, ord FROM chats ORDER BY ord")?;
        let rows = st.query_map([], |r| {
            Ok(Chat {
                id: r.get(0)?,
                kind: ChatKind::parse(&r.get::<_, String>(1)?),
                title: r.get(2)?,
                preview: r.get(3)?,
                time_label: r.get(4)?,
                unread: r.get::<_, i64>(5)? != 0,
                order: r.get::<_, i64>(6)? as usize,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Replace the chat list with a fresh sidebar snapshot (keeps messages).
    pub fn replace_chats(&mut self, chats: &[Chat]) -> Result<()> {
        let tx = self.db.transaction()?;
        tx.execute("DELETE FROM chats", [])?;
        {
            let mut st = tx.prepare(
                "INSERT OR REPLACE INTO chats (id, kind, title, preview, time_label, unread, ord)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            )?;
            for c in chats {
                st.execute(params![
                    c.id,
                    c.kind.as_str(),
                    c.title,
                    c.preview,
                    c.time_label,
                    c.unread as i64,
                    c.order as i64
                ])?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    pub fn messages(&self, chat_id: &str, limit: usize) -> Result<Vec<Message>> {
        let mut st = self.db.prepare(
            "SELECT id, sender, from_me, content, ts FROM messages
             WHERE chat_id = ?1 ORDER BY ts DESC LIMIT ?2",
        )?;
        let rows = st.query_map(params![chat_id, limit as i64], |r| {
            let ts: i64 = r.get(4)?;
            Ok(Message {
                id: r.get(0)?,
                chat_id: chat_id.to_string(),
                sender: r.get(1)?,
                from_me: r.get::<_, i64>(2)? != 0,
                content: r.get(3)?,
                time: DateTime::<Utc>::from_timestamp_millis(ts).unwrap_or_default(),
                state: SendState::Sent,
            })
        })?;
        let mut v: Vec<Message> = rows.collect::<rusqlite::Result<_>>()?;
        v.reverse();
        Ok(v)
    }

    /// Insert messages; returns those that were not known before.
    pub fn add_messages(&mut self, msgs: &[Message]) -> Result<Vec<Message>> {
        let tx = self.db.transaction()?;
        let mut new = Vec::new();
        {
            let mut st = tx.prepare(
                "INSERT INTO messages (chat_id, id, sender, from_me, content, ts)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)
                 ON CONFLICT(chat_id, id) DO UPDATE SET
                     content = excluded.content,
                     sender = CASE WHEN excluded.sender = '' THEN messages.sender ELSE excluded.sender END,
                     from_me = MAX(messages.from_me, excluded.from_me)",
            )?;
            let mut exists = tx.prepare("SELECT 1 FROM messages WHERE chat_id = ?1 AND id = ?2")?;
            for m in msgs.iter().filter(|m| !m.is_local()) {
                let known = exists.exists(params![m.chat_id, m.id])?;
                st.execute(params![
                    m.chat_id,
                    m.id,
                    m.sender,
                    m.from_me as i64,
                    m.content,
                    m.time.timestamp_millis()
                ])?;
                if !known {
                    new.push(m.clone());
                }
            }
        }
        tx.commit()?;
        Ok(new)
    }

    pub fn meta(&self, key: &str) -> Option<String> {
        self.db
            .query_row("SELECT value FROM meta WHERE key = ?1", [key], |r| r.get(0))
            .ok()
    }

    pub fn set_meta(&self, key: &str, value: &str) -> Result<()> {
        self.db
            .execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?1, ?2)", [key, value])?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(chat: &str, id: &str, content: &str) -> Message {
        Message {
            id: id.into(),
            chat_id: chat.into(),
            sender: "A".into(),
            from_me: false,
            content: content.into(),
            time: Utc::now(),
            state: SendState::Sent,
        }
    }

    #[test]
    fn dedup_per_chat() {
        let dir = std::env::temp_dir().join(format!("tt-store-{}", std::process::id()));
        let mut s = Store::open(&dir).unwrap();
        let new = s.add_messages(&[msg("c1", "1", "a"), msg("c2", "1", "b")]).unwrap();
        assert_eq!(new.len(), 2, "same id in different chats must not collide");
        let new = s.add_messages(&[msg("c1", "1", "a2")]).unwrap();
        assert!(new.is_empty());
        assert_eq!(s.messages("c1", 10).unwrap()[0].content, "a2");
        let _ = std::fs::remove_file(&dir);
    }
}
