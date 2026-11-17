"""Inline button router."""

from __future__ import annotations

import logging

from pyrogram.types import InlineKeyboardMarkup

from bot import __version__
from bot.client import FileStoreBot
from bot.config import EDITABLE, is_admin
from bot.delivery import deliver_token
from bot.plugins.admin import (
    is_restart_allowed,
    perform_restart,
    render_requests,
    render_stats,
    resolve_request,
    restart_markup,
    run_broadcast,
)
from bot.plugins.search import render_page
from bot.state import set_state
from bot.ui import about_text, help_text, panel_markup, settings_markup, settings_text, welcome_markup
from bot.utils import format_user_text, uptime_string

log = logging.getLogger("kdrama.callbacks")

try:
    import pyrogram

    _LIBRARY = pyrogram.__version__
except Exception:
    _LIBRARY = ""


async def _show(message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    try:
        if message.photo or message.video or message.document or message.animation:
            await message.edit_caption(text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        await message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)


@FileStoreBot.on_callback_query()
async def on_callback(client: FileStoreBot, query):
    data = query.data or ""
    user = query.from_user
    message = query.message
    if user is None or message is None:
        await query.answer()
        return
    try:
        await _route(client, query, data, user, message)
    except Exception:
        log.exception("callback %s failed", data[:40])
        await query.answer("Something went wrong. Try again.", show_alert=True)


async def _route(client, query, data: str, user, message) -> None:
    if data == "close":
        await query.answer()
        try:
            await message.delete()
        except Exception:
            await query.answer("I could not close that message.", show_alert=True)
        return

    if data == "about":
        await query.answer()
        await _show(
            message,
            about_text(client.username, __version__, _LIBRARY),
            InlineKeyboardMarkup_back(),
        )
        return

    if data == "home":
        await query.answer()
        text = format_user_text(
            client.cfg.get("start_message") or "Hello {mention}",
            user,
            extra={"uptime": uptime_string(client.started_at)},
        )
        await _show(message, text, welcome_markup())
        return

    if data == "go:help":
        await query.answer()
        await _show(message, help_text(is_admin(user.id)), InlineKeyboardMarkup_back())
        return

    if data == "go:search":
        await query.answer()
        await _show(message, "Send /search followed by a name.\nExample: <code>/search alchemy</code>", InlineKeyboardMarkup_back())
        return

    if data == "go:request":
        await query.answer()
        await _show(message, "Send /request and the title.\nExample: <code>/request Moving</code>", InlineKeyboardMarkup_back())
        return

    if data.startswith("again:") or data.startswith("get:"):
        token = data.split(":", 1)[1]
        await query.answer("Sending…")
        await deliver_token(client, user, message.chat.id, token, count_use=data.startswith("get:"))
        return

    if data.startswith("page:"):
        await query.answer()
        _prefix, token, page_text = data.split(":")
        session = client.searches.get(token)
        if not session or session.get("user_id") != user.id:
            await query.answer("Open the search again.", show_alert=True)
            return
        rendered = await render_page(client, token, int(page_text))
        if rendered is None:
            await query.answer("This page expired.", show_alert=True)
            return
        text, markup = rendered
        await _show(message, text, markup)
        return

    if not is_admin(user.id):
        await query.answer("Admins only.", show_alert=True)
        return

    if data == "panel:home":
        await query.answer()
        await _show(message, "<b>Admin panel</b>", panel_markup())
        return
    if data == "panel:stats":
        await query.answer()
        await _show(message, await render_stats(client), panel_markup())
        return
    if data == "panel:requests":
        await query.answer()
        text, markup = await render_requests(client)
        await _show(message, text, markup)
        return
    if data == "panel:ban":
        await query.answer()
        set_state(user.id, flow="ban")
        await _show(message, "Send the user id to ban, plus an optional reason. /cancel stops this.", None)
        return
    if data == "panel:unban":
        await query.answer()
        set_state(user.id, flow="unban")
        await _show(message, "Send the user id to unban. /cancel stops this.", None)
        return
    if data == "panel:premium":
        await query.answer()
        set_state(user.id, flow="premium")
        await _show(
            message,
            "Send <code>add USER_ID [days]</code>, <code>del USER_ID</code>, or use /premium list.",
            None,
        )
        return
    if data == "panel:broadcast":
        await query.answer("Reply to a message with /broadcast", show_alert=True)
        return
    if data == "panel:restart":
        if not is_restart_allowed(user.id):
            await query.answer("Only the owner can restart the bot.", show_alert=True)
            return
        await query.answer()
        await _show(message, "Restart the process? In-flight sends will stop.", restart_markup())
        return

    if data == "restart:yes":
        if not is_restart_allowed(user.id):
            await query.answer("Only the owner can restart the bot.", show_alert=True)
            return
        await query.answer("Restarting…")
        await message.edit_text("Restarting…")
        perform_restart()
        return

    if data.startswith("bc:"):
        _prefix, action, token = data.split(":")
        job = client.broadcasts.get(token)
        if not job or job.get("owner") != user.id:
            await query.answer("This broadcast prompt is not yours.", show_alert=True)
            return
        if action == "no":
            client.broadcasts.pop(token, None)
            await query.answer("Cancelled")
            await message.edit_text("Broadcast cancelled.")
            return
        await query.answer("Broadcast started")
        await run_broadcast(client, message, token, pin=(action == "pin"))
        return

    if data == "set:show":
        await query.answer()
        await _show(message, settings_text(client.cfg), settings_markup(client.cfg))
        return

    if data.startswith("set:toggle:"):
        key = data.split(":", 2)[2]
        if EDITABLE.get(key) != "bool":
            await query.answer("That setting is not a switch.", show_alert=True)
            return
        if key == "shortener_enabled" and not (client.cfg.get("shortener_enabled") or _shortener_configured()):
            await query.answer("Set SHORTENER_URL and SHORTENER_API first.", show_alert=True)
            return
        value = not bool(client.cfg.get(key))
        client.cfg[key] = value
        await client.db.set_runtime(key, value)
        await query.answer("Saved")
        await _show(message, settings_text(client.cfg), settings_markup(client.cfg))
        return

    if data.startswith("set:edit:"):
        key = data.split(":", 2)[2]
        if key not in EDITABLE or EDITABLE[key] == "bool":
            await query.answer("Use the button to toggle that.", show_alert=True)
            return
        set_state(user.id, flow="setting", key=key)
        await query.answer()
        hint = _edit_hint(key)
        await _show(message, hint, None)
        return

    if data.startswith("rq:"):
        _prefix, action, request_id = data.split(":")
        status = "done" if action == "done" else "rejected"
        text = await resolve_request(client, request_id, status)
        await query.answer("Updated")
        listing, markup = await render_requests(client)
        await _show(message, text + "\n\n" + listing, markup)
        return

    await query.answer("Unknown button.", show_alert=True)


def InlineKeyboardMarkup_back():
    from bot.ui import back_markup

    return back_markup("home")


def _shortener_configured() -> bool:
    from bot.config import settings

    return bool(settings.shortener_api and settings.shortener_url)


def _edit_hint(key: str) -> str:
    hints = {
        "start_message": "Send the new start message. Placeholders: {first} {last} {id} {mention} {username} {uptime}.",
        "start_pic": "Send a photo, a direct image URL, or /skip to remove the start picture.",
        "force_message": "Send the force-subscribe message. The same user placeholders work.",
        "custom_caption": "Send the caption template. Placeholders: {filename} {previouscaption} {filesize} {filetype}. /skip keeps the original caption.",
        "auto_delete_text": "Send the message people see after files are deleted. /skip restores a blank notice.",
        "user_reply_text": "Send the auto reply for ordinary text, or /skip to disable it.",
        "file_auto_delete": "Send how many seconds to keep delivered files. 0 disables auto-delete.",
    }
    return hints.get(key, "Send the new value.")
