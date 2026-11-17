""" /start welcome, file links, and verification return links. """

from __future__ import annotations

import logging

from pyrogram import filters

from bot.client import FileStoreBot
from bot.config import settings
from bot.delivery import complete_verification, deliver_token
from bot.ui import welcome_markup
from bot.utils import format_user_text, uptime_string

log = logging.getLogger("kdrama.start")


@FileStoreBot.on_message(filters.private & filters.command("start"))
async def start_command(client: FileStoreBot, message):
    user = message.from_user
    created = await client.db.touch_user(user)
    if created and settings.new_user_log:
        try:
            await client.send_message(
                settings.log_channel,
                f"New user {user.mention}\nID: <code>{user.id}</code>",
            )
        except Exception:
            log.info("new-user log could not be delivered")

    payload = message.command[1] if len(message.command) > 1 else ""
    if payload.startswith("v"):
        await complete_verification(client, message)
        return
    if payload.startswith("f"):
        await deliver_token(client, user, message.chat.id, payload, count_use=True)
        return
    if payload and payload not in {"joined", "start"}:
        await message.reply_text("That start link is not recognized.")
    await _welcome(client, message)


async def _welcome(client: FileStoreBot, message) -> None:
    text = format_user_text(
        client.cfg.get("start_message") or "Hello {mention}",
        message.from_user,
        extra={"uptime": uptime_string(client.started_at)},
    )
    markup = welcome_markup()
    picture = (client.cfg.get("start_pic") or "").strip()
    if picture:
        try:
            await message.reply_photo(picture, caption=text, reply_markup=markup)
            return
        except Exception:
            log.warning("start picture could not be sent")
    await message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)
