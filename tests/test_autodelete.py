"""Auto-delete: env name, Mongo override, Kurigram delete_messages, and restart resume."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta
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
        "FILE_AUTO_DELETE": "600",
        "ADMINS": "",
        "FORCE_SUB_CHANNELS": "",
        "FORCE_SUB_REQUEST_CHANNELS": "",
    }
)

from pyrogram import raw  # noqa: E402
from pyrogram.methods.messages.delete_messages import DeleteMessages  # noqa: E402

from bot.config import is_admin, load_settings  # noqa: E402
from bot.database import apply_runtime, autodelete_source  # noqa: E402
from bot.delivery import deliver_token, resume_autodeletes  # noqa: E402
from bot.delivery import asyncio as delivery_asyncio  # noqa: E402
from bot.plugins.store import _reply_link  # noqa: E402
from bot.utils import auto_delete_label, utcnow  # noqa: E402


def test_env_name_matches_docs() -> None:
    loaded = load_settings()
    assert loaded.file_auto_delete == 600
    os.environ["FILE_AUTO_DELETE"] = "90"
    try:
        assert load_settings().file_auto_delete == 90
    finally:
        os.environ["FILE_AUTO_DELETE"] = "600"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "FILE_AUTO_DELETE" in readme
    assert "FILE_AUTO_DELETE=600" in example
    assert auto_delete_label(600) == "10 min"
    assert auto_delete_label(0) == "off"


def test_mongo_zero_shadows_env() -> None:
    base = {"file_auto_delete": 600, "protect_content": False, "disable_channel_button": False, "shortener_enabled": False}
    assert apply_runtime(base, {})["file_auto_delete"] == 600
    assert apply_runtime(base, {"file_auto_delete": None})["file_auto_delete"] == 600
    assert apply_runtime(base, {"file_auto_delete": 0})["file_auto_delete"] == 0
    assert apply_runtime(base, {"file_auto_delete": "0"})["file_auto_delete"] == 0
    assert autodelete_source({}) == "env"
    assert autodelete_source({"file_auto_delete": 0}) == "mongo"
    assert autodelete_source({"start_message": "hi"}) == "env"


class Out:
    def __init__(self, message_id: int, app: "App"):
        self.id = message_id
        self.app = app

    async def delete(self):
        self.app.removed.append(self.id)

    async def edit_text(self, text, **_kwargs):
        self.app.edits.append(text)


class MemDB:
    def __init__(self):
        self.rows: list[dict] = []
        self.links: dict = {}
        self.premium: set[int] = set()

    async def is_premium(self, user_id: int) -> bool:
        return user_id in self.premium

    async def consume_link(self, token: str):
        return self.links[token], "ok"

    async def peek_link(self, token: str, enforce_uses: bool = True):
        return self.links[token], "ok"

    async def release_use(self, _token: str) -> None:
        return None

    async def add_deletion(self, doc: dict) -> dict:
        self.rows.append(doc)
        return doc

    async def pending_deletions(self) -> list[dict]:
        return list(self.rows)

    async def mark_files_deleted(self, deletion_id: str) -> None:
        for row in self.rows:
            if row["_id"] == deletion_id:
                row["files_deleted"] = True

    async def finish_deletion(self, deletion_id: str) -> None:
        self.rows = [row for row in self.rows if row["_id"] != deletion_id]


class App:
    def __init__(self, seconds: int = 600):
        self.db = MemDB()
        self.cfg = {
            "file_auto_delete": seconds,
            "protect_content": False,
            "custom_caption": "",
            "shortener_enabled": False,
            "auto_delete_text": "Files deleted. {mention}",
        }
        self.force_channels: list = []
        self.db_channel = SimpleNamespace(id=-100123)
        self.limiter = SimpleNamespace(allow=lambda _user_id: True)
        self.sent: list[tuple] = []
        self.deleted: list[tuple] = []
        self.removed: list[int] = []
        self.edits: list[str] = []
        self._next = 0
        self.source = _source()

    async def send_message(self, chat_id, text, **kwargs):
        self._next += 1
        msg = Out(self._next, self)
        self.sent.append((chat_id, text, kwargs, msg.id))
        return msg

    async def delete_messages(self, chat_id, message_ids, revoke=True, is_scheduled=None):
        ids = [message_ids] if isinstance(message_ids, int) else list(message_ids)
        self.deleted.append((chat_id, ids, revoke, is_scheduled))
        return len(ids)

    async def get_messages(self, _chat_id, _ids):
        return [self.source]


def _source():
    media = SimpleNamespace(file_name="ep.mkv", file_size=2048, mime_type="video/mp4", file_unique_id="u")

    class Source:
        id = 5
        empty = False
        document = media
        video = audio = voice = video_note = animation = sticker = photo = None
        caption = None
        html_text = ""
        text = None

        async def copy(self, **_kwargs):
            return SimpleNamespace(id=100)

    return Source()


def _user(user_id: int):
    return SimpleNamespace(
        id=user_id,
        first_name="Owner" if user_id == 42 else "Fan",
        last_name="",
        username="own" if user_id == 42 else "fan",
        mention=f'<a href="tg://user?id={user_id}">user</a>',
    )


def _install_clock():
    slept: list[float] = []
    tasks: list[asyncio.Task] = []
    original_sleep = asyncio.sleep
    original_create = asyncio.create_task

    async def fast_sleep(seconds=0, result=None):
        slept.append(float(seconds))
        await original_sleep(0)
        return result

    def capture(coro, *args, **kwargs):
        task = original_create(coro, *args, **kwargs)
        tasks.append(task)
        return task

    delivery_asyncio.sleep = fast_sleep
    delivery_asyncio.create_task = capture
    return slept, tasks


async def _settle(tasks: list[asyncio.Task]) -> None:
    await asyncio.sleep(0)
    if tasks:
        await asyncio.gather(*tasks)


def test_owner_delivery_deletes_and_get_again_survives_restart() -> None:
    assert is_admin(42)

    async def run() -> None:
        slept, tasks = _install_clock()
        app = App(600)
        token = "fDELIVER1"
        app.db.links[token] = {
            "_id": token,
            "message_ids": [5],
            "protect": False,
            "max_uses": 0,
            "uses": 0,
            "revoked": False,
            "kind": "single",
        }
        user = _user(42)
        await deliver_token(app, user, 42, token, count_use=True)
        assert app.db.rows, "owner delivery did not schedule auto-delete"
        saved = app.db.rows[0]
        assert saved["chat_id"] == 42
        assert saved["message_ids"] == [100]
        assert (saved["due_at"] - utcnow()).total_seconds() > 590
        warning = next(text for _chat, text, _kw, _id in app.sent if "deleted in" in text)
        assert "10m" in warning
        await _settle(tasks)
        assert any(sleep >= 590 for sleep in slept)
        assert app.deleted, "delete_messages was not called"
        chat_id, ids, revoke, _scheduled = app.deleted[0]
        assert chat_id == 42
        assert revoke is True
        assert 100 in ids
        assert saved["warning_id"] in ids
        again = next(item for item in app.sent if item[2].get("reply_markup") is not None and "deleted in" not in item[1])
        button = again[2]["reply_markup"].inline_keyboard[0][0]
        assert button.text == "Get again"
        assert button.callback_data == f"again:{token}"
        assert app.db.rows == []

        # The button calls deliver_token again and must schedule a fresh timer.
        tasks.clear()
        await deliver_token(app, user, 42, token, count_use=False)
        assert len(app.db.rows) == 1
        await _settle(tasks)
        assert app.db.rows == []

        # A due row left behind by a killed process is resumed from MongoDB.
        overdue = {
            "_id": "dOVERDUE",
            "chat_id": 42,
            "message_ids": [7],
            "warning_id": 8,
            "token": token,
            "due_at": utcnow() - timedelta(seconds=5),
            "user_id": 42,
            "first_name": "Owner",
            "last_name": "",
            "username": "own",
            "files_deleted": False,
        }
        app.db.rows.append(overdue)
        tasks.clear()
        before = len(app.deleted)
        await resume_autodeletes(app)
        await _settle(tasks)
        assert len(app.deleted) == before + 1
        assert app.deleted[-1][1] == [7, 8]
        assert app.db.rows == []

    asyncio.run(run())


def test_zero_and_premium_do_not_schedule() -> None:
    async def run() -> None:
        _install_clock()
        app = App(0)
        token = "fZERO"
        app.db.links[token] = {"_id": token, "message_ids": [5], "protect": False, "revoked": False}
        await deliver_token(app, _user(99), 99, token, count_use=True)
        assert app.db.rows == []

        app.cfg["file_auto_delete"] = 600
        app.db.premium.add(99)
        await deliver_token(app, _user(99), 99, token, count_use=True)
        assert app.db.rows == []

    asyncio.run(run())


def test_store_card_shows_timer() -> None:
    async def run() -> None:
        seen = {}

        class Message:
            async def reply_text(self, text, **_kwargs):
                seen["text"] = text

        class DB:
            async def get_link(self, _token):
                return {
                    "kind": "single",
                    "message_ids": [1],
                    "protect": False,
                    "max_uses": 0,
                    "uses": 0,
                    "expires_at": None,
                }

        client = SimpleNamespace(cfg={"file_auto_delete": 600}, username="storebot", db=DB())
        await _reply_link(Message(), client, "fCARD")
        assert "Auto-delete: <b>10 min</b>" in seen["text"]
        assert "Expires:" in seen["text"]

        client.cfg["file_auto_delete"] = 0
        await _reply_link(Message(), client, "fCARD")
        assert "Auto-delete: <b>off</b>" in seen["text"]

    asyncio.run(run())


def test_real_delete_messages_accepts_id_list() -> None:
    async def run() -> None:
        class Stub:
            def __init__(self):
                self.query = None

            async def resolve_peer(self, chat_id):
                return raw.types.InputPeerUser(user_id=int(chat_id), access_hash=1)

            async def invoke(self, query):
                self.query = query

                class Result:
                    pts_count = 2

                return Result()

        stub = Stub()
        deleted = await DeleteMessages.delete_messages(stub, 77, [11, 12])
        assert deleted == 2
        assert list(stub.query.id) == [11, 12]
        assert stub.query.revoke is True

    asyncio.run(run())


def main() -> None:
    test_env_name_matches_docs()
    test_mongo_zero_shadows_env()
    test_owner_delivery_deletes_and_get_again_survives_restart()
    test_zero_and_premium_do_not_schedule()
    test_store_card_shows_timer()
    test_real_delete_messages_accepts_id_list()
    print("auto-delete tests passed")


if __name__ == "__main__":
    main()
