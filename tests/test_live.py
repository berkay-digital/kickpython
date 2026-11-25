"""Checks against the real Kick API. Skipped unless credentials are set.

    KICK_CLIENT_ID=... KICK_CLIENT_SECRET=... uv run pytest -m live

These only read public data with an app token, so they are safe to run.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from kickpython import KickClient, LiveChat

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("KICK_CLIENT_ID") and os.environ.get("KICK_CLIENT_SECRET")),
        reason="KICK_CLIENT_ID / KICK_CLIENT_SECRET not set",
    ),
]

SLUG = os.environ.get("KICK_TEST_SLUG", "berkaydigital")


@pytest.fixture
async def kick() -> AsyncIterator[KickClient]:
    async with KickClient(os.environ["KICK_CLIENT_ID"], os.environ["KICK_CLIENT_SECRET"]) as c:
        yield c


async def test_app_token_and_introspect(kick: KickClient) -> None:
    info = await kick.introspect()
    assert info.active and info.token_type == "app"


async def test_channels_and_users(kick: KickClient) -> None:
    channel = await kick.channels.get_by_slug(SLUG)
    assert channel is not None and channel.slug == SLUG
    users = await kick.users.get([channel.broadcaster_user_id])
    assert users[0].user_id == channel.broadcaster_user_id


async def test_categories_and_livestreams(kick: KickClient) -> None:
    page = await kick.categories.list(names=["just"], limit=5)
    assert any(c.name == "Just Chatting" for c in page)
    streams = await kick.livestreams.list(limit=2)
    assert len(streams) == 2
    following = await streams.next()
    assert following is not None
    wanted = {s.broadcaster_user_id for s in streams}
    live = await kick.livestreams.for_users(wanted)
    assert {s.broadcaster_user_id for s in live} <= wanted
    assert (await kick.livestreams.stats()).total_count > 0


async def test_public_key(kick: KickClient) -> None:
    assert (await kick.public_key()).startswith("-----BEGIN PUBLIC KEY-----")


async def test_live_chat_connects() -> None:
    import asyncio

    async with LiveChat() as live:
        ref = await live.join(SLUG)
        assert ref.chatroom_id > 0 and ref.user_id
        task = asyncio.create_task(live.run())
        await live.wait_connected(15)
        await live.stop()
        await asyncio.wait_for(task, 10)
