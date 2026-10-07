//! Hidden Chromium running Teams web, driven over CDP.
//!
//! One driver task owns the page. It executes `Cmd`s one at a time and reacts to
//! DOM-change notifications from the injected observer, scraping only what changed.
//! Nothing waits on fixed sleeps: every wait polls a concrete condition every 100 ms.

mod js;

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, anyhow, bail};
use chromiumoxide::cdp::browser_protocol::browser::{
    Bounds, GetWindowForTargetParams, SetWindowBoundsParams, WindowState,
};
use chromiumoxide::cdp::browser_protocol::input::{
    DispatchKeyEventParams, DispatchKeyEventType, InsertTextParams,
};
use chromiumoxide::cdp::js_protocol::runtime::{AddBindingParams, EventBindingCalled};
use chromiumoxide::layout::Point;
use chromiumoxide::{Browser, BrowserConfig, Page};
use chrono::{DateTime, Utc};
use futures::StreamExt;
use serde::Deserialize;
use serde::de::DeserializeOwned;
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

use crate::bridge::{Cmd, Ev};
use crate::config::Config;
use crate::model::{Chat, ChatKind, Message, SendState};

const TEAMS_URL: &str = "https://teams.cloud.microsoft";
const OFFSCREEN: i64 = -32000;

pub async fn run(cfg: Config, mut cmds: mpsc::UnboundedReceiver<Cmd>, ev: mpsc::UnboundedSender<Ev>) {
    if let Err(e) = drive(&cfg, &mut cmds, &ev).await {
        warn!("browser driver stopped: {e:#}");
        let _ = ev.send(Ev::Error(format!("przeglądarka: {e:#}")));
    }
}

async fn drive(cfg: &Config, cmds: &mut mpsc::UnboundedReceiver<Cmd>, ev: &mpsc::UnboundedSender<Ev>) -> Result<()> {
    let status = |s: &str| {
        let _ = ev.send(Ev::Status(s.to_string()));
    };
    status("uruchamiam przeglądarkę…");
    let (mut browser, mut handler) = Browser::launch(browser_config(cfg)?)
        .await
        .context("nie udało się uruchomić Chromium (czy działa jeszcze stara wersja teams-tui?)")?;
    let handler_task = tokio::spawn(async move {
        while let Some(r) = handler.next().await {
            if let Err(e) = r {
                debug!("cdp handler: {e}");
            }
        }
    });

    let page = match browser.pages().await?.into_iter().next() {
        Some(p) => p,
        None => browser.new_page("about:blank").await?,
    };
    let d = Driver { page, ev: ev.clone(), wayland: cfg.wayland };

    let mut bindings = d.page.event_listener::<EventBindingCalled>().await?;
    d.page.execute(AddBindingParams::new("__ttEvent")).await?;
    d.page.evaluate_on_new_document(js::OBSERVER).await?;

    status("łączę z Teams…");
    d.page.goto(TEAMS_URL).await?;
    d.ensure_logged_in().await?;
    let _ = d.page.evaluate(js::OBSERVER).await;
    d.hide().await;
    d.wait_until(Duration::from_secs(15), || d.has_chat_items()).await;
    d.refresh_chats().await;
    let _ = ev.send(Ev::Ready);
    status("połączono");

    let mut safety = tokio::time::interval(Duration::from_secs(60));
    safety.tick().await;
    loop {
        tokio::select! {
            cmd = cmds.recv() => {
                let Some(cmd) = cmd else { break };
                d.handle(cmd).await;
            }
            Some(b) = bindings.next() => {
                if b.name == "__ttEvent" {
                    d.on_dom_event(&b.payload).await;
                }
            }
            _ = safety.tick() => d.refresh_chats().await,
        }
    }

    let _ = browser.close().await;
    let _ = browser.wait().await;
    handler_task.abort();
    Ok(())
}

fn browser_config(cfg: &Config) -> Result<BrowserConfig> {
    // Our own argument list: chromiumoxide's defaults add --enable-automation and
    // --lang=en_US, and the profile must keep Playwright's password-store settings
    // or the existing session cookies cannot be decrypted.
    let mut args = vec![
        "--no-first-run".to_string(),
        "--no-default-browser-check".into(),
        "--password-store=basic".into(),
        "--use-mock-keychain".into(),
        "--disable-blink-features=AutomationControlled".into(),
        "--use-fake-ui-for-media-stream".into(),
        "--disable-background-timer-throttling".into(),
        "--disable-backgrounding-occluded-windows".into(),
        "--disable-renderer-backgrounding".into(),
        "--lang=pl-PL".into(),
    ];
    if !cfg.wayland {
        args.push(format!("--window-position={OFFSCREEN},{OFFSCREEN}"));
    }
    BrowserConfig::builder()
        .chrome_executable(&cfg.chrome)
        .user_data_dir(&cfg.profile_dir)
        .with_head()
        .disable_default_args()
        .window_size(1280, 900)
        .viewport(None)
        .request_timeout(Duration::from_secs(30))
        .args(args)
        .build()
        .map_err(|e| anyhow!(e))
}

/// Find a Chromium binary: $TEAMS_TUI_CHROME, Playwright's download, or the system one.
pub fn find_chrome() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("TEAMS_TUI_CHROME") {
        return Some(PathBuf::from(p));
    }
    let home = std::env::var_os("HOME").map(PathBuf::from)?;
    let pw = home.join(".cache/ms-playwright");
    let mut found: Vec<PathBuf> = std::fs::read_dir(&pw)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().starts_with("chromium-")))
        .map(|p| p.join("chrome-linux64/chrome"))
        .filter(|p| p.exists())
        .collect();
    found.sort();
    found.pop().or_else(|| {
        ["/usr/bin/chromium", "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome"]
            .iter()
            .map(Path::new)
            .find(|p| p.exists())
            .map(Path::to_path_buf)
    })
}

struct Driver {
    page: Page,
    ev: mpsc::UnboundedSender<Ev>,
    wayland: bool,
}

#[derive(Deserialize)]
struct RawChat {
    id: String,
    prefix: String,
    title: String,
    time: String,
    preview: String,
    unread: bool,
}

#[derive(Deserialize)]
struct RawMessages {
    chat: String,
    messages: Vec<RawMessage>,
}

#[derive(Deserialize)]
struct RawMessage {
    id: String,
    ts: String,
    mine: bool,
    sender: String,
    content: String,
}

#[derive(Deserialize)]
struct DomEvent {
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    chats: bool,
    #[serde(default)]
    msgs: bool,
    #[serde(default)]
    text: String,
}

impl Driver {
    async fn eval<T: DeserializeOwned>(&self, expr: impl Into<String>) -> Result<T> {
        let r = self.page.evaluate(expr.into()).await?;
        Ok(r.into_value()?)
    }

    async fn wait_until<F, Fut>(&self, timeout: Duration, mut cond: F) -> bool
    where
        F: FnMut() -> Fut,
        Fut: Future<Output = bool>,
    {
        let start = Instant::now();
        loop {
            if cond().await {
                return true;
            }
            if start.elapsed() >= timeout {
                return false;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    async fn logged_in(&self) -> bool {
        self.eval::<bool>(js::APP_SHELL).await.unwrap_or(false)
    }

    async fn has_chat_items(&self) -> bool {
        self.eval::<serde_json::Value>(js::scrape_chats())
            .await
            .map(|v| v.as_array().is_some_and(|a| !a.is_empty()))
            .unwrap_or(false)
    }

    async fn ensure_logged_in(&self) -> Result<()> {
        if self.wait_until(Duration::from_secs(10), || self.logged_in()).await {
            return Ok(());
        }
        info!("not logged in, showing browser window");
        let _ = self.ev.send(Ev::LoginRequired);
        self.show().await;
        if self.wait_until(Duration::from_secs(300), || self.logged_in()).await {
            Ok(())
        } else {
            bail!("nie zalogowano w ciągu 5 minut")
        }
    }

    async fn handle(&self, cmd: Cmd) {
        match cmd {
            Cmd::OpenChat(id) => {
                if let Err(e) = self.open_chat(&id).await {
                    warn!("open chat failed: {e:#}");
                    let _ = self.ev.send(Ev::Error(format!("nie otworzono czatu: {e}")));
                }
            }
            Cmd::Send { chat_id, text, local_id } => {
                let r = self.send(&chat_id, &text).await;
                if let Err(e) = &r {
                    warn!("send failed: {e:#}");
                }
                let error = r.as_ref().err().map(|e| e.to_string());
                let _ = self.ev.send(Ev::SendResult { chat_id: chat_id.clone(), local_id, ok: r.is_ok(), error });
                self.scrape_messages_for(&chat_id).await;
            }
            Cmd::RefreshChats => self.refresh_chats().await,
            Cmd::ShowBrowser => self.show().await,
            Cmd::HideBrowser => self.hide().await,
        }
    }

    async fn on_dom_event(&self, payload: &str) {
        let Ok(e) = serde_json::from_str::<DomEvent>(payload) else { return };
        match e.kind.as_str() {
            "dirty" => {
                if e.chats {
                    self.refresh_chats().await;
                }
                if e.msgs {
                    let active = self.active_chat().await;
                    if !active.is_empty() {
                        self.scrape_messages_for(&active).await;
                    }
                }
            }
            "typing" => {
                let t = e.text.trim().to_string();
                let _ = self.ev.send(Ev::Typing((!t.is_empty()).then_some(t)));
            }
            _ => {}
        }
    }

    async fn refresh_chats(&self) {
        match self.eval::<Vec<RawChat>>(js::scrape_chats()).await {
            Ok(raw) if !raw.is_empty() => {
                let chats = raw
                    .into_iter()
                    .enumerate()
                    .map(|(i, r)| Chat {
                        id: r.id,
                        kind: ChatKind::from_prefix(&r.prefix),
                        title: r.title,
                        preview: r.preview,
                        time_label: r.time,
                        unread: r.unread,
                        order: i,
                    })
                    .collect();
                let _ = self.ev.send(Ev::Chats(chats));
            }
            Ok(_) => debug!("chat list empty"),
            Err(e) => debug!("scrape chats: {e}"),
        }
    }

    async fn active_chat(&self) -> String {
        self.eval::<String>(js::active_chat()).await.unwrap_or_default()
    }

    async fn click_point(&self, expr: String) -> Result<bool> {
        let p: Option<serde_json::Value> = self.eval(expr).await?;
        let Some(p) = p else { return Ok(false) };
        let (Some(x), Some(y)) = (p["x"].as_f64(), p["y"].as_f64()) else { return Ok(false) };
        self.page.click(Point { x, y }).await?;
        Ok(true)
    }

    /// Make `chat_id` the open chat in Teams. Clicks the sidebar item (switching to the
    /// Chat app first if needed) and waits until Teams reports it as selected.
    async fn select_chat(&self, chat_id: &str) -> Result<()> {
        if self.active_chat().await == chat_id {
            return Ok(());
        }
        if !self.click_point(js::chat_item_point(chat_id)).await? {
            // Not in the sidebar: probably another Teams app (Calendar, Teams) is open.
            self.click_point(js::point_of(&[js::CHAT_APP_BUTTON])).await?;
            let found = self
                .wait_until(Duration::from_secs(5), || {
                    let expr = js::chat_item_point(chat_id);
                    async move { self.eval::<Option<serde_json::Value>>(expr).await.ok().flatten().is_some() }
                })
                .await;
            if !found || !self.click_point(js::chat_item_point(chat_id)).await? {
                bail!("czatu nie ma na liście w Teams");
            }
        }
        let ok = self
            .wait_until(Duration::from_secs(5), || async move { self.active_chat().await == chat_id })
            .await;
        if !ok {
            bail!("Teams nie przełączył się na wybrany czat");
        }
        Ok(())
    }

    async fn open_chat(&self, chat_id: &str) -> Result<()> {
        self.select_chat(chat_id).await?;
        // Wait for the message list of *this* chat to render, then scrape.
        self.wait_until(Duration::from_secs(5), || async move {
            self.eval::<RawMessages>(js::scrape_messages())
                .await
                .is_ok_and(|r| r.chat == chat_id && !r.messages.is_empty())
        })
        .await;
        self.scrape_messages_for(chat_id).await;
        Ok(())
    }

    async fn scrape_messages_for(&self, chat_id: &str) {
        let raw = match self.eval::<RawMessages>(js::scrape_messages()).await {
            Ok(r) => r,
            Err(e) => {
                debug!("scrape messages: {e}");
                return;
            }
        };
        // Never attribute messages to a chat other than the one actually open.
        if raw.chat != chat_id || raw.messages.is_empty() {
            return;
        }
        let messages = raw
            .messages
            .into_iter()
            .map(|m| Message {
                id: m.id,
                chat_id: chat_id.to_string(),
                sender: m.sender,
                from_me: m.mine,
                content: m.content,
                time: m
                    .ts
                    .parse::<i64>()
                    .ok()
                    .and_then(DateTime::<Utc>::from_timestamp_millis)
                    .unwrap_or_else(Utc::now),
                state: SendState::Sent,
            })
            .collect();
        let _ = self.ev.send(Ev::Messages { chat_id: chat_id.to_string(), messages });
    }

    /// Type and send a message, verifying at each step that the right chat is open.
    async fn send(&self, chat_id: &str, text: &str) -> Result<()> {
        self.select_chat(chat_id).await?;
        if !self.click_point(js::point_of(js::COMPOSE_SELECTORS)).await? {
            bail!("nie znaleziono pola wiadomości");
        }
        let empty = self
            .wait_until(Duration::from_secs(2), || async move {
                self.eval::<Option<String>>(js::compose_text()).await.ok().flatten().is_some()
            })
            .await;
        if !empty {
            bail!("pole wiadomości nie jest aktywne");
        }
        let draft: Option<String> = self.eval(js::compose_text()).await?;
        if draft.is_some_and(|d| !d.is_empty()) {
            bail!("w Teams jest niewysłany szkic w tym czacie — nie nadpisuję");
        }

        for (i, line) in text.split('\n').enumerate() {
            if i > 0 {
                self.key("Enter", "Enter", 13, 8).await?; // Shift+Enter = new line
            }
            if !line.is_empty() {
                self.page.execute(InsertTextParams::new(line)).await?;
            }
        }

        let typed: Option<String> = self.eval(js::compose_text()).await?;
        let want: String = text.split_whitespace().collect();
        let got: String = typed.unwrap_or_default().split_whitespace().collect();
        if got != want {
            bail!("tekst w polu nie zgadza się z wiadomością — nie wysłano (sprawdź okno Teams)");
        }
        if self.active_chat().await != chat_id {
            bail!("czat zmienił się w trakcie pisania — nie wysłano");
        }
        self.key("Enter", "Enter", 13, 0).await?;

        let cleared = self
            .wait_until(Duration::from_secs(5), || async move {
                self.eval::<Option<String>>(js::compose_text()).await.ok().flatten().is_some_and(|t| t.is_empty())
            })
            .await;
        if !cleared {
            bail!("Teams nie potwierdził wysłania");
        }
        info!("message sent");
        Ok(())
    }

    async fn key(&self, key: &str, code: &str, vk: i64, modifiers: i64) -> Result<()> {
        let text = if key == "Enter" && modifiers == 0 { "\r" } else { "" };
        for kind in [DispatchKeyEventType::KeyDown, DispatchKeyEventType::KeyUp] {
            let mut b = DispatchKeyEventParams::builder()
                .r#type(kind.clone())
                .key(key)
                .code(code)
                .windows_virtual_key_code(vk)
                .native_virtual_key_code(vk)
                .modifiers(modifiers);
            if kind == DispatchKeyEventType::KeyDown && !text.is_empty() {
                b = b.text(text);
            }
            self.page.execute(b.build().map_err(|e| anyhow!(e))?).await?;
        }
        Ok(())
    }

    async fn window_id(&self) -> Result<chromiumoxide::cdp::browser_protocol::browser::WindowId> {
        let r = self.page.execute(GetWindowForTargetParams::default()).await?;
        Ok(r.result.window_id.clone())
    }

    async fn set_bounds(&self, bounds: Bounds) {
        let r = async {
            let id = self.window_id().await?;
            self.page.execute(SetWindowBoundsParams::new(id, bounds)).await?;
            anyhow::Ok(())
        }
        .await;
        if let Err(e) = r {
            debug!("window bounds: {e:#}");
        }
    }

    async fn show(&self) {
        // State and position cannot be combined in one call.
        self.set_bounds(Bounds { window_state: Some(WindowState::Normal), ..Default::default() }).await;
        if !self.wayland {
            self.set_bounds(Bounds {
                left: Some(100),
                top: Some(100),
                width: Some(1280),
                height: Some(900),
                ..Default::default()
            })
            .await;
        }
        let _ = self.page.bring_to_front().await;
    }

    async fn hide(&self) {
        if self.wayland {
            self.set_bounds(Bounds { window_state: Some(WindowState::Minimized), ..Default::default() }).await;
        } else {
            self.set_bounds(Bounds { window_state: Some(WindowState::Normal), ..Default::default() }).await;
            self.set_bounds(Bounds { left: Some(OFFSCREEN), top: Some(OFFSCREEN), ..Default::default() }).await;
        }
    }
}
