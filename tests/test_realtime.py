from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

from kickpython import ChannelRef, LiveChat, LiveChatMessage, RealtimeError
from kickpython.realtime import LiveUserBanned

CHAT_EVENT = {
    "id": "a6ef",
    "chatroom_id": 1653897,
    "content": "!ping",
    "type": "reply",
    "created_at": "2026-11-25T04:17:54+00:00",
    "sender": {
        "id": 1,
        "username": "Viewer",
        "slug": "viewer",
        "identity": {
            "color": "#FF9D00",
            "badges": [{"type": "moderator", "text": "Moderator"}],
            "badges_v2": [],
        },
    },
    "metadata": {
        "original_sender": {"id": 2, "username": "Other"},
        "original_message": {"id": "61fa", "content": "hi"},
        "message_ref": "1",
    },
    "thread_parent_id": "61fa",
}
BAN_EVENT = {
    "id": "b1",
    "user": {"id": 3, "username": "spammer", "slug": "spammer"},
    "banned_by": {"id": 0, "username": "moderator", "slug": "moderator"},
    "permanent": False,
    "duration": 10,
    "expires_at": "2026-11-25T04:28:26+00:00",
}
REF = ChannelRef(slug="berkaydigital", chatroom_id=1653897, channel_id=1661231, user_id=1710384)


class FakePusher:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.pongs = 0
        self.connections = 0

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        await ws.send_str("not json")  # junk must not kill the listener
        await ws.send_str("[1, 2]")
        await ws.send_str(
            json.dumps(
                {
                    "event": "pusher:connection_established",
                    "data": json.dumps({"socket_id": "1.2", "activity_timeout": 120}),
                }
            )
        )
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            frame = json.loads(msg.data)
            if frame["event"] == "pusher:subscribe":
                channel = frame["data"]["channel"]
                self.subscribed.append(channel)
                await ws.send_str(json.dumps({"event": "pusher:ping", "data": {}}))
                await ws.send_str(
                    json.dumps(
                        {
                            "event": "App\\Events\\UserBannedEvent",
                            "channel": channel,
                            "data": json.dumps(BAN_EVENT),
                        }
                    )
                )
                await ws.send_str(
                    json.dumps(
                        {
                            "event": "App\\Events\\ChatMessageEvent",
                            "channel": channel,
                            "data": json.dumps(CHAT_EVENT),
                        }
                    )
                )
                if self.connections == 1:
                    await ws.close()  # force one reconnect
                    break
            elif frame["event"] == "pusher:pong":
                self.pongs += 1
        return ws


async def test_live_chat_end_to_end() -> None:
    fake = FakePusher()
    app = web.Application()
    app.router.add_get("/ws", fake.handler)
    server = TestServer(app)
    await server.start_server()
    live = LiveChat(ws_url=str(server.make_url("/ws")), max_backoff=0.05)
    messages: list[LiveChatMessage] = []
    bans: list[LiveUserBanned] = []
    raw: list[Any] = []

    @live.on_message
    async def on_message(msg: LiveChatMessage) -> None:
        messages.append(msg)
        if len(messages) == 2:
            await live.stop()

    @live.on_event("banned")
    def on_ban(ban: LiveUserBanned) -> None:
        bans.append(ban)

    @live.on_event("*")
    def on_raw(data: dict[str, Any], ref: ChannelRef | None) -> None:
        raw.append((data["id"], ref))

    await live.join(REF)
    try:
        await asyncio.wait_for(live.run(), 5)
    finally:
        await server.close()

    assert fake.connections == 2  # reconnected after the server closed the socket
    assert fake.subscribed == ["chatrooms.1653897.v2"] * 2
    assert fake.pongs >= 1
    msg = messages[0]
    assert msg.content == "!ping" and msg.channel == REF
    assert msg.broadcaster_user_id == 1710384
    assert msg.sender.is_moderator and not msg.sender.is_subscriber
    assert msg.is_reply and msg.reply_to_id == "61fa"
    assert bans[0].user.username == "spammer" and bans[0].duration == 10
    assert ("a6ef", REF) in raw


async def test_resolve_channel() -> None:
    async def channel(request: web.Request) -> web.Response:
        if request.match_info["slug"] == "missing":
            return web.json_response({"message": "Not found"}, status=404)
        return web.json_response(
            {
                "id": 1661231,
                "user_id": 1710384,
                "slug": "berkaydigital",
                "chatroom": {"id": 1653897},
            }
        )

    app = web.Application()
    app.router.add_get("/api/v2/channels/{slug}", channel)
    server = TestServer(app)
    await server.start_server()
    live = LiveChat()
    live.channel_info_url = str(server.make_url("/api/v2/channels/")) + "{slug}"
    try:
        ref = await live.resolve("https://kick.com/BerkayDigital/")
        assert ref == REF
        with pytest.raises(RealtimeError, match="no Kick channel"):
            await live.resolve("missing")
    finally:
        await live._close_session()
        await server.close()


def _dead_url() -> str:
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"ws://127.0.0.1:{port}/ws"


async def test_reconnect_false_raises() -> None:
    live = LiveChat(ws_url=_dead_url(), reconnect=False)
    with pytest.raises(RealtimeError):
        await live.run()


async def test_stop_interrupts_backoff_and_ends_messages() -> None:
    live = LiveChat(ws_url=_dead_url(), max_backoff=60)
    received: list[LiveChatMessage] = []

    async def consume() -> None:
        async for msg in live.messages():
            received.append(msg)

    consumer = asyncio.create_task(consume())
    runner = asyncio.create_task(live.run())
    await asyncio.sleep(0.3)  # first connect fails, now sleeping in backoff
    await live.stop()
    await asyncio.wait_for(runner, 2)
    await asyncio.wait_for(consumer, 2)
    assert received == []


async def test_stop_before_run_starts() -> None:
    live = LiveChat(ws_url=_dead_url())
    task = asyncio.create_task(live.run())
    await live.stop()  # the task hasn't started yet
    await asyncio.wait_for(task, 2)


async def test_second_run_still_dispatches() -> None:
    fake = FakePusher()
    app = web.Application()
    app.router.add_get("/ws", fake.handler)
    server = TestServer(app)
    await server.start_server()
    live = LiveChat(ws_url=str(server.make_url("/ws")), max_backoff=0.05)
    got: list[str] = []

    @live.on_message
    async def on_message(msg: LiveChatMessage) -> None:
        got.append(msg.id)
        await live.stop()

    await live.join(REF)
    try:
        await asyncio.wait_for(live.run(), 5)
        first = len(got)
        await asyncio.wait_for(live.run(), 5)
    finally:
        await server.close()
    assert first >= 1 and len(got) > first
