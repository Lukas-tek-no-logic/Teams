"""Entry point: python -m teams_tui  /  teams-tui command."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from platformdirs import user_data_dir

from teams_tui.config import Config
from teams_tui.browser.engine import BrowserEngine
from teams_tui.browser.auth import AuthManager
from teams_tui.browser.interceptor import APIInterceptor, scrape_chat_list
from teams_tui.browser.dom_observer import DOMObserver
from teams_tui.browser.call_manager import CallManager
from teams_tui.browser.command_executor import CommandExecutor
from teams_tui.bridge.event_bus import EventBus
from teams_tui.data.store import DataStore
from teams_tui.data.cache import Cache
from teams_tui.notifications.notifier import Notifier
from teams_tui.app import TeamsTUIApp

_LOG_FILE = Path(user_data_dir("teams-tui")) / "teams-tui.log"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    filename=str(_LOG_FILE),
    filemode="a",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)
log = logging.getLogger("teams-tui")


async def run() -> None:
    config = Config.load()
    log.info("Data dir: %s", config.data_dir)
    log.info("Display server: %s", config.display_server)

    engine = BrowserEngine(config)
    executor_task: asyncio.Task | None = None
    call_manager: CallManager | None = None
    cache: Cache | None = None
    notifier: Notifier | None = None

    try:
        # 1. Initialize cache
        cache = Cache(config.data_dir / "cache.db")
        await cache.initialize()

        # 2. Connect desktop notifications (non-fatal)
        notifier = Notifier()
        try:
            await notifier.connect()
        except Exception as e:
            log.info("Notifications disabled: %s", e)
            notifier = None

        # 3. Launch browser
        await engine.start()

        # 4. Authenticate
        auth = AuthManager(engine)
        await auth.ensure_authenticated()

        # 5. Set up data pipeline
        bus = EventBus()
        store = DataStore()
        interceptor = APIInterceptor(bus)
        interceptor.attach(engine.page)

        # 6. Install DOM observer (typing, calls, unread)
        dom_observer = DOMObserver(bus)
        await dom_observer.install(engine.page)

        # 7. Wait for chat list to render (no reload needed — already loaded after auth)
        log.info("Waiting for chat list to render…")
        try:
            await engine.page.wait_for_selector(
                '[role="treeitem"]', timeout=10_000,
            )
        except Exception:
            log.info("Chat list selector not found, waiting a bit longer…")
            await engine.page.wait_for_timeout(3000)

        # 8. Scrape chat list from DOM
        chats = await scrape_chat_list(engine.page)
        if chats:
            store.update_chats(chats)
            log.info("Loaded %d chats.", len(chats))
        else:
            log.warning("No chats scraped from DOM.")

        # 9. Hide browser
        await engine.hide_window()

        # 10. Start command executor (TUI → browser)
        executor = CommandExecutor(engine, bus)
        executor_task = asyncio.create_task(executor.run())

        # 11. Start call manager (polling + event-driven)
        call_manager = CallManager(engine, bus)
        await call_manager.start()

        # 12. Launch TUI
        app = TeamsTUIApp(engine, bus, store, cache=cache, notifier=notifier)
        await app.run_async()

    except KeyboardInterrupt:
        pass
    finally:
        if call_manager:
            await call_manager.stop()
        if executor_task:
            executor_task.cancel()
            try:
                await executor_task
            except asyncio.CancelledError:
                pass
        await engine.stop()
        if cache:
            await cache.close()
        if notifier:
            await notifier.close()
        log.info("Bye.")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
