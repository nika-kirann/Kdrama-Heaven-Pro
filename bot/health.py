"""Tiny HTTP server so a platform health check can reach the process."""

from __future__ import annotations

import logging

from aiohttp import web

log = logging.getLogger("kdrama.health")

STATE = {"bot": "starting"}


def set_bot_status(status: str) -> None:
    STATE["bot"] = status


async def _health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "bot": STATE["bot"]})


async def _root(_request: web.Request) -> web.Response:
    return web.Response(text="Kdrama Heaven file bot is running.\n", content_type="text/plain")


async def start_health_server(host: str, port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _health)
    app.router.add_get("/healthz", _health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("health server listening on %s:%s", host, port)
    return runner
