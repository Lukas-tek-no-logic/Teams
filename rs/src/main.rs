//! teams-tui — fast terminal front-end for Microsoft Teams.
//!
//! Teams web runs in a hidden Chromium (as before); this binary drives it over CDP
//! and renders a mutt-style interface. See README.md.

mod bridge;
mod browser;
mod config;
mod html;
mod model;
mod store;
mod ui;

use anyhow::Result;
use tokio::sync::mpsc;

const HELP: &str = "teams-tui — terminalowy klient Microsoft Teams

Użycie: teams-tui [--help]

Zmienne środowiskowe:
  TEAMS_TUI_CHROME   ścieżka do Chromium (domyślnie Chromium z Playwright)
  RUST_LOG           poziom logów (domyślnie info), plik: ~/.local/share/teams-tui/teams-tui-rs.log

W programie naciśnij ? aby zobaczyć skróty klawiszowe.";

#[tokio::main]
async fn main() -> Result<()> {
    if std::env::args().skip(1).any(|a| a == "-h" || a == "--help") {
        println!("{HELP}");
        return Ok(());
    }

    let cfg = config::Config::load()?;
    let _log_guard = init_logging(&cfg)?;
    let store = store::Store::open(&cfg.cache_db)?;

    let (cmd_tx, cmd_rx) = mpsc::unbounded_channel();
    let (ev_tx, ev_rx) = mpsc::unbounded_channel();
    let browser_task = tokio::spawn(browser::run(cfg.clone(), cmd_rx, ev_tx));

    let result = ui::run(store, cmd_tx, ev_rx).await;

    // Dropping the command sender ends the driver loop, which closes Chromium.
    let _ = tokio::time::timeout(std::time::Duration::from_secs(5), browser_task).await;
    result
}

fn init_logging(cfg: &config::Config) -> Result<tracing_appender::non_blocking::WorkerGuard> {
    use std::os::unix::fs::OpenOptionsExt;
    let file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(&cfg.log_file)?;
    let (writer, guard) = tracing_appender::non_blocking(file);
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| "info,chromiumoxide=warn,tungstenite=warn".into());
    tracing_subscriber::fmt().with_env_filter(filter).with_ansi(false).with_writer(writer).init();
    Ok(guard)
}
