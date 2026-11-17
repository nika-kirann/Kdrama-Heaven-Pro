"""Process entrypoint. The health server binds before Telegram or MongoDB."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from pyrogram import idle

from bot.client import FileStoreBot
from bot.config import settings
from bot.health import set_bot_status, start_health_server

ROOT = Path(__file__).resolve().parent.parent


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


async def _run() -> None:
    runner = await start_health_server(settings.web_host, settings.port)
    bot = FileStoreBot()
    try:
        await bot.start()
        await idle()
    finally:
        set_bot_status("stopping")
        if getattr(bot, "is_connected", False):
            await bot.stop()
        elif bot.db is not None:
            bot.db.close()
        await runner.cleanup()


def main() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    _configure_logging()
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        raise


if __name__ == "__main__":
    main()
