"""Pyrogram client bootstrap: database, force-subscribe, and commands."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import BotCommand, BotCommandScopeChat

from bot.config import settings
from bot.database import Database
from bot.delivery import resume_autodeletes
from bot.health import set_bot_status
from bot.utils import RateLimiter, auto_delete_label, utcnow

log = logging.getLogger("kdrama")

USER_COMMANDS = [
    BotCommand("start", "Start the bot or open a file link"),
    BotCommand("help", "How to use this bot"),
    BotCommand("id", "Show your Telegram user id"),
    BotCommand("me", "Show your access status"),
    BotCommand("search", "Search stored files by name"),
    BotCommand("anime", "Browse the stored catalog"),
    BotCommand("request", "Request a drama or series"),
    BotCommand("myrequests", "Check your requests"),
    BotCommand("uptime", "Show uptime and response time"),
]

ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("genlink", "Create a link for one post"),
    BotCommand("batch", "Create a link for a range of posts"),
    BotCommand("cbatch", "Create a link for selected posts"),
    BotCommand("rbatch", "Create a protected batch link"),
    BotCommand("delink", "Revoke a file link"),
    BotCommand("broadcast", "Send a message to all users"),
    BotCommand("stats", "Show library and user statistics"),
    BotCommand("users", "Show how many users the bot has"),
    BotCommand("ban", "Ban a user"),
    BotCommand("unban", "Unban a user"),
    BotCommand("premium", "Grant or revoke premium"),
    BotCommand("settings", "Edit messages and toggles"),
    BotCommand("panel", "Open the admin panel"),
    BotCommand("admins", "List bot admins"),
    BotCommand("requests", "List open drama requests"),
    BotCommand("restart", "Restart the bot"),
    BotCommand("cancel", "Cancel the current action"),
]


class FileStoreBot(Client):
    def __init__(self):
        super().__init__(
            name="kdrama-heaven",
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            bot_token=settings.bot_token,
            workers=settings.workers,
            plugins={"root": "bot.plugins"},
            in_memory=True,
            sleep_threshold=30,
        )
        self.db: Database | None = None
        self.cfg = settings.overlay()
        self.force_channels: list[dict] = []
        self.banned: set[int] = set()
        self.limiter = RateLimiter(settings.rate_limit, settings.rate_window)
        self.started_at: datetime = utcnow()
        self.username = ""
        self.db_channel = None
        self.searches: dict[str, dict] = {}
        self.broadcasts: dict[str, dict] = {}
        self.broadcasting = False

    async def start(self):
        self.started_at = datetime.now(timezone.utc)
        set_bot_status("starting")
        self.db = Database(settings.database_url, settings.database_name)
        try:
            await self.db.connect()
        except Exception as exc:
            log.error(
                "MongoDB connection failed (%s). Check DATABASE_URL, DATABASE_NAME, and network access.",
                type(exc).__name__,
            )
            raise SystemExit(1) from None
        try:
            await super().start()
        except Exception as exc:
            log.error(
                "Telegram login failed (%s). Check API_ID, API_HASH, and BOT_TOKEN.",
                type(exc).__name__,
            )
            raise SystemExit(1) from None

        me = await self.get_me()
        self.username = me.username or ""
        self.set_parse_mode(ParseMode.HTML)
        try:
            self.db_channel = await self.get_chat(settings.channel_id)
        except Exception as exc:
            log.error(
                "Cannot access CHANNEL_ID %s (%s). Add the bot as an admin with permission to post.",
                settings.channel_id,
                type(exc).__name__,
            )
            raise SystemExit(1) from None

        self.force_channels = await self._load_forcesub()
        self.cfg = await self.db.load_config(settings)
        self.file_auto_delete_source = getattr(self.db, "file_auto_delete_source", "env")
        self.banned = set(await self.db.banned_ids())
        seconds = int(self.cfg.get("file_auto_delete") or 0)
        log.info(
            "Auto-delete: %s (source: %s, FILE_AUTO_DELETE=%s). Admins are not exempt.",
            auto_delete_label(seconds),
            "MongoDB /settings" if self.file_auto_delete_source == "mongo" else "FILE_AUTO_DELETE",
            settings.file_auto_delete,
        )
        await self._install_commands()
        await self._announce()
        await resume_autodeletes(self)
        if settings.keep_alive_url:
            asyncio.create_task(self._keep_alive())
        set_bot_status("ready")
        log.info("bot @%s is ready", self.username)

    async def stop(self, *args, **kwargs):
        set_bot_status("stopping")
        try:
            await super().stop(*args, **kwargs)
        finally:
            if self.db is not None:
                self.db.close()

    async def _load_forcesub(self) -> list[dict]:
        ready = []
        for spec in settings.force_channels:
            try:
                chat = await self.get_chat(spec.chat_id)
            except Exception as exc:
                log.error(
                    "Force-sub channel %s is not reachable (%s). Add the bot as an admin.",
                    spec.chat_id,
                    type(exc).__name__,
                )
                continue
            title = spec.label or chat.title or str(spec.chat_id)
            try:
                if spec.request:
                    invite = await self.create_chat_invite_link(
                        spec.chat_id,
                        name="File access",
                        creates_join_request=True,
                    )
                    url = getattr(invite, "invite_link", None)
                elif chat.username:
                    url = f"https://t.me/{chat.username}"
                else:
                    url = chat.invite_link or await self.export_chat_invite_link(spec.chat_id)
            except Exception as exc:
                log.error(
                    "Could not build an invite for %s (%s).",
                    spec.chat_id,
                    type(exc).__name__,
                )
                continue
            if not url:
                log.error("No invite URL for force-sub channel %s.", spec.chat_id)
                continue
            ready.append(
                {"id": spec.chat_id, "title": title, "url": url, "request": spec.request}
            )
        if settings.force_channels and not ready:
            log.error("Every force-sub channel failed to load. The join gate is currently off.")
        return ready

    async def _install_commands(self) -> None:
        try:
            await self.set_bot_commands(USER_COMMANDS)
        except Exception as exc:
            log.warning("could not set default commands (%s)", type(exc).__name__)
        for admin_id in settings.admin_ids:
            try:
                await self.set_bot_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id))
            except Exception as exc:
                log.warning(
                    "could not set admin commands for %s (%s). They may need to open the bot once.",
                    admin_id,
                    type(exc).__name__,
                )

    async def _announce(self) -> None:
        seconds = int(self.cfg.get("file_auto_delete") or 0)
        if self.file_auto_delete_source == "mongo":
            timer = (
                f"Auto-delete: {auto_delete_label(seconds)} "
                f"(MongoDB /settings overrides FILE_AUTO_DELETE={settings.file_auto_delete})"
            )
        else:
            timer = f"Auto-delete: {auto_delete_label(seconds)} (FILE_AUTO_DELETE)"
        text = (
            "<b>Bot started</b>\n"
            f"Username: @{self.username}\n"
            f"Database channel: <code>{self.db_channel.id}</code>\n"
            f"Force-sub channels: {len(self.force_channels)}\n"
            f"Admins: {len(settings.admin_ids)}\n"
            f"{timer}"
        )
        try:
            await self.send_message(settings.log_channel, text)
        except Exception as exc:
            log.warning(
                "could not announce startup to LOG_CHANNEL %s (%s)",
                settings.log_channel,
                type(exc).__name__,
            )

    async def _keep_alive(self) -> None:
        import aiohttp

        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=20)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(settings.keep_alive_url) as response:
                        log.info("keep-alive HTTP %s", response.status)
            except Exception as exc:
                log.warning("keep-alive failed (%s)", type(exc).__name__)
            await asyncio.sleep(240)
