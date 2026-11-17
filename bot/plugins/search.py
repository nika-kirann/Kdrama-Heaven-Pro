"""Search and catalog browsing over indexed files."""

from __future__ import annotations

import secrets

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.client import FileStoreBot
from bot.config import is_admin
from bot.delivery import gate_user
from bot.ui import file_button_label, pager

PAGE = 8


def remember(client: FileStoreBot, user_id: int, mode: str, query: str) -> str:
    token = secrets.token_hex(3)
    client.searches[token] = {"user_id": user_id, "mode": mode, "query": query}
    if len(client.searches) > 300:
        for old in list(client.searches)[:50]:
            client.searches.pop(old, None)
    return token


async def render_page(client: FileStoreBot, token: str, page: int):
    session = client.searches.get(token)
    if not session:
        return None
    skip = page * PAGE
    if session["mode"] == "search":
        rows = await client.db.search_files(session["query"], skip, PAGE + 1)
        title = f"Search: <b>{_escape(session['query'])}</b>"
    else:
        rows = await client.db.recent_files(skip, PAGE + 1)
        title = "<b>Catalog</b>"
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    if not rows:
        return title + "\n\nNothing in this page.", None
    buttons = [
        [InlineKeyboardButton(file_button_label(doc), callback_data=f"get:{doc['link_token']}")]
        for doc in rows
        if doc.get("link_token")
    ]
    nav = pager("page", token, page, has_next)
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("Close", callback_data="close")])
    return title + "\nTap a file to receive it.", InlineKeyboardMarkup(buttons)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _within_rate(client: FileStoreBot, message) -> bool:
    user = message.from_user
    if user is None or is_admin(user.id) or client.limiter.allow(user.id):
        return True
    return False


@FileStoreBot.on_message(filters.private & filters.command("search"))
async def search_command(client: FileStoreBot, message):
    if not _within_rate(client, message):
        await message.reply_text("Slow down a little, then search again.")
        return
    if not await gate_user(client, message):
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /search <code>drama name</code>")
        return
    query = message.text.split(maxsplit=1)[1].strip()
    if len(query) < 2:
        await message.reply_text("Use at least 2 characters.")
        return
    if len(query) > 80:
        await message.reply_text("Keep the search under 80 characters.")
        return
    token = remember(client, message.from_user.id, "search", query)
    rendered = await render_page(client, token, 0)
    if rendered is None:
        await message.reply_text("Search expired. Try again.")
        return
    text, markup = rendered
    await message.reply_text(text, reply_markup=markup)


@FileStoreBot.on_message(filters.private & filters.command(["anime", "catalog"]))
async def catalog_command(client: FileStoreBot, message):
    if not _within_rate(client, message):
        await message.reply_text("Slow down a little, then try again.")
        return
    if not await gate_user(client, message):
        return
    total = await client.db.count_files()
    if total == 0:
        await message.reply_text("The catalog is empty. An admin has not stored any files yet.")
        return
    token = remember(client, message.from_user.id, "catalog", "")
    rendered = await render_page(client, token, 0)
    if rendered is None:
        await message.reply_text("The catalog page expired. Try again.")
        return
    text, markup = rendered
    await message.reply_text(f"{text}\nIndexed files: <b>{total}</b>", reply_markup=markup)
