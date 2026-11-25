from __future__ import annotations

import time

import pytest
from aiohttp import web

from kickpython import (
    BadRequestError,
    Channel,
    EventType,
    KickClient,
    MissingTokenError,
    NotFoundError,
    RateLimitError,
    ServerError,
    Token,
    UnauthorizedError,
)

from .conftest import FakeKick, ok

CHANNEL = {
    "broadcaster_user_id": 7,
    "slug": "berkaydigital",
    "channel_description": "hi",
    "banner_picture": "",
    "stream_title": "",
    "stream": {
        "url": "",
        "key": "",
        "is_live": False,
        "is_mature": False,
        "language": "",
        "start_time": "0001-01-01T00:00:00Z",
        "viewer_count": 0,
        "thumbnail": "",
    },
    "category": {"id": 0, "name": "", "thumbnail": ""},
    "active_subscribers_count": 3,
    "brand_new_field": True,
}


async def store_user(
    client: KickClient,
    user_id: int = 7,
    *,
    expires_in: float = 3600,
    access: str = "user-1",
    refresh: str = "ref-1",
) -> None:
    await client.token_store.set(
        Token(
            access,
            expires_at=time.time() + expires_in,
            refresh_token=refresh,
            user_id=user_id,
            scopes=("chat:write",),
        )
    )


async def test_app_token_is_fetched_once_and_cached(fake: FakeKick, client: KickClient) -> None:
    fake.on("GET", "/public/v1/channels", ok([CHANNEL]))
    ch = await client.channels.get_by_slug("berkaydigital")
    await client.channels.get_by_slug("berkaydigital")
    assert isinstance(ch, Channel)
    assert ch.category is None  # id 0 means "no category"
    assert ch.stream is not None and ch.stream.start_time is None
    assert ch.stream.key is None
    assert ch.model_extra == {"brand_new_field": True}
    assert ch.url == "https://kick.com/berkaydigital"
    token_calls = fake.calls("POST", "/oauth/token")
    assert len(token_calls) == 1
    assert token_calls[0].form == {
        "grant_type": "client_credentials",
        "client_id": "cid",
        "client_secret": "secret",
    }
    api = fake.calls("GET", "/public/v1/channels")
    assert api[0].bearer == "app-1"
    assert api[0].query == [("slug", "berkaydigital")]
    assert api[0].headers["User-Agent"].startswith("kickpython/")


async def test_lists_are_sent_as_repeated_params_and_chunked(
    fake: FakeKick, client: KickClient
) -> None:
    fake.on("GET", "/public/v1/users", ok([]))
    await client.users.get(range(1, 121))
    calls = fake.calls("GET", "/public/v1/users")
    assert [len(c.query) for c in calls] == [50, 50, 20]
    assert calls[0].query[:2] == [("id", "1"), ("id", "2")]


async def test_channels_rejects_ids_and_slugs(client: KickClient) -> None:
    with pytest.raises(ValueError, match="not both"):
        await client.channels.get(user_ids=[1], slugs=["a"])


async def test_user_endpoint_without_user_raises(client: KickClient) -> None:
    with pytest.raises(MissingTokenError, match="as_user"):
        await client.chat.send("hi")
    with pytest.raises(MissingTokenError, match="no token stored"):
        await client.as_user(99).chat.send("hi")


async def test_chat_send_bot_and_user(fake: FakeKick, client: KickClient) -> None:
    await store_user(client)
    fake.on("POST", "/public/v1/chat", ok({"is_sent": True, "message_id": "m1"}))
    me = client.as_user(7)
    sent = await me.chat.send("hello")
    assert sent.is_sent and sent.message_id == "m1"
    await me.chat.send("as me", sender="user")
    await me.chat.reply("m0", "re", sender="user", broadcaster_user_id=55)
    bodies = [c.json for c in fake.calls("POST", "/public/v1/chat")]
    assert bodies[0] == {"content": "hello", "type": "bot"}
    assert bodies[1] == {"content": "as me", "type": "user", "broadcaster_user_id": 7}
    assert bodies[2] == {
        "content": "re",
        "type": "user",
        "broadcaster_user_id": 55,
        "reply_to_message_id": "m0",
    }
    assert fake.calls("POST", "/public/v1/chat")[0].bearer == "user-1"
    with pytest.raises(ValueError):
        await me.chat.send("   ")
    with pytest.raises(ValueError):
        await me.chat.send("é" * 1100)


async def test_expired_user_token_is_refreshed_and_saved(
    fake: FakeKick, client: KickClient
) -> None:
    await store_user(client, expires_in=10)  # inside the refresh margin
    fake.on(
        "POST",
        "/oauth/token",
        (
            200,
            {
                "access_token": "user-2",
                "refresh_token": "ref-2",
                "expires_in": 7200,
                "scope": "chat:write",
            },
        ),
    )
    fake.on("GET", "/public/v1/users", ok([{"user_id": 7, "name": "b"}]))
    me = await client.as_user(7).users.me()
    assert me.user_id == 7
    refresh = fake.calls("POST", "/oauth/token")
    assert refresh[0].form["grant_type"] == "refresh_token"
    assert refresh[0].form["refresh_token"] == "ref-1"
    assert fake.calls("GET", "/public/v1/users")[0].bearer == "user-2"
    saved = await client.token_store.get(7)
    assert saved is not None and saved.refresh_token == "ref-2" and saved.user_id == 7


async def test_concurrent_requests_refresh_once(fake: FakeKick, client: KickClient) -> None:
    import asyncio

    await store_user(client, expires_in=0)
    fake.on(
        "POST",
        "/oauth/token",
        (200, {"access_token": "user-2", "refresh_token": "ref-2", "expires_in": 7200}),
    )
    fake.on("GET", "/public/v1/channels", ok([CHANNEL]))
    me = client.as_user(7)
    await asyncio.gather(*(me.channels.me() for _ in range(5)))
    assert len(fake.calls("POST", "/oauth/token")) == 1


async def test_401_with_dead_token_refreshes_and_retries(
    fake: FakeKick, client: KickClient
) -> None:
    await store_user(client)
    fake.on("GET", "/public/v1/channels", (401, {"message": "Unauthorized"}), ok([CHANNEL]))
    fake.on("POST", "/oauth/token/introspect", ok({"active": False}))
    fake.on(
        "POST",
        "/oauth/token",
        (200, {"access_token": "user-2", "refresh_token": "ref-2", "expires_in": 7200}),
    )
    ch = await client.as_user(7).channels.me()
    assert ch.slug == "berkaydigital"
    calls = fake.calls("GET", "/public/v1/channels")
    assert [c.bearer for c in calls] == ["user-1", "user-2"]


async def test_401_with_valid_token_reports_missing_scope(
    fake: FakeKick, client: KickClient
) -> None:
    await store_user(client)
    fake.on("PATCH", "/public/v1/channels", (401, {"message": "Unauthorized", "data": None}))
    fake.on(
        "POST",
        "/oauth/token/introspect",
        ok({"active": True, "scope": "chat:write", "token_type": "user"}),
    )
    with pytest.raises(UnauthorizedError, match="lacks the scope") as info:
        await client.as_user(7).channels.update(stream_title="x")
    assert info.value.status == 401
    assert fake.calls("POST", "/oauth/token") == []  # no pointless refresh


async def test_failed_refresh_raises_missing_token(fake: FakeKick, client: KickClient) -> None:
    await store_user(client, expires_in=0)
    fake.on("POST", "/oauth/token", (400, {"error": "invalid_grant"}))
    with pytest.raises(MissingTokenError, match="authorize the app again"):
        await client.as_user(7).users.me()


async def test_rate_limit_retry_after(fake: FakeKick, client: KickClient) -> None:
    fake.on(
        "GET",
        "/public/v1/livestreams/stats",
        (429, {"message": "slow down"}, {"Retry-After": "0"}),
        ok({"total_count": 9}),
    )
    stats = await client.livestreams.stats()
    assert stats.total_count == 9
    assert len(fake.calls("GET", "/public/v1/livestreams/stats")) == 2


async def test_rate_limit_gives_up(fake: FakeKick, client: KickClient) -> None:
    fake.on(
        "GET", "/public/v1/livestreams/stats", (429, {"message": "slow down"}, {"Retry-After": "0"})
    )
    with pytest.raises(RateLimitError) as info:
        await client.livestreams.stats()
    assert info.value.retry_after == 0
    assert len(fake.calls("GET", "/public/v1/livestreams/stats")) == 3


async def test_server_error_retries_get_only(fake: FakeKick, client: KickClient) -> None:
    await store_user(client)
    fake.on("GET", "/public/v1/livestreams/stats", (502, "bad gateway"), ok({"total_count": 1}))
    assert (await client.livestreams.stats()).total_count == 1
    fake.on("POST", "/public/v1/chat", (500, {"message": "boom", "data": None}))
    with pytest.raises(ServerError):
        await client.as_user(7).chat.send("hi")
    assert len(fake.calls("POST", "/public/v1/chat")) == 1


async def test_error_mapping_keeps_reason(fake: FakeKick, client: KickClient) -> None:
    await store_user(client)
    fake.on(
        "POST",
        "/public/v1/ads/ad-break",
        (400, {"message": "Bad request", "data": "CHANNEL_IS_NOT_LIVE"}),
    )
    with pytest.raises(BadRequestError) as info:
        await client.as_user(7).ads.start_break(30)
    assert info.value.data == "CHANNEL_IS_NOT_LIVE"
    assert "id" in fake.calls("POST", "/public/v1/ads/ad-break")[0].json
    fake.on("DELETE", "/public/v1/chat/abc", (404, {"message": "Not Found"}))
    with pytest.raises(NotFoundError):
        await client.as_user(7).chat.delete("abc")


async def test_pagination(fake: FakeKick, client: KickClient) -> None:
    def stream(i: int) -> dict[str, object]:
        return {
            "id": f"s{i}",
            "title": f"t{i}",
            "channel": {"slug": f"c{i}"},
            "broadcaster_user": {"id": i, "username": f"u{i}", "profile_picture": ""},
            "category": {"id": 1, "name": "x", "thumbnail": ""},
            "language_code": "en",
            "tags": [],
            "has_mature_content": False,
            "viewer_count": i,
            "thumbnail": "",
            "started_at": "2026-01-01T00:00:00Z",
        }

    def handler(request: web.Request, _: bytes) -> tuple[int, object]:
        cursor = request.query.get("cursor")
        if cursor is None:
            return ok([stream(1), stream(2)], pagination={"next_cursor": "c2"})
        return ok([stream(3)], pagination={"next_cursor": ""})

    fake.on("GET", "/public/v2/livestreams", handler)
    page = await client.livestreams.list(language_codes="en", limit=2)
    assert [s.slug for s in page] == ["c1", "c2"]
    assert page.has_next
    second = await page.next()
    assert second is not None and [s.broadcaster_user_id for s in second] == [3]
    assert not second.has_next and await second.next() is None
    assert [s.id async for s in page.iter_all()] == ["s1", "s2", "s3"]
    first_query = fake.calls("GET", "/public/v2/livestreams")[0].query
    assert ("language_code", "en") in first_query and ("limit", "2") in first_query


async def test_categories_use_repeated_params(fake: FakeKick, client: KickClient) -> None:
    fake.on(
        "GET",
        "/public/v2/categories",
        ok(
            [{"id": 15, "name": "Just Chatting", "thumbnail": "", "tags": ["IRL"]}],
            pagination={"next_cursor": ""},
        ),
    )
    await client.categories.list(names=["just", "fortnite"], tags="IRL")
    cat = await client.categories.get(15)
    assert cat is not None and cat.tags == ["IRL"]
    calls = fake.calls("GET", "/public/v2/categories")
    assert calls[0].query == [("name", "just"), ("name", "fortnite"), ("tag", "IRL")]
    assert calls[1].query == [("id", "15")]


async def test_moderation_bodies(fake: FakeKick, client: KickClient) -> None:
    await store_user(client)
    fake.on("POST", "/public/v1/moderation/bans", ok({}))
    fake.on("DELETE", "/public/v1/moderation/bans", ok({}))
    me = client.as_user(7)
    await me.moderation.ban(99, reason="spam")
    await me.moderation.timeout(99, 10, broadcaster_user_id=8)
    await me.moderation.unban(99)
    posts = [c.json for c in fake.calls("POST", "/public/v1/moderation/bans")]
    assert posts == [
        {"broadcaster_user_id": 7, "user_id": 99, "reason": "spam"},
        {"broadcaster_user_id": 8, "user_id": 99, "duration": 10},
    ]
    assert fake.calls("DELETE", "/public/v1/moderation/bans")[0].json == {
        "broadcaster_user_id": 7,
        "user_id": 99,
    }
    with pytest.raises(ValueError):
        await me.moderation.timeout(99, 0)


async def test_rewards(fake: FakeKick, client: KickClient) -> None:
    await store_user(client)
    reward = {"id": "r1", "title": "Hydrate", "cost": 100, "is_enabled": True}
    fake.on("POST", "/public/v1/channels/rewards", ok(reward))
    fake.on(
        "POST",
        "/public/v1/channels/rewards/redemptions/accept",
        ok([{"id": "x", "reason": "NOT_PENDING"}]),
    )
    fake.on(
        "GET",
        "/public/v1/channels/rewards/redemptions",
        ok(
            [
                {
                    "reward": {"id": "r1", "title": "Hydrate", "can_manage": True},
                    "redemptions": [
                        {
                            "id": "d1",
                            "status": "pending",
                            "redeemed_at": "2026-01-01T00:00:00Z",
                            "user_input": "",
                            "redeemer": {"user_id": 3},
                        }
                    ],
                }
            ],
            pagination={"next_cursor": ""},
        ),
    )
    me = client.as_user(7)
    created = await me.rewards.create("Hydrate", 100, description=None)
    assert created.cost == 100
    assert fake.calls("POST", "/public/v1/channels/rewards")[0].json == {
        "title": "Hydrate",
        "cost": 100,
    }
    failed = await me.rewards.accept([f"id{i}" for i in range(30)])
    assert len(failed) == 2  # two chunks of 25 + 5, one failure each from the fake
    page = await me.rewards.redemptions(status="pending")
    assert page.items[0].redemptions[0].redeemer.user_id == 3
    with pytest.raises(ValueError):
        await me.rewards.redemptions(ids=["a"], status="pending")


async def test_events_subscribe(fake: FakeKick, client: KickClient) -> None:
    fake.on(
        "POST",
        "/public/v1/events/subscriptions",
        ok(
            [
                {"name": "chat.message.sent", "version": 1, "subscription_id": "s1"},
                {"name": "kicks.gifted", "version": 1, "error": "nope"},
            ]
        ),
    )
    with pytest.raises(ValueError, match="broadcaster_user_id"):
        await client.events.subscribe(EventType.CHAT_MESSAGE_SENT)
    res = await client.events.subscribe(
        [EventType.CHAT_MESSAGE_SENT, ("kicks.gifted", 1)], broadcaster_user_id=7
    )
    assert [r.ok for r in res] == [True, False]
    assert fake.calls("POST", "/public/v1/events/subscriptions")[0].json == {
        "events": [
            {"name": "chat.message.sent", "version": 1},
            {"name": "kicks.gifted", "version": 1},
        ],
        "method": "webhook",
        "broadcaster_user_id": 7,
    }


async def test_exchange_code_stores_token(fake: FakeKick, client: KickClient) -> None:
    fake.on(
        "POST",
        "/oauth/token",
        (
            200,
            {"access_token": "u", "refresh_token": "r", "expires_in": 7200, "scope": "user:read"},
        ),
    )
    fake.on("GET", "/public/v1/users", ok([{"user_id": 1710384, "name": "berkaydigital"}]))
    req = client.authorization_url("http://localhost:9765", ["user:read"])
    tok = await client.exchange_code(
        "the-code", code_verifier=req.code_verifier, redirect_uri=req.redirect_uri
    )
    assert tok.user_id == 1710384
    form = fake.calls("POST", "/oauth/token")[0].form
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code" and form["code_verifier"] == req.code_verifier
    assert fake.calls("GET", "/public/v1/users")[0].bearer == "u"
    assert await client.token_store.user_ids() == [1710384]


async def test_static_access_token(fake: FakeKick) -> None:
    fake.on("GET", "/public/v1/users", ok([{"user_id": 1, "name": "x"}]))
    async with KickClient(access_token="static", api_base=fake.base) as kick:
        assert (await kick.users.me()).name == "x"
    assert fake.calls("GET", "/public/v1/users")[0].bearer == "static"
    assert fake.calls("POST", "/oauth/token") == []


async def test_revoke_and_introspect(fake: FakeKick, client: KickClient) -> None:
    await store_user(client)
    fake.on("POST", "/oauth/revoke", (200, "OK"))
    fake.on(
        "POST",
        "/oauth/token/introspect",
        ok({"active": True, "token_type": "user", "scope": "chat:write", "exp": 2000000000}),
    )
    info = await client.as_user(7).introspect()
    assert info.active and info.scopes == ["chat:write"] and info.expires_at is not None
    assert fake.calls("POST", "/oauth/token/introspect")[0].bearer == "user-1"
    await client.revoke_user(7)
    revokes = fake.calls("POST", "/oauth/revoke")
    assert [dict(r.query)["token_type_hint"] for r in revokes] == ["refresh_token", "access_token"]
    assert await client.token_store.get(7) is None


async def test_public_key_needs_no_auth(fake: FakeKick, client: KickClient) -> None:
    fake.on("GET", "/public/v1/public-key", ok({"public_key": "PEM"}))
    assert await client.public_key() == "PEM"
    assert fake.calls("GET", "/public/v1/public-key")[0].bearer is None
