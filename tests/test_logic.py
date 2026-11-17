"""Pure tests. They do not call Telegram or MongoDB."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

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
        "ADMINS": "7, 8",
        "FORCE_SUB_CHANNELS": "-1001|Updates",
        "FORCE_SUB_REQUEST_CHANNELS": "-1001,-1002|Requests",
    }
)

from bot.config import load_settings  # noqa: E402
from bot.utils import (  # noqa: E402
    RateLimiter,
    extract_message_id,
    human_duration,
    message_range,
    parse_duration,
    parse_limits,
    render_caption,
)


def test_settings_shape() -> None:
    loaded = load_settings()
    assert loaded.api_id == 12345
    assert loaded.owner_id == 42
    assert loaded.admin_ids == (7, 8, 42)
    by_id = {item.chat_id: item for item in loaded.force_channels}
    assert by_id[-1001].request is True
    assert by_id[-1001].label == "Updates"
    assert by_id[-1002].request is True
    assert by_id[-1002].label == "Requests"
    assert loaded.database_url.startswith("mongodb://")


def test_db_url_alias(monkey_env: dict) -> None:
    os.environ.pop("DATABASE_URL", None)
    os.environ["DB_URL"] = "mongodb+srv://" + "user:pass@" + "cluster.example.mongodb.net"
    loaded = load_settings()
    assert loaded.database_url.startswith("mongodb+srv://")
    os.environ["DATABASE_URL"] = monkey_env["DATABASE_URL"]
    os.environ.pop("DB_URL", None)


def test_rejects_bad_values() -> None:
    previous = os.environ["BOT_TOKEN"]
    os.environ["BOT_TOKEN"] = "not-a-token"
    try:
        try:
            load_settings()
        except SystemExit:
            pass
        else:
            raise AssertionError("bad BOT_TOKEN should exit")
    finally:
        os.environ["BOT_TOKEN"] = previous

    os.environ["CHANNEL_ID"] = "-1001"
    os.environ["FORCE_SUB_CHANNELS"] = "-1001"
    try:
        try:
            load_settings()
        except SystemExit:
            pass
        else:
            raise AssertionError("database channel cannot also be force-sub")
    finally:
        os.environ["CHANNEL_ID"] = "-100123"
        os.environ["FORCE_SUB_CHANNELS"] = "-1001|Updates"


def test_missing_env_lists_names() -> None:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [sys.executable, "-c", "import bot.config"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    for name in ("API_ID", "API_HASH", "BOT_TOKEN", "DATABASE_URL", "OWNER_ID", "CHANNEL_ID"):
        assert name in completed.stderr
    assert "mongodb+srv://" not in completed.stderr
    assert "AA" not in completed.stderr


def test_durations_and_ranges() -> None:
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("2h") == timedelta(hours=2)
    expiry, uses = parse_limits("0 0")
    assert expiry is None and uses == 0
    expiry, uses = parse_limits("24h 10")
    assert uses == 10 and expiry is not None
    assert message_range(1, 3, 10) == [1, 2, 3]
    assert message_range(5, 3, 10) == [5, 4, 3]
    try:
        message_range(1, 20, 5)
    except ValueError:
        pass
    else:
        raise AssertionError("oversized range should fail")
    assert human_duration(90) == "1m 30s"


def test_link_parse_and_caption() -> None:
    class Origin:
        chat = type("C", (), {"id": -100123456})()
        message_id = 15

    class Forwarded:
        forward_origin = Origin()
        text = ""
        caption = None

    assert extract_message_id(Forwarded(), -100123456, None) == 15

    class Linked:
        forward_origin = None
        text = "see https://t.me/c/123456/9"
        caption = None

    assert extract_message_id(Linked(), -100123456, "ignored") == 9

    class Media:
        file_name = "a<b>.mkv"
        file_size = 2048
        mime_type = "video/mp4"
        file_unique_id = "x"

    class Msg:
        document = Media()
        video = audio = voice = video_note = animation = sticker = photo = None
        caption = "hello"
        html_text = "hello"

    rendered = render_caption(Msg(), "{filename} | {previouscaption} | {filesize}")
    assert rendered is not None
    assert "a&lt;b&gt;.mkv" in rendered
    assert "hello" in rendered


def test_rate_limiter() -> None:
    limiter = RateLimiter(2, 60)
    assert limiter.allow(1) is True
    assert limiter.allow(1) is True
    assert limiter.allow(1) is False
    assert limiter.allow(2) is True


def test_sources_have_no_live_secrets() -> None:
    token = re.compile(r"\d{8,}:[A-Za-z0-9_-]{30,}")
    srv = re.compile(r"mongodb\+srv://[^:\s]+:[^@\s]+@")
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "__pycache__", ".venv", "venv"} for part in path.parts):
            continue
        if path.suffix not in {".py", ".md", ".txt", ".example", ""} and path.name not in {
            "Dockerfile",
            ".env.example",
            ".gitignore",
            ".dockerignore",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert token.search(text) is None, path
        for line in text.splitlines():
            if any(word in line for word in ("USER", "PASSWORD", "example", "localhost")):
                continue
            assert srv.search(line) is None, (path, line)


def test_plugins_import_and_register() -> None:
    from bot.client import FileStoreBot

    bot = FileStoreBot()
    bot.load_plugins()
    total = sum(len(handlers) for handlers in bot.dispatcher.groups.values())
    assert total >= 15, total
    assert -2 in bot.dispatcher.groups
    assert -1 in bot.dispatcher.groups


def test_health_server() -> None:
    import asyncio

    import aiohttp

    from bot.health import start_health_server

    async def run() -> None:
        runner = await start_health_server("127.0.0.1", 8765)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8765/health") as response:
                    body = await response.read()
                    assert response.status == 200
                    assert b'"status": "ok"' in body
                async with session.get("http://127.0.0.1:8765/") as response:
                    assert response.status == 200
                    assert b"running" in await response.read()
                async with session.get("http://127.0.0.1:8765/healthz") as response:
                    assert response.status == 200
        finally:
            await runner.cleanup()

    asyncio.run(run())


def main() -> None:
    saved = {
        "DATABASE_URL": os.environ["DATABASE_URL"],
        "BOT_TOKEN": os.environ["BOT_TOKEN"],
        "CHANNEL_ID": os.environ["CHANNEL_ID"],
        "FORCE_SUB_CHANNELS": os.environ["FORCE_SUB_CHANNELS"],
    }
    test_settings_shape()
    test_db_url_alias(saved)
    test_rejects_bad_values()
    test_missing_env_lists_names()
    test_durations_and_ranges()
    test_link_parse_and_caption()
    test_rate_limiter()
    test_sources_have_no_live_secrets()
    test_plugins_import_and_register()
    test_health_server()
    print("logic tests passed")


if __name__ == "__main__":
    main()
