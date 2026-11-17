"""Lock bot call sites to the installed Kurigram signatures.

Kurigram 2.2.27 removed ``quote`` and ``disable_web_page_preview`` from
``Message.reply`` / ``Message.edit_text``. This test reads the real methods
and fails if any bot call passes a keyword those methods do not accept.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

os.environ.update(
    {
        "API_ID": "12345",
        "API_HASH": "a" * 32,
        "BOT_TOKEN": "123456:TEST_TOKEN_NOT_REAL",
        "DATABASE_URL": "mongodb://localhost:27017",
        "OWNER_ID": "42",
        "CHANNEL_ID": "-100123",
    }
)

import pyrogram  # noqa: E402
from pyrogram import Client  # noqa: E402
from pyrogram.types import LinkPreviewOptions, Message  # noqa: E402

from bot.plugins.admin import admins_command, settings_command  # noqa: E402
from bot.plugins.callbacks import _show  # noqa: E402
from bot.plugins.misc import user_media  # noqa: E402
from bot.plugins.start import _welcome  # noqa: E402
from bot.utils import utcnow  # noqa: E402

PINNED = "2.2.27"

# Removed or deprecated on the methods that actually crashed in production.
# Reject them even when a lower-level Client method still lists the old name.
FORBIDDEN = {
    "quote",
    "disable_web_page_preview",
    "reply_to_message_id",
    "reply_to_chat_id",
    "reply_to_story_id",
    "quote_text",
    "quote_entities",
    "quote_offset",
}

METHODS = {
    "reply": Message.reply,
    "reply_text": Message.reply_text,
    "reply_photo": Message.reply_photo,
    "edit": Message.edit,
    "edit_text": Message.edit_text,
    "edit_caption": Message.edit_caption,
    "edit_reply_markup": Message.edit_reply_markup,
    "copy": Message.copy,
    "send_message": Client.send_message,
    "send_photo": Client.send_photo,
    "edit_message_text": Client.edit_message_text,
    "edit_message_caption": Client.edit_message_caption,
    "copy_message": Client.copy_message,
    "pin_chat_message": Client.pin_chat_message,
    "delete_messages": Client.delete_messages,
    "get_messages": Client.get_messages,
}


def _params(func) -> set[str]:
    return {name for name in inspect.signature(func).parameters if name != "self"}


def test_pinned_version() -> None:
    assert pyrogram.__version__ == PINNED
    reply_params = _params(Message.reply)
    assert "link_preview_options" in reply_params
    assert "quote" not in reply_params
    assert "disable_web_page_preview" not in reply_params
    edit_params = _params(Message.edit_text)
    assert "link_preview_options" in edit_params
    assert "disable_web_page_preview" not in edit_params
    assert "quote" not in _params(Message.reply_photo)


def test_call_sites_match_signatures() -> None:
    problems: list[str] = []
    for path in sorted((ROOT / "bot").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            func = METHODS.get(node.func.attr)
            if func is None:
                continue
            allowed = _params(func)
            for keyword in node.keywords:
                if keyword.arg is None:
                    continue
                location = f"{path.relative_to(ROOT)}:{keyword.lineno}"
                if keyword.arg in FORBIDDEN:
                    problems.append(f"{location} passes removed kwarg {keyword.arg} to {node.func.attr}")
                elif keyword.arg not in allowed:
                    problems.append(
                        f"{location} {node.func.attr}() has no parameter {keyword.arg}"
                    )
    if problems:
        raise AssertionError("Kurigram call sites drifted:\n" + "\n".join(problems))


def _message(recorder, user_id: int = 7):
    user = SimpleNamespace(id=user_id, first_name="Ada", last_name="", username=None, mention="Ada")
    message = SimpleNamespace(
        id=10,
        chat=SimpleNamespace(id=user_id),
        from_user=user,
        message_thread_id=None,
        direct_messages_topic_id=None,
        business_connection_id=None,
        ephemeral_message_id=None,
        photo=None,
        video=None,
        document=None,
        animation=None,
        _client=recorder,
    )
    message.reply_text = Message.reply.__get__(message, Message)
    message.reply_photo = Message.reply_photo.__get__(message, Message)
    message.edit_text = Message.edit_text.__get__(message, Message)
    message.edit_caption = Message.edit_caption.__get__(message, Message)
    return message


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))
        return self

    async def send_photo(self, **kwargs):
        self.calls.append(("send_photo", kwargs))
        return self

    async def edit_message_text(self, **kwargs):
        self.calls.append(("edit_message_text", kwargs))
        return self

    async def edit_message_caption(self, **kwargs):
        self.calls.append(("edit_message_caption", kwargs))
        return self


def test_handlers_use_real_reply_methods() -> None:
    from bot.config import settings

    async def run() -> None:
        recorder = Recorder()
        message = _message(recorder)
        client = SimpleNamespace(cfg=settings.overlay(), started_at=utcnow())
        await _welcome(client, message)
        await user_media(client, message)
        await admins_command(client, message)
        await settings_command(client, message)
        await _show(message, "panel", None)

        kinds = [name for name, _ in recorder.calls]
        assert kinds.count("send_message") >= 4
        assert "edit_message_text" in kinds
        for name, kwargs in recorder.calls:
            assert "quote" not in kwargs
            assert "disable_web_page_preview" not in kwargs
            extra = set(kwargs) - _params(getattr(Client, name))
            assert not extra, (name, extra)
        welcome = recorder.calls[0][1]
        assert isinstance(welcome["link_preview_options"], LinkPreviewOptions)
        assert welcome["link_preview_options"].is_disabled is True
        assert welcome["reply_parameters"].message_id == 10

        try:
            await Message.reply(message, "nope", quote=True)
        except TypeError as exc:
            assert "quote" in str(exc)
        else:
            raise AssertionError("Message.reply still accepted quote")

        try:
            await Message.reply(message, "nope", disable_web_page_preview=True)
        except TypeError as exc:
            assert "disable_web_page_preview" in str(exc)
        else:
            raise AssertionError("Message.reply still accepted disable_web_page_preview")

    asyncio.run(run())


def main() -> None:
    test_pinned_version()
    test_call_sites_match_signatures()
    test_handlers_use_real_reply_methods()
    print("kurigram signature tests passed")


if __name__ == "__main__":
    main()
