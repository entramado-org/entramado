"""Per-IP sliding-window rate limit for the public /mcp endpoint.

Pure in-memory ASGI middleware — the stock caddy:2 image has no rate limiting
(the plugin needs an xcaddy build), so the cap lives in the app. Single-process
uvicorn, friends-scale traffic: a dict of deques is plenty.
"""
from __future__ import annotations

import time
from collections import deque

import structlog

from .metrics import MCP_RATE_LIMITED

WINDOW_SECONDS = 60
MAX_REQUESTS = 30  # per IP per window
_CLEANUP_EVERY = 500  # requests between sweeps of idle IPs

_log = structlog.get_logger()


class RateLimitMiddleware:
    def __init__(self, app, max_requests: int = MAX_REQUESTS,
                 window: float = WINDOW_SECONDS):
        self.app = app
        self.max_requests = max_requests
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._since_cleanup = 0

    def _client_ip(self, scope) -> str:
        # Caddy sets X-Forwarded-For; the first hop is the real client. Direct
        # (dev) connections fall back to the socket peer.
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                return value.decode("latin-1").split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        now = time.monotonic()
        ip = self._client_ip(scope)
        hits = self._hits.setdefault(ip, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.max_requests:
            retry_after = max(1, int(self.window - (now - hits[0])) + 1)
            # Observability (spec 017): count the rejection (no `ip` label — it
            # would explode cardinality) and log it WITH the ip so the per-IP
            # drill-down is recoverable from Loki.
            MCP_RATE_LIMITED.inc()
            _log.warning("rate_limited", ip=ip, retry_after=retry_after)
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(retry_after).encode()),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"error":"rate_limited","detail":"Demasiadas peticiones; espera un momento."}',
            })
            return
        hits.append(now)
        self._since_cleanup += 1
        if self._since_cleanup >= _CLEANUP_EVERY:
            self._since_cleanup = 0
            stale = [k for k, v in self._hits.items()
                     if not v or now - v[-1] > self.window]
            for k in stale:
                del self._hits[k]
        await self.app(scope, receive, send)
