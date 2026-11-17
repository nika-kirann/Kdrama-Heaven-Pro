"""Users ask for a drama; admins accept or reject the request."""

from __future__ import annotations

import html
import logging
from datetime import timedelta

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.client import FileStoreBot
from bot.config import settings
from bot.delivery import gate_user
from bot.utils import utcnow

log = logging.getLogger("kdrama.requests")


@FileStoreBot.on_message(filters.private & filters.command("request"))
async def request_command(client: FileStoreBot, message):
    if not await gate_user(client, message):
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /request <code>Drama title</code>")
        return
    text = message.text.split(maxsplit=1)[1].strip()
    if len(text) < 2:
        await message.reply_text("Tell me the title.")
        return
    since = utcnow() - timedelta(hours=1)
    if await client.db.recent_request_count(message.from_user.id, since) >= 3:
        await message.reply_text("You can send 3 requests an hour.")
        return
    doc = await client.db.add_request(message.from_user, text)
    await message.reply_text(
        f"Request saved.\nId: <code>{doc['_id']}</code>\nAn admin will see it.",
    )
    note = (
        "<b>New request</b>\n"
        f"From: {message.from_user.mention} (<code>{message.from_user.id}</code>)\n"
        f"Id: <code>{doc['_id']}</code>\n"
        f"{html.escape(doc['text'])}"
    )
    markup = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Done", callback_data=f"rq:done:{doc['_id']}"),
            InlineKeyboardButton("Reject", callback_data=f"rq:no:{doc['_id']}"),
        ]]
    )
    try:
        await client.send_message(settings.log_channel, note, reply_markup=markup)
    except Exception:
        log.info("request notification could not be sent to the log channel")


@FileStoreBot.on_message(filters.private & filters.command("myrequests"))
async def my_requests(client: FileStoreBot, message):
    rows = await client.db.user_requests(message.from_user.id)
    if not rows:
        await message.reply_text("You have not requested anything yet.")
        return
    lines = ["<b>Your requests</b>"]
    for doc in rows:
        lines.append(
            f"• <code>{doc['_id']}</code> [{html.escape(doc.get('status') or '')}] "
            f"{html.escape(doc.get('text') or '')}"
        )
    await message.reply_text("\n".join(lines))
