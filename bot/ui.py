"""Keyboards and short HTML screens."""

from __future__ import annotations

import html

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

# Kurigram 2.2.27 dropped disable_web_page_preview on Message.reply and Message.edit_text.
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

from bot.config import EDITABLE, settings
from bot.utils import clip, human_duration


def welcome_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Search", callback_data="go:search"),
                InlineKeyboardButton("Request", callback_data="go:request"),
            ],
            [
                InlineKeyboardButton("Help", callback_data="go:help"),
                InlineKeyboardButton("About", callback_data="about"),
            ],
        ]
    )


def back_markup(target: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Close", callback_data="close"), InlineKeyboardButton("Back", callback_data=target)]]
    )


def forcesub_markup(channels: list[dict], username: str, payload: str) -> InlineKeyboardMarkup:
    rows = []
    for channel in channels:
        verb = "Request" if channel.get("request") else "Join"
        title = clip(channel.get("title") or "channel", 40)
        rows.append([InlineKeyboardButton(f"{verb} {title}", url=channel["url"])])
    again = payload or "joined"
    rows.append([InlineKeyboardButton("Try again", url=f"https://t.me/{username}?start={again}")])
    return InlineKeyboardMarkup(rows)


def share_markup(link: str) -> InlineKeyboardMarkup:
    from urllib.parse import quote

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Share link", url=f"https://t.me/share/url?url={quote(link, safe='')}")]]
    )


def again_markup(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Get again", callback_data=f"again:{token}")]])


def verify_markup(short_url: str, tutorial: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("Open verification link", url=short_url)]]
    if tutorial:
        rows.append([InlineKeyboardButton("How verification works", url=tutorial)])
    return InlineKeyboardMarkup(rows)


def panel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Stats", callback_data="panel:stats"),
                InlineKeyboardButton("Requests", callback_data="panel:requests"),
            ],
            [
                InlineKeyboardButton("Ban", callback_data="panel:ban"),
                InlineKeyboardButton("Unban", callback_data="panel:unban"),
            ],
            [
                InlineKeyboardButton("Premium", callback_data="panel:premium"),
                InlineKeyboardButton("Settings", callback_data="set:show"),
            ],
            [
                InlineKeyboardButton("Broadcast", callback_data="panel:broadcast"),
                InlineKeyboardButton("Restart", callback_data="panel:restart"),
            ],
            [InlineKeyboardButton("Close", callback_data="close")],
        ]
    )


def broadcast_markup(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Send", callback_data=f"bc:send:{token}"),
                InlineKeyboardButton("Send and pin", callback_data=f"bc:pin:{token}"),
            ],
            [InlineKeyboardButton("Cancel", callback_data=f"bc:no:{token}")],
        ]
    )


def settings_markup(cfg: dict) -> InlineKeyboardMarkup:
    rows = []
    labels = {
        "start_message": "Start message",
        "start_pic": "Start picture",
        "force_message": "Force-sub message",
        "custom_caption": "Custom caption",
        "auto_delete_text": "Delete notice",
        "user_reply_text": "Auto reply",
        "file_auto_delete": "Auto-delete timer",
        "protect_content": "Protect content",
        "disable_channel_button": "Hide channel button",
        "shortener_enabled": "Link verification",
    }
    for key, kind in EDITABLE.items():
        label = labels[key]
        if kind == "bool":
            state = "ON" if cfg.get(key) else "OFF"
            rows.append([InlineKeyboardButton(f"{label}: {state}", callback_data=f"set:toggle:{key}")])
        else:
            rows.append([InlineKeyboardButton(f"Edit {label}", callback_data=f"set:edit:{key}")])
    rows.append(
        [
            InlineKeyboardButton("Back", callback_data="panel:home"),
            InlineKeyboardButton("Close", callback_data="close"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def settings_text(cfg: dict) -> str:
    timer = int(cfg.get("file_auto_delete") or 0)
    timer_text = "off" if timer == 0 else human_duration(timer)
    caption = clip(cfg.get("custom_caption") or "(keep original caption)", 80)
    pic = "set" if cfg.get("start_pic") else "none"
    reply = "set" if cfg.get("user_reply_text") else "off"
    return (
        "<b>Runtime settings</b>\n"
        "Text changes are stored in MongoDB and override the environment until you edit them again.\n\n"
        f"Auto-delete: <b>{timer_text}</b>\n"
        f"Protect content: <b>{'on' if cfg.get('protect_content') else 'off'}</b>\n"
        f"Channel share button: <b>{'hidden' if cfg.get('disable_channel_button') else 'shown'}</b>\n"
        f"Link verification: <b>{'on' if cfg.get('shortener_enabled') else 'off'}</b>\n"
        f"Start picture: <b>{pic}</b>\n"
        f"Auto reply: <b>{reply}</b>\n"
        f"Caption template: <code>{html.escape(caption)}</code>"
    )


def pager(prefix: str, token: str, page: int, has_next: bool) -> list[InlineKeyboardButton]:
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("Prev", callback_data=f"{prefix}:{token}:{page - 1}"))
    if has_next:
        buttons.append(InlineKeyboardButton("Next", callback_data=f"{prefix}:{token}:{page + 1}"))
    return buttons


def help_text(admin: bool) -> str:
    base = (
        "<b>How to get files</b>\n"
        "Open a link shared by an admin, or use /search and /anime to browse what is stored.\n"
        "Use /request <code>title</code> when a drama is missing.\n"
        "/id shows your Telegram id. /me shows premium and verification status. /uptime shows the process uptime.\n"
    )
    if not admin:
        return base
    return base + (
        "\n<b>Admin</b>\n"
        "Send a file here and the bot stores it in the database channel, indexes the name, and replies with a link.\n"
        "/genlink makes a link for one post. /batch covers a range. /cbatch collects specific posts. "
        "/rbatch is a protected batch.\n"
        "After you pick the posts, send limits like <code>24h 10</code> (expires in 24 hours, 10 opens) "
        "or <code>0 0</code> / /skip for no limit.\n"
        "/panel opens buttons for stats, bans, premium, settings, requests, broadcast, and restart.\n"
        "/broadcast as a reply to a message asks before sending. /delink revokes a token. /cancel aborts a wizard."
    )


def about_text(username: str, version: str, library: str) -> str:
    return (
        f"<b>Bot:</b> @{html.escape(username or 'bot')}\n"
        f"<b>Version:</b> {html.escape(version)}\n"
        f"<b>Library:</b> Kurigram (Pyrogram fork) {html.escape(library)}\n"
        f"<b>Owner id:</b> <code>{settings.owner_id}</code>\n"
        "Files stay in the database channel. Links only copy them into this chat."
    )


def file_button_label(doc: dict) -> str:
    size = doc.get("file_size") or 0
    from bot.utils import human_size

    name = clip(doc.get("file_name") or "file", 40)
    return clip(f"{name} ({human_size(size)})", 60)
