from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


BACKPRESSURE_SCHEMA = "mapi_http_backpressure.v1"
DEFAULT_MAX_IN_FLIGHT_POSTS = 16
DEFAULT_RETRY_AFTER_SECONDS = 1
DEFAULT_KEEPALIVE_SECONDS = 30


@dataclass
class BackpressureState:
    max_in_flight_posts: int = DEFAULT_MAX_IN_FLIGHT_POSTS
    retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS
    keepalive_seconds: int = DEFAULT_KEEPALIVE_SECONDS
    active_posts: int = 0
    max_observed_posts: int = 0
    accepted_total: int = 0
    rejected_total: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self.active_posts >= self.max_in_flight_posts:
                self.rejected_total += 1
                return False
            self.active_posts += 1
            self.accepted_total += 1
            self.max_observed_posts = max(self.max_observed_posts, self.active_posts)
            return True

    async def release(self) -> None:
        async with self._lock:
            self.active_posts = max(0, self.active_posts - 1)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": BACKPRESSURE_SCHEMA,
            "max_in_flight_posts": int(self.max_in_flight_posts),
            "retry_after_seconds": int(self.retry_after_seconds),
            "keepalive_seconds": int(self.keepalive_seconds),
            "active_posts": int(self.active_posts),
            "max_observed_posts": int(self.max_observed_posts),
            "accepted_total": int(self.accepted_total),
            "rejected_total": int(self.rejected_total),
        }


STATE = BackpressureState()


def configure_backpressure_from_env() -> BackpressureState:
    STATE.max_in_flight_posts = max(
        1,
        int(os.environ.get("MAPI_HTTP_MAX_IN_FLIGHT_POSTS", str(DEFAULT_MAX_IN_FLIGHT_POSTS))),
    )
    STATE.retry_after_seconds = max(
        1,
        int(os.environ.get("MAPI_HTTP_RETRY_AFTER_SECONDS", str(DEFAULT_RETRY_AFTER_SECONDS))),
    )
    STATE.keepalive_seconds = max(
        1,
        int(os.environ.get("MAPI_HTTP_KEEPALIVE_SECONDS", str(DEFAULT_KEEPALIVE_SECONDS))),
    )
    return STATE


def transport_status_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "transport": "http",
        "streamable_http": True,
        "stateful_session": True,
        "backpressure": STATE.snapshot(),
        "overload_contract": {
            "status_code": 429,
            "retry_after_header": "Retry-After",
            "retry_after_seconds": int(STATE.retry_after_seconds),
            "body_schema": BACKPRESSURE_SCHEMA,
        },
        "connection_reuse": {
            "server_keepalive_seconds": int(STATE.keepalive_seconds),
            "client_pooling": "client_managed",
        },
    }


class McpBackpressureMiddleware:
    """ASGI overload guard for MCP POST requests.

    GET/SSE requests are not counted because a stateful stream can remain open
    for a long time. Sequential POST calls are unaffected; only excess
    concurrent POSTs are rejected with an explicit HTTP 429 contract.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], *, state: BackpressureState | None = None):
        self.app = app
        self.state = state or STATE

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "http" or str(scope.get("method") or "").upper() != "POST":
            await self.app(scope, receive, send)
            return

        acquired = await self.state.try_acquire()
        if not acquired:
            body = json.dumps(
                {
                    "status": "error",
                    "error": "mapi_backpressure",
                    "schema": BACKPRESSURE_SCHEMA,
                    "retry_after_seconds": int(self.state.retry_after_seconds),
                },
                separators=(",", ":"),
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(self.state.retry_after_seconds).encode("ascii")),
                        (b"cache-control", b"no-store"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        try:
            await self.app(scope, receive, send)
        finally:
            await self.state.release()
