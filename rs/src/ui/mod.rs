//! Terminal event loop: keys, browser events and redraws. Redraws only after a change.

mod app;
mod text;
mod view;

use std::io::Write;
use std::time::Duration;

use anyhow::Result;
use crossterm::event::{Event, EventStream, KeyEventKind};
use futures::StreamExt;
use tokio::sync::mpsc;

use crate::bridge::{Cmd, Ev};
use crate::store::Store;
use app::{Action, App, Input};

pub async fn run(store: Store, cmd: mpsc::UnboundedSender<Cmd>, mut ev: mpsc::UnboundedReceiver<Ev>) -> Result<()> {
    let mut terminal = ratatui::init();
    let mut app = App::new(store, cmd);
    let mut keys = EventStream::new();
    let mut tick = tokio::time::interval(Duration::from_secs(30));
    let mut dirty = true;

    let result = loop {
        if dirty {
            terminal.draw(|f| view::draw(f, &mut app))?;
            dirty = false;
        }
        tokio::select! {
            k = keys.next() => {
                match k {
                    Some(Ok(Event::Key(k))) if k.kind != KeyEventKind::Release => {
                        match app.on_key(k) {
                            Action::Quit => break Ok(()),
                            Action::Editor => {
                                let initial = match &app.input {
                                    Input::ConfirmSend(t) => t.clone(),
                                    _ => String::new(),
                                };
                                app.input = Input::None;
                                ratatui::restore();
                                let text = edit_in_editor(&initial);
                                terminal = ratatui::init();
                                terminal.clear()?;
                                app.editor_done(text);
                            }
                            Action::None => {}
                        }
                        dirty = true;
                    }
                    Some(Ok(Event::Resize(..))) => dirty = true,
                    Some(Ok(_)) => {}
                    Some(Err(e)) => break Err(e.into()),
                    None => break Ok(()),
                }
            }
            e = ev.recv() => {
                let Some(e) = e else { continue };
                for n in app.on_event(e) {
                    notify(n.title, n.body);
                }
                // Drain whatever else is queued so a burst costs one redraw.
                while let Ok(e) = ev.try_recv() {
                    for n in app.on_event(e) {
                        notify(n.title, n.body);
                    }
                }
                dirty = true;
            }
            _ = tick.tick() => dirty = true,
        }
    };
    ratatui::restore();
    result
}

/// Open $EDITOR (vi fallback) on a temp file; returns the text, or None if it failed.
fn edit_in_editor(initial: &str) -> Option<String> {
    let path = std::env::temp_dir().join(format!("teams-tui-{}.txt", std::process::id()));
    {
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&path)
            .ok()?;
        f.write_all(initial.as_bytes()).ok()?;
    }
    let editor = std::env::var("VISUAL").or_else(|_| std::env::var("EDITOR")).unwrap_or_else(|_| "vi".into());
    let mut parts = editor.split_whitespace();
    let ok = std::process::Command::new(parts.next()?)
        .args(parts)
        .arg(&path)
        .status()
        .is_ok_and(|s| s.success());
    let text = std::fs::read_to_string(&path).ok();
    let _ = std::fs::remove_file(&path);
    if ok { text } else { None }
}

fn notify(title: String, body: String) {
    tokio::task::spawn_blocking(move || {
        let body: String = body.chars().take(200).collect();
        let _ = notify_rust::Notification::new().appname("teams-tui").summary(&title).body(&body).show();
    });
}
