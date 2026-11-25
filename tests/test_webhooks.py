from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kickpython import (
    ChatMessage,
    EventType,
    KicksGifted,
    ModerationBanned,
    SubscriptionGifts,
    WebhookEvent,
    WebhookHandler,
    WebhookVerificationError,
    verify_signature,
)
from kickpython.events import payload_model

from .samples import SAMPLES


def make_key() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return key, pem


KEY, PEM = make_key()


def signed(
    event: str,
    payload: dict[str, Any],
    *,
    key: rsa.RSAPrivateKey = KEY,
    message_id: str = "01MSG",
    ts: datetime | None = None,
) -> tuple[dict[str, str], bytes]:
    body = json.dumps(payload).encode()
    stamp = (ts or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sig = key.sign(f"{message_id}.{stamp}.".encode() + body, padding.PKCS1v15(), hashes.SHA256())
    headers = {
        "Kick-Event-Message-Id": message_id,
        "Kick-Event-Subscription-Id": "01SUB",
        "Kick-Event-Signature": base64.b64encode(sig).decode(),
        "Kick-Event-Message-Timestamp": stamp,
        "Kick-Event-Type": event,
        "Kick-Event-Version": "1",
    }
    return headers, body


def test_verify_signature() -> None:
    headers, body = signed("chat.message.sent", SAMPLES["chat.message.sent"])
    args = {
        "message_id": headers["Kick-Event-Message-Id"],
        "timestamp": headers["Kick-Event-Message-Timestamp"],
        "signature": headers["Kick-Event-Signature"],
    }
    assert verify_signature(PEM, body=body, **args)
    assert not verify_signature(PEM, body=body + b" ", **args)
    assert not verify_signature(PEM, body=body, **{**args, "signature": "not base64!"})
    _, other = make_key()
    assert not verify_signature(other, body=body, **args)


@pytest.mark.parametrize("event_type", list(SAMPLES))
def test_doc_samples_parse(event_type: str) -> None:
    model = payload_model(event_type)
    assert model is not None
    model.model_validate(SAMPLES[event_type])


async def test_handler_dispatches_typed_events() -> None:
    hook = WebhookHandler(PEM)
    seen: list[WebhookEvent] = []
    every: list[str] = []

    @hook.on(EventType.CHAT_MESSAGE_SENT)
    async def chat(event: WebhookEvent) -> None:
        seen.append(event)

    @hook.on()
    def catch_all(event: WebhookEvent) -> None:
        every.append(event.type)

    headers, body = signed("chat.message.sent", SAMPLES["chat.message.sent"])
    event = await hook.handle(headers, body)
    assert event is not None and seen == [event]
    msg = event.payload
    assert isinstance(msg, ChatMessage)
    assert msg.sender.username == "sender_name"
    assert msg.sender.is_moderator
    assert msg.replies_to is not None and msg.replies_to.message_id == "unique_message_id_456"
    assert event.subscription_id == "01SUB" and event.version == 1
    assert every == ["chat.message.sent"]

    # Kick retries deliveries; the same message id is dispatched once.
    assert await hook.handle(headers, body) is None
    assert len(seen) == 1


async def test_handler_rejects_bad_requests() -> None:
    hook = WebhookHandler(PEM)
    headers, body = signed("channel.followed", SAMPLES["channel.followed"])
    with pytest.raises(WebhookVerificationError):
        await hook.handle(headers, body.replace(b"follower", b"f0llower"))
    with pytest.raises(WebhookVerificationError, match="missing"):
        await hook.handle({k: v for k, v in headers.items() if "Signature" not in k}, body)
    old_headers, old_body = signed(
        "channel.followed",
        SAMPLES["channel.followed"],
        ts=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    with pytest.raises(WebhookVerificationError, match="too old"):
        await hook.handle(old_headers, old_body)
    # Header names are case-insensitive.
    lower = {k.lower(): v for k, v in headers.items()}
    assert await hook.handle(lower, body) is not None


def _fake_key_source(hook: WebhookHandler, pems: list[str]) -> list[int]:
    calls = [0]

    async def fetch() -> rsa.RSAPublicKey:
        calls[0] += 1
        pem = pems.pop(0) if len(pems) > 1 else pems[0]
        return serialization.load_pem_public_key(pem.encode())  # type: ignore[return-value]

    hook._fetch_key = fetch  # type: ignore[method-assign]
    return calls


async def test_handler_refetches_rotated_key() -> None:
    new_key, new_pem = make_key()
    hook = WebhookHandler()
    calls = _fake_key_source(hook, [PEM, new_pem])
    headers, body = signed("kicks.gifted", SAMPLES["kicks.gifted"], key=new_key)
    hook.key_refresh_interval = 0
    event = await hook.handle(headers, body)
    assert event is not None and isinstance(event.payload, KicksGifted)
    assert calls[0] == 2


async def test_forged_requests_cannot_force_key_refetches() -> None:
    hook = WebhookHandler()
    calls = _fake_key_source(hook, [PEM])
    forger, _ = make_key()
    for i in range(20):
        headers, body = signed(
            "channel.followed", SAMPLES["channel.followed"], key=forger, message_id=f"forged-{i}"
        )
        with pytest.raises(WebhookVerificationError):
            await hook.handle(headers, body)
    assert calls[0] == 1  # the initial fetch only; refetches are on cooldown
    headers, body = signed("channel.followed", SAMPLES["channel.followed"])
    assert await hook.handle(headers, body) is not None


async def test_broadcaster_allowlist() -> None:
    hook = WebhookHandler(PEM, broadcaster_user_ids=[123456789])
    headers, body = signed("channel.followed", SAMPLES["channel.followed"])
    assert await hook.handle(headers, body) is not None
    other = WebhookHandler(PEM, broadcaster_user_ids=[1])
    headers, body = signed("channel.followed", SAMPLES["channel.followed"], message_id="m2")
    with pytest.raises(WebhookVerificationError, match="not one of yours"):
        await other.handle(headers, body)


async def test_failed_handler_lets_retry_through() -> None:
    hook = WebhookHandler(PEM)
    attempts = []

    @hook.on(EventType.CHANNEL_FOLLOWED)
    def flaky(event: WebhookEvent) -> None:
        attempts.append(event.message_id)
        if len(attempts) == 1:
            raise RuntimeError("database down")

    headers, body = signed("channel.followed", SAMPLES["channel.followed"])
    with pytest.raises(RuntimeError):
        await hook.handle(headers, body)
    assert await hook.handle(headers, body) is not None
    assert await hook.handle(headers, body) is None
    assert len(attempts) == 2


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-01-14T16:08:06Z",
        "2026-01-14T16:08:06.1Z",
        "2026-01-14T16:08:06.12345Z",
        "2026-01-14T16:08:06.123456789Z",
        "2026-01-14T16:08:06+00:00",
    ],
)
def test_timestamp_formats(stamp: str) -> None:
    from kickpython.webhooks import _parse_timestamp

    ts = _parse_timestamp(stamp)
    assert ts is not None and ts.tzinfo is not None and ts.second == 6


async def test_unknown_event_type_keeps_raw() -> None:
    hook = WebhookHandler(PEM)
    headers, body = signed("brand.new.event", {"x": 1})
    event = await hook.handle(headers, body)
    assert event is not None and event.payload is None and event.raw == {"x": 1}


async def test_aiohttp_app() -> None:
    hook = WebhookHandler(PEM)
    got: list[WebhookEvent] = []

    @hook.on(EventType.MODERATION_BANNED)
    async def banned(event: WebhookEvent) -> None:
        got.append(event)

    client = TestClient(TestServer(hook.aiohttp_app("/hook")))
    await client.start_server()
    try:
        headers, body = signed("moderation.banned", SAMPLES["moderation.banned"])
        resp = await client.post("/hook", data=body, headers=headers)
        assert resp.status == 200
        bad = await client.post("/hook", data=body + b"x", headers=headers)
        assert bad.status == 401
        for _ in range(20):
            if got:
                break
            import asyncio

            await asyncio.sleep(0.01)
        assert isinstance(got[0].payload, ModerationBanned)
        assert not got[0].payload.is_permanent
    finally:
        await client.close()


def test_anonymous_gifter() -> None:
    data = dict(SAMPLES["channel.subscription.gifts"])
    data["gifter"] = {
        "is_anonymous": True,
        "user_id": None,
        "username": None,
        "is_verified": None,
        "profile_picture": None,
        "channel_slug": None,
        "identity": None,
    }
    gifts = SubscriptionGifts.model_validate(data)
    assert gifts.gifter.is_anonymous and gifts.gifter.user_id is None


async def test_concurrent_retries_dispatch_once() -> None:
    import asyncio

    hook = WebhookHandler(PEM)
    runs = []
    release = asyncio.Event()

    @hook.on(EventType.CHANNEL_FOLLOWED)
    async def slow(event: WebhookEvent) -> None:
        runs.append(event.message_id)
        await release.wait()

    headers, body = signed("channel.followed", SAMPLES["channel.followed"])
    tasks = [asyncio.create_task(hook.handle(headers, body)) for _ in range(5)]
    await asyncio.sleep(0.05)
    release.set()
    results = await asyncio.gather(*tasks)
    assert len(runs) == 1
    assert sum(r is not None for r in results) == 1


async def test_failed_key_fetch_fails_fast() -> None:
    from kickpython import WebhookKeyUnavailable

    hook = WebhookHandler()
    calls = [0]

    async def broken() -> rsa.RSAPublicKey:
        calls[0] += 1
        raise OSError("kick is down")

    hook._fetch_key = broken  # type: ignore[method-assign]
    headers, body = signed("channel.followed", SAMPLES["channel.followed"])
    for _ in range(10):
        with pytest.raises(WebhookKeyUnavailable):
            await hook.handle(headers, body)
    assert calls[0] == 1


CLAIM = {
    "claim_id": "01CLAIM",
    "user_id": 42,
    "campaign_id": "01CAMP",
    "reward_id": "01REW",
    "external_id": "sword-1",
}


def drops_signed(
    payload: dict[str, Any], message_id: str = "01DROP"
) -> tuple[dict[str, str], bytes]:
    headers, body = signed("unused", payload, message_id=message_id)
    # Drops claim webhooks: no type/version/subscription headers, and a different timestamp name.
    return {
        "Kick-Event-Signature": headers["Kick-Event-Signature"],
        "Kick-Event-Message-ID": headers["Kick-Event-Message-Id"],
        "Kick-Event-Timestamp": headers["Kick-Event-Message-Timestamp"],
    }, body


async def test_drops_claim_webhook() -> None:
    from kickpython import DROPS_REWARD_CLAIMED, DropsRewardClaimed

    hook = WebhookHandler(PEM, broadcaster_user_ids=[123])  # allowlist must not block drops
    got: list[WebhookEvent] = []

    @hook.on(DROPS_REWARD_CLAIMED)
    def on_claim(event: WebhookEvent) -> None:
        got.append(event)

    headers, body = drops_signed(CLAIM)
    event = await hook.handle(headers, body)
    assert event is not None and event.type == DROPS_REWARD_CLAIMED
    claim = event.payload
    assert isinstance(claim, DropsRewardClaimed)
    assert (claim.claim_id, claim.user_id, claim.external_id) == ("01CLAIM", 42, "sword-1")
    assert event.timestamp is not None and got == [event]


async def test_untyped_non_drops_body_is_rejected() -> None:
    hook = WebhookHandler(PEM)
    headers, body = drops_signed({"hello": "world"})
    with pytest.raises(WebhookVerificationError, match="Kick-Event-Type"):
        await hook.handle(headers, body)


async def test_aiohttp_drops_claim_is_synchronous() -> None:
    hook = WebhookHandler(PEM)
    calls: list[str] = []

    @hook.on("drops.reward.claimed")
    async def fulfil(event: WebhookEvent) -> None:
        calls.append(event.message_id)
        if len(calls) == 1:
            raise RuntimeError("game server down")

    client = TestClient(TestServer(hook.aiohttp_app("/hook")))
    await client.start_server()
    try:
        headers, body = drops_signed(CLAIM)
        first = await client.post("/hook", data=body, headers=headers)
        assert first.status == 500  # Kick will retry
        retry = await client.post("/hook", data=body, headers=headers)
        assert retry.status == 200
        again = await client.post("/hook", data=body, headers=headers)
        assert again.status == 200  # idempotent: already fulfilled, not run again
        assert len(calls) == 2
    finally:
        await client.close()
