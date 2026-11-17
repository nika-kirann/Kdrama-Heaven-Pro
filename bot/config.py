"""Environment-only configuration.

Every secret and Telegram or database id is read from the environment.
There are no baked-in tokens, hashes, or connection strings. Importing this
module exits the process with a clear message when a required variable is
missing or malformed.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


def _raw(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return ""


def _warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def _fail(errors: list[str]) -> None:
    print("ERROR: bot configuration is incomplete.", file=sys.stderr)
    for item in errors:
        print(f"  - {item}", file=sys.stderr)
    print(
        "Set the variables in the environment (see .env.example) and start again.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _parse_int(name: str, value: str, *, errors: list[str]) -> int:
    try:
        return int(value)
    except ValueError:
        errors.append(f"{name} must be an integer, got {value!r}")
        return 0


def _parse_bool(value: str, default: bool) -> bool:
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ForceChannel:
    chat_id: int
    request: bool
    label: str


def _parse_channel_list(value: str, *, request: bool, errors: list[str], name: str) -> list[ForceChannel]:
    items: list[ForceChannel] = []
    if not value or value == "0":
        return items
    for part in value.split(","):
        part = part.strip()
        if not part or part == "0":
            continue
        if "|" in part:
            id_text, label = part.split("|", 1)
        else:
            id_text, label = part, ""
        id_text = id_text.strip()
        if not id_text.lstrip("-").isdigit():
            errors.append(f"{name} entry {part!r} is not a chat id")
            continue
        items.append(ForceChannel(int(id_text), request, label.strip()))
    return items


def _parse_admins(value: str, owner_id: int, errors: list[str]) -> tuple[int, ...]:
    found = {owner_id}
    if not value:
        return tuple(sorted(found))
    for part in value.replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            errors.append(f"ADMINS entry {part!r} is not a user id")
            continue
        found.add(int(part))
    return tuple(sorted(found))


# Keys an admin can change at runtime. Values live in MongoDB and override env defaults.
EDITABLE = {
    "start_message": "text",
    "start_pic": "pic",
    "force_message": "text",
    "custom_caption": "text",
    "auto_delete_text": "text",
    "user_reply_text": "text",
    "file_auto_delete": "int",
    "protect_content": "bool",
    "disable_channel_button": "bool",
    "shortener_enabled": "bool",
}


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    database_url: str
    database_name: str
    owner_id: int
    channel_id: int
    log_channel: int
    port: int
    web_host: str
    workers: int
    admin_ids: tuple[int, ...]
    force_channels: tuple[ForceChannel, ...]
    file_auto_delete: int
    protect_content: bool
    disable_channel_button: bool
    start_pic: str
    start_message: str
    force_message: str
    custom_caption: str
    auto_delete_text: str
    user_reply_text: str
    shortener_api: str
    shortener_url: str
    verify_tutorial: str
    token_hours: int
    shortener_enabled: bool
    rate_limit: int
    rate_window: int
    premium_bypass_forcesub: bool
    premium_bypass_shortener: bool
    premium_bypass_autodelete: bool
    max_batch: int
    keep_alive_url: str
    auto_approve_join: bool
    new_user_log: bool

    def overlay(self) -> dict:
        """Runtime-editable values seeded from the environment."""
        return {
            "start_message": self.start_message,
            "start_pic": self.start_pic,
            "force_message": self.force_message,
            "custom_caption": self.custom_caption,
            "auto_delete_text": self.auto_delete_text,
            "user_reply_text": self.user_reply_text,
            "file_auto_delete": self.file_auto_delete,
            "protect_content": self.protect_content,
            "disable_channel_button": self.disable_channel_button,
            "shortener_enabled": self.shortener_enabled,
        }


def load_settings() -> Settings:
    errors: list[str] = []

    api_id_raw = _raw("API_ID")
    api_hash = _raw("API_HASH")
    bot_token = _raw("BOT_TOKEN")
    database_url = _raw("DATABASE_URL", "DB_URL", "MONGO_URL")
    owner_raw = _raw("OWNER_ID")
    channel_raw = _raw("CHANNEL_ID")

    required = {
        "API_ID": api_id_raw,
        "API_HASH": api_hash,
        "BOT_TOKEN": bot_token,
        "DATABASE_URL": database_url,
        "OWNER_ID": owner_raw,
        "CHANNEL_ID": channel_raw,
    }
    for name, value in required.items():
        if not value:
            errors.append(f"{name} is required and has no value")

    api_id = _parse_int("API_ID", api_id_raw, errors=errors) if api_id_raw else 0
    owner_id = _parse_int("OWNER_ID", owner_raw, errors=errors) if owner_raw else 0
    channel_id = _parse_int("CHANNEL_ID", channel_raw, errors=errors) if channel_raw else 0

    if api_hash and (len(api_hash) < 8 or " " in api_hash):
        errors.append("API_HASH does not look like a Telegram API hash")
    if bot_token and ":" not in bot_token:
        errors.append("BOT_TOKEN must look like a BotFather token (it contains a colon)")
    if database_url and not (
        database_url.startswith("mongodb://") or database_url.startswith("mongodb+srv://")
    ):
        errors.append("DATABASE_URL must start with mongodb:// or mongodb+srv://")
    if api_id and api_id <= 0:
        errors.append("API_ID must be a positive integer")
    if owner_id and owner_id <= 0:
        errors.append("OWNER_ID must be a positive Telegram user id")

    port_raw = _raw("PORT") or "8000"
    port = _parse_int("PORT", port_raw, errors=errors)
    if port_raw and not (1 <= port <= 65535):
        errors.append("PORT must be between 1 and 65535")

    workers_raw = _raw("WORKERS", "TG_BOT_WORKERS") or "8"
    workers = _parse_int("WORKERS", workers_raw, errors=errors)
    if workers_raw and not (1 <= workers <= 128):
        errors.append("WORKERS must be between 1 and 128")

    log_raw = _raw("LOG_CHANNEL")
    log_channel = owner_id
    if log_raw and log_raw != "0":
        log_channel = _parse_int("LOG_CHANNEL", log_raw, errors=errors)

    delete_raw = _raw("FILE_AUTO_DELETE") or "600"
    file_auto_delete = _parse_int("FILE_AUTO_DELETE", delete_raw, errors=errors)
    if file_auto_delete < 0:
        errors.append("FILE_AUTO_DELETE cannot be negative (use 0 to disable)")
    if file_auto_delete > 7 * 24 * 3600:
        _warn("FILE_AUTO_DELETE is above 7 days; clamping to 604800 seconds")
        file_auto_delete = 7 * 24 * 3600

    batch_raw = _raw("MAX_BATCH") or "50"
    max_batch = _parse_int("MAX_BATCH", batch_raw, errors=errors)
    if batch_raw and not (1 <= max_batch <= 200):
        errors.append("MAX_BATCH must be between 1 and 200")

    rate_limit = _parse_int("RATE_LIMIT", _raw("RATE_LIMIT") or "8", errors=errors)
    rate_window = _parse_int("RATE_WINDOW", _raw("RATE_WINDOW") or "30", errors=errors)
    if rate_limit < 1:
        errors.append("RATE_LIMIT must be at least 1")
    if rate_window < 1:
        errors.append("RATE_WINDOW must be at least 1 second")

    token_hours = _parse_int("TOKEN_HOURS", _raw("TOKEN_HOURS") or "12", errors=errors)
    if not (1 <= token_hours <= 168):
        errors.append("TOKEN_HOURS must be between 1 and 168")

    admin_ids = _parse_admins(_raw("ADMINS"), owner_id, errors) if owner_id else tuple()

    normal = _parse_channel_list(
        _raw("FORCE_SUB_CHANNELS", "FORCE_SUB_CHANNEL"),
        request=False,
        errors=errors,
        name="FORCE_SUB_CHANNELS",
    )
    requests = _parse_channel_list(
        _raw("FORCE_SUB_REQUEST_CHANNELS"),
        request=True,
        errors=errors,
        name="FORCE_SUB_REQUEST_CHANNELS",
    )
    merged: dict[int, ForceChannel] = {}
    for item in normal:
        merged[item.chat_id] = item
    for item in requests:
        previous = merged.get(item.chat_id)
        label = item.label or (previous.label if previous else "")
        merged[item.chat_id] = ForceChannel(item.chat_id, True, label)
    if channel_id and channel_id in merged:
        errors.append("CHANNEL_ID (database channel) cannot also be a force-subscribe channel")

    shortener_api = _raw("SHORTENER_API")
    shortener_url = _raw("SHORTENER_URL")
    shortener_default = bool(shortener_api and shortener_url)
    shortener_enabled = _parse_bool(_raw("SHORTENER_ENABLED"), shortener_default)
    if shortener_enabled and not (shortener_api and shortener_url):
        _warn("SHORTENER_ENABLED is on but SHORTENER_URL or SHORTENER_API is empty; verification stays off")
        shortener_enabled = False

    keep_alive = _raw("KEEP_ALIVE_URL")
    if keep_alive and not (keep_alive.startswith("https://") or keep_alive.startswith("http://")):
        errors.append("KEEP_ALIVE_URL must start with http:// or https://")

    if channel_id and channel_id > 0:
        _warn("CHANNEL_ID is positive. Private channels use an id like -100xxxxxxxxxx")

    if errors:
        _fail(errors)

    start_message = _raw("START_MESSAGE") or (
        "Hello {mention}\n\n"
        "I keep files in a private channel and hand them out through special links.\n"
        "Use /search to look through the library, or /request if something is missing.\n\n"
        "Your id: <code>{id}</code>"
    )
    force_message = _raw("FORCE_SUB_MESSAGE") or (
        "Hello {mention}\n\n"
        "<b>Join the channel below to use this bot, then tap Try again.</b>"
    )
    auto_delete_text = _raw("AUTO_DELETE_MESSAGE") or (
        "These files were deleted from this chat.\n\n"
        "Next time, save them to Saved Messages before the timer ends. "
        "Tap <b>Get again</b> if this link is still valid."
    )

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        database_url=database_url,
        database_name=_raw("DATABASE_NAME", "DB_NAME") or "kdrama",
        owner_id=owner_id,
        channel_id=channel_id,
        log_channel=log_channel,
        port=port,
        web_host=_raw("WEB_HOST") or "0.0.0.0",
        workers=workers,
        admin_ids=admin_ids,
        force_channels=tuple(merged.values()),
        file_auto_delete=file_auto_delete,
        protect_content=_parse_bool(_raw("PROTECT_CONTENT"), False),
        disable_channel_button=_parse_bool(_raw("DISABLE_CHANNEL_BUTTON"), False),
        start_pic=_raw("START_PIC"),
        start_message=start_message,
        force_message=force_message,
        custom_caption=_raw("CUSTOM_CAPTION"),
        auto_delete_text=auto_delete_text,
        user_reply_text=_raw("USER_REPLY_TEXT"),
        shortener_api=shortener_api,
        shortener_url=shortener_url,
        verify_tutorial=_raw("VERIFY_TUTORIAL"),
        token_hours=token_hours,
        shortener_enabled=shortener_enabled,
        rate_limit=rate_limit,
        rate_window=rate_window,
        premium_bypass_forcesub=_parse_bool(_raw("PREMIUM_BYPASS_FORCESUB"), True),
        premium_bypass_shortener=_parse_bool(_raw("PREMIUM_BYPASS_SHORTENER"), True),
        premium_bypass_autodelete=_parse_bool(_raw("PREMIUM_BYPASS_AUTODELETE"), True),
        max_batch=max_batch,
        keep_alive_url=keep_alive,
        auto_approve_join=_parse_bool(_raw("AUTO_APPROVE_JOIN"), False),
        new_user_log=_parse_bool(_raw("NEW_USER_LOG"), True),
    )


settings = load_settings()


def is_admin(user_id: int) -> bool:
    return int(user_id) in settings.admin_ids


def is_owner(user_id: int) -> bool:
    return int(user_id) == settings.owner_id
