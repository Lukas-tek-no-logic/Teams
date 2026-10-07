//! Paths and environment detection.

use std::path::PathBuf;

use anyhow::{Context, Result};

#[derive(Debug, Clone)]
pub struct Config {
    /// `~/.local/share/teams-tui` — shared with the Python version (same browser profile).
    pub data_dir: PathBuf,
    pub profile_dir: PathBuf,
    pub cache_db: PathBuf,
    pub log_file: PathBuf,
    pub chrome: PathBuf,
    pub wayland: bool,
}

impl Config {
    pub fn load() -> Result<Self> {
        let base = directories::BaseDirs::new().context("brak katalogu domowego")?;
        let data_dir = base.data_dir().join("teams-tui");
        std::fs::create_dir_all(&data_dir)?;
        let profile_dir = data_dir.join("chromium-profile");
        std::fs::create_dir_all(&profile_dir)?;
        let chrome = crate::browser::find_chrome().context(
            "nie znaleziono Chromium — ustaw TEAMS_TUI_CHROME albo zainstaluj: uv run playwright install chromium",
        )?;
        let wayland = std::env::var("XDG_SESSION_TYPE").is_ok_and(|s| s.eq_ignore_ascii_case("wayland"))
            || std::env::var_os("WAYLAND_DISPLAY").is_some();
        Ok(Self {
            cache_db: data_dir.join("cache-rs.db"),
            log_file: data_dir.join("teams-tui-rs.log"),
            profile_dir,
            data_dir,
            chrome,
            wayland,
        })
    }
}
