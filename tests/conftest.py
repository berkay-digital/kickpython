"""A fake Kick API (api + oauth on one aiohttp server) for offline tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from kickpython import KickClient, MemoryTokenStore

Reply = tuple[int, Any] | tuple[int, Any, dict[str, str]]
RouteHandler = Callable[[web.Request, bytes], Reply]


@dataclass
class Recorded:
    method: str
    path: str
    query: list[tuple[str, str]]
    headers: dict[str, str]
    body: bytes
    raw_path: str = ""

    @property
    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def form(self) -> dict[str, str]:
        from urllib.parse import parse_qsl

        return dict(parse_qsl(self.body.decode()))

    @property
    def bearer(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return auth.removeprefix("Bearer ") if auth else None


@dataclass
class FakeKick:
    server: TestServer
    routes: dict[tuple[str, str], list[RouteHandler | Reply]] = field(default_factory=dict)
    requests: list[Recorded] = field(default_factory=list)

    @property
    def base(self) -> str:
        return str(self.server.make_url("")).rstrip("/")

    def on(self, method: str, path: str, *replies: RouteHandler | Reply) -> None:
        """Queue replies for a route. The last one repeats."""
        self.routes[(method, path)] = list(replies)

    def calls(self, method: str, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and r.path == path]

    async def handle(self, request: web.Request) -> web.StreamResponse:
        body = await request.read()
        self.requests.append(
            Recorded(
                request.method,
                request.path,
                list(request.query.items()),
                dict(request.headers),
                body,
                request.raw_path,
            )
        )
        queue = self.routes.get((request.method, request.path))
        if not queue:
            return web.json_response({"message": "no fake route"}, status=599)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(reply):
            reply = reply(request, body)
        status, payload, *rest = reply
        headers = rest[0] if rest else {}
        if payload is None:
            return web.Response(status=status, headers=headers)
        if isinstance(payload, str):
            return web.Response(status=status, text=payload, headers=headers)
        return web.json_response(payload, status=status, headers=headers)


@pytest.fixture
async def fake() -> AsyncIterator[FakeKick]:
    app = web.Application()
    holder: dict[str, FakeKick] = {}

    async def dispatch(request: web.Request) -> web.StreamResponse:
        return await holder["fake"].handle(request)

    app.router.add_route("*", "/{tail:.*}", dispatch)
    server = TestServer(app)
    await server.start_server()
    fk = FakeKick(server)
    holder["fake"] = fk
    # An app token that is always available unless a test overrides it.
    fk.on(
        "POST",
        "/oauth/token",
        (200, {"access_token": "app-1", "expires_in": 3600, "token_type": "Bearer"}),
    )
    yield fk
    await server.close()


@pytest.fixture
async def client(fake: FakeKick) -> AsyncIterator[KickClient]:
    kick = KickClient(
        "cid",
        "secret",
        token_store=MemoryTokenStore(),
        api_base=fake.base,
        oauth_base=fake.base,
        max_retries=2,
    )
    yield kick
    await kick.close()


def ok(data: Any, **extra: Any) -> tuple[int, Any]:
    return 200, {"data": data, "message": "OK", **extra}
