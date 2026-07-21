"""Minimal HTTP liveness, readiness, and Prometheus endpoints."""

from __future__ import annotations

import asyncio

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .metrics import ServerMetrics


class HealthHTTPServer:
    def __init__(self, metrics: ServerMetrics, ready: asyncio.Event) -> None:
        self._metrics = metrics
        self._ready = ready
        self._runner: web.AppRunner | None = None

    async def start(self, host: str, port: int) -> None:
        application = web.Application(client_max_size=1024)
        application.router.add_get("/healthz", self._health)
        application.router.add_get("/readyz", self._readiness)
        application.router.add_get("/metrics", self._prometheus)
        self._runner = web.AppRunner(application, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, host=host, port=port).start()

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response({"status": "alive"})

    async def _readiness(self, request: web.Request) -> web.Response:
        del request
        if not self._ready.is_set():
            return web.json_response({"status": "not-ready"}, status=503)
        return web.json_response({"status": "ready"})

    async def _prometheus(self, request: web.Request) -> web.Response:
        del request
        return web.Response(
            body=generate_latest(self._metrics.registry),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )
