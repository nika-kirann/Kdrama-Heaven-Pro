"""Help, identity, uptime, and the optional auto reply."""

from __future__ import annotations

import time

from pyrogram import filters

from bot.client import FileStoreBot
from bot.config import is_admin, settings
from bot.ui import help_text
from bot.utils import aware, format_user_text, uptime_string, utcnow


@FileStoreBot.on_message(filters.private & filters.command("help"))
async def help_command(_client: FileStoreBot, message):
    await message.reply_text(
        help_text(is_admin(message.from_user.id)),
        quote=True,
        disable_web_page_preview=True,
    )


@FileStoreBot.on_message(filters.private & filters.command("id"))
async def id_command(_client: FileStoreBot, message):
    await message.reply_text(f"Your user id is <code>{message.from_user.id}</code>.", quote=True)


@FileStoreBot.on_message(filters.private & filters.command("me"))
async def me_command(client: FileStoreBot, message):
    user_id = message.from_user.id
    premium = await client.db.is_premium(user_id)
    verified = await client.db.is_verified(user_id)
    doc = await client.db.users.find_one({"_id": user_id}) or {}
    until = aware(doc.get("premium_until"))
    premium_text = "no"
    if premium:
        premium_text = "yes, no expiry" if until is None else f"yes, until {until.strftime('%Y-%m-%d %H:%M UTC')}"
    verified_until = aware(doc.get("verified_until"))
    verify_text = "not required or expired"
    if verified and verified_until:
        verify_text = verified_until.strftime("%Y-%m-%d %H:%M UTC")
    banned = "yes" if user_id in client.banned else "no"
    await message.reply_text(
        f"Id: <code>{user_id}</code>\n"
        f"Admin: <b>{'yes' if is_admin(user_id) else 'no'}</b>\n"
        f"Premium: <b>{premium_text}</b>\n"
        f"Link verification: <b>{verify_text}</b>\n"
        f"Banned: <b>{banned}</b>",
        quote=True,
    )


@FileStoreBot.on_message(filters.private & filters.command(["uptime", "ping"]))
async def uptime_command(client: FileStoreBot, message):
    started = time.perf_counter()
    probe = await message.reply_text("Checking…", quote=True)
    elapsed = (time.perf_counter() - started) * 1000
    await probe.edit_text(
        "<b>Status</b>\n"
        f"Ping: <code>{elapsed:.0f} ms</code>\n"
        f"Uptime: <code>{uptime_string(client.started_at)}</code>\n"
        f"Now: <code>{utcnow().strftime('%Y-%m-%d %H:%M UTC')}</code>"
    )


@FileStoreBot.on_message(
    filters.private
    & ~filters.user(list(settings.admin_ids))
    & (filters.document | filters.video | filters.audio | filters.photo | filters.animation),
    group=1,
)
async def user_media(_client: FileStoreBot, message):
    await message.reply_text(
        "Only admins can store files. Open a link you were given, or use /search and /request.",
        quote=True,
    )


@FileStoreBot.on_message(
    filters.private & filters.incoming & filters.text & ~filters.regex(r"^/"),
    group=1,
)
async def auto_reply(client: FileStoreBot, message):
    if message.from_user is None or is_admin(message.from_user.id):
        return
    template = (client.cfg.get("user_reply_text") or "").strip()
    if not template:
        return
    await message.reply_text(
        format_user_text(template, message.from_user, extra={"uptime": uptime_string(client.started_at)})
    )
