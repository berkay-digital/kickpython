"""Receive and verify Kick webhooks.

Kick signs every delivery with RSA (PKCS#1 v1.5, SHA-256) over
``"{message_id}.{timestamp}.{raw body}"``. :class:`WebhookHandler` checks the
signature against Kick's public key (fetched from the API and refetched on a
mismatch in case Kick rotated it), drops replays and duplicate deliveries,
parses the payload and calls your handlers.

It works with any web framework through :meth:`WebhookHandler.handle`, and
ships an aiohttp route for convenience::

    hook = WebhookHandler()

    @hook.on(EventType.CHAT_MESSAGE_SENT)
    async def on_chat(event: WebhookEvent) -> None:
        msg = event.payload  # ChatMessage
        print(msg.sender.username, msg.content)

    app = hook.aiohttp_app("/kick/webhook")
    web.run_app(app, host="127.0.0.1", port=8080)
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, TypeVar

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .errors import WebhookVerificationError
from .events import WebhookEvent, payload_model
from .scopes import EventType

if TYPE_CHECKING:
    from aiohttp import web

    from .client import KickClient

__all__ = [
    "HEADER_EVENT_TYPE",
    "HEADER_EVENT_VERSION",
    "HEADER_MESSAGE_ID",
    "HEADER_SIGNATURE",
    "HEADER_SUBSCRIPTION_ID",
    "HEADER_TIMESTAMP",
    "WebhookHandler",
    "parse_event",
    "verify_signature",
]

log = logging.getLogger("kickpython.webhooks")

HEADER_MESSAGE_ID = "Kick-Event-Message-Id"
HEADER_SUBSCRIPTION_ID = "Kick-Event-Subscription-Id"
HEADER_SIGNATURE = "Kick-Event-Signature"
HEADER_TIMESTAMP = "Kick-Event-Message-Timestamp"
HEADER_EVENT_TYPE = "Kick-Event-Type"
HEADER_EVENT_VERSION = "Kick-Event-Version"
PUBLIC_KEY_URL = "https://api.kick.com/public/v1/public-key"
KEY_FETCH_TIMEOUT = 10.0

Handler = Callable[[WebhookEvent], Awaitable[None] | None]
H = TypeVar("H", bound=Handler)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None:
        lowered = name.lower()
        for key, val in headers.items():
            if key.lower() == lowered:
                return val
    return value


def _load_key(pem: str | bytes) -> rsa.RSAPublicKey:
    data = pem.encode() if isinstance(pem, str) else pem
    key = serialization.load_pem_public_key(data)
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("Kick's webhook public key should be an RSA key")
    return key


def verify_signature(
    public_key: str | bytes | rsa.RSAPublicKey,
    *,
    message_id: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Check a ``Kick-Event-Signature`` header. Returns ``True`` if it is valid."""
    key = public_key if isinstance(public_key, rsa.RSAPublicKey) else _load_key(public_key)
    try:
        sig = base64.b64decode(signature, validate=True)
    except (binascii.Error, ValueError):
        return False
    signed = message_id.encode() + b"." + timestamp.encode() + b"." + body
    try:
        key.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        return False
    return True


_FRACTION = re.compile(r"\.(\d+)")


def _parse_timestamp(value: str) -> datetime | None:
    # Python 3.10's fromisoformat only takes 3 or 6 fractional digits.
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def parse_event(headers: Mapping[str, str], body: bytes | str) -> WebhookEvent:
    """Parse a delivery without verifying it. Use :class:`WebhookHandler` in production."""
    raw_body = body.encode() if isinstance(body, str) else body
    event_type = _header(headers, HEADER_EVENT_TYPE)
    message_id = _header(headers, HEADER_MESSAGE_ID)
    if not event_type or not message_id:
        raise WebhookVerificationError("missing Kick-Event-Type or Kick-Event-Message-Id header")
    try:
        data = json.loads(raw_body)
    except ValueError as exc:
        raise WebhookVerificationError("webhook body is not JSON") from exc
    if not isinstance(data, dict):
        raise WebhookVerificationError("webhook body is not a JSON object")
    version_raw = _header(headers, HEADER_EVENT_VERSION) or "1"
    try:
        version = int(version_raw.strip().strip('"'))
    except ValueError:
        version = 1
    model = payload_model(event_type)
    payload = None
    if model is not None:
        try:
            payload = model.model_validate(data)
        except ValueError:
            log.warning(
                "could not parse %s v%s payload; see event.raw", event_type, version, exc_info=True
            )
    ts_header = _header(headers, HEADER_TIMESTAMP)
    return WebhookEvent(
        type=event_type,
        version=version,
        message_id=message_id,
        subscription_id=_header(headers, HEADER_SUBSCRIPTION_ID),
        timestamp=_parse_timestamp(ts_header) if ts_header else None,
        payload=payload,
        raw=data,
    )


class WebhookHandler:
    """Verifies webhook deliveries and dispatches them to your handlers.

    Only the message ID, timestamp and body are signed. The
    ``Kick-Event-Type``, ``-Version`` and ``-Subscription-Id`` headers are not,
    and Kick signs deliveries for every app with the same key. So a valid
    signature proves Kick sent the body to *some* app, not that it is an event
    you subscribed to. Pass ``broadcaster_user_ids`` to accept only events for
    your channels, and keep handlers idempotent.

    Args:
        public_key: Kick's public key (PEM). If omitted it is fetched from the
            API on first use. After a signature failure it is refetched, at
            most once per ``key_refresh_interval``, since Kick may rotate it.
        client: Optional :class:`~kickpython.KickClient` whose session and API
            base URL are used to fetch the key.
        broadcaster_user_ids: If set, reject deliveries whose body names a
            different broadcaster (``broadcaster.user_id``).
        max_age: Reject deliveries whose timestamp is further than this many
            seconds from now. ``None`` turns the check off (and makes duplicate
            detection best effort).
        key_refresh_interval: Minimum seconds between key refetches.
    """

    def __init__(
        self,
        public_key: str | bytes | None = None,
        *,
        client: KickClient | None = None,
        broadcaster_user_ids: Iterable[int] | None = None,
        max_age: float | None = 600,
        key_refresh_interval: float = 300,
        dedupe_limit: int = 100_000,
    ) -> None:
        self._key = _load_key(public_key) if public_key else None
        self._pinned = public_key is not None
        self._client = client
        self._key_lock = asyncio.Lock()
        self._last_fetch = float("-inf")
        self.key_refresh_interval = key_refresh_interval
        self.broadcaster_user_ids = (
            frozenset(int(i) for i in broadcaster_user_ids)
            if broadcaster_user_ids is not None
            else None
        )
        self.max_age = max_age
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._dedupe_limit = dedupe_limit
        self._handlers: dict[str, list[Handler]] = {}
        self._catch_all: list[Handler] = []
        self._tasks: set[asyncio.Task[None]] = set()

    # Registration -------------------------------------------------------

    def on(self, *event_types: EventType | str) -> Callable[[H], H]:
        """Decorator: call the function for these event types (all events if none given)."""

        def register(func: H) -> H:
            self.add_handler(func, *event_types)
            return func

        return register

    def add_handler(self, func: Handler, *event_types: EventType | str) -> None:
        if not event_types:
            self._catch_all.append(func)
        for et in event_types:
            self._handlers.setdefault(str(et), []).append(func)

    # Key management -----------------------------------------------------

    async def _fetch_key(self) -> rsa.RSAPublicKey:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=KEY_FETCH_TIMEOUT)
        headers = {"Accept": "application/json", "User-Agent": "kickpython-webhooks"}
        if self._client is not None:
            http = self._client._http
            url = f"{http.api_base}/public/v1/public-key"
            async with http.session.get(url, headers=headers, timeout=timeout) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        else:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(PUBLIC_KEY_URL, headers=headers) as resp,
            ):
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        return _load_key(data["data"]["public_key"])

    async def _current_key(self) -> rsa.RSAPublicKey:
        key = self._key
        if key is not None:
            return key
        async with self._key_lock:
            if self._key is None:
                self._key = await self._fetch_key()
                self._last_fetch = time.monotonic()
            return self._key

    async def _rotated_key(self, failed: rsa.RSAPublicKey) -> rsa.RSAPublicKey | None:
        """A newer key than ``failed``, or ``None`` if refetching is on cooldown or fails."""
        if self._pinned:
            return None
        async with self._key_lock:
            if self._key is not None and self._key is not failed:
                return self._key  # another request already refetched
            if time.monotonic() - self._last_fetch < self.key_refresh_interval:
                return None
            self._last_fetch = time.monotonic()
            try:
                key = await self._fetch_key()
            except Exception as exc:
                log.warning("could not refetch Kick's public key: %s", exc)
                return None
            if key.public_numbers() == failed.public_numbers():
                return None
            self._key = key
            return key

    # Processing ---------------------------------------------------------

    async def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        """Raise :class:`WebhookVerificationError` unless the delivery is genuine and fresh.

        May raise other exceptions (network errors) if the public key can't
        be fetched at all; answer those with HTTP 5xx so Kick retries.
        """
        message_id = _header(headers, HEADER_MESSAGE_ID)
        timestamp = _header(headers, HEADER_TIMESTAMP)
        signature = _header(headers, HEADER_SIGNATURE)
        if not message_id or not timestamp or not signature:
            raise WebhookVerificationError("missing Kick-Event-* signature headers")
        if self.max_age is not None:
            ts = _parse_timestamp(timestamp)
            if ts is None:
                raise WebhookVerificationError("bad Kick-Event-Message-Timestamp")
            skew = abs(datetime.now(timezone.utc) - ts)
            if skew > timedelta(seconds=self.max_age):
                raise WebhookVerificationError(f"delivery is too old ({skew})")
        args = (message_id, timestamp, body, signature)
        key = await self._current_key()
        if _check(key, *args):
            return
        newer = await self._rotated_key(key)
        if newer is not None and _check(newer, *args):
            log.info("Kick webhook public key changed; using the new key")
            return
        raise WebhookVerificationError("invalid Kick-Event-Signature")

    def _check_broadcaster(self, event: WebhookEvent) -> None:
        if self.broadcaster_user_ids is None:
            return
        broadcaster = event.raw.get("broadcaster")
        uid = broadcaster.get("user_id") if isinstance(broadcaster, dict) else None
        if uid is None or int(uid) not in self.broadcaster_user_ids:
            raise WebhookVerificationError(f"event is for broadcaster {uid}, not one of yours")

    def _seen_before(self, message_id: str) -> bool:
        return message_id in self._seen

    def _remember(self, message_id: str) -> None:
        now = time.monotonic()
        self._seen[message_id] = now
        self._seen.move_to_end(message_id)
        # Forget IDs once their timestamp can no longer pass the max_age check.
        horizon = 2 * self.max_age if self.max_age is not None else float("inf")
        while self._seen:
            oldest_id, seen_at = next(iter(self._seen.items()))
            if now - seen_at > horizon or len(self._seen) > self._dedupe_limit:
                self._seen.pop(oldest_id)
            else:
                break

    async def accept(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent | None:
        """Verify and parse a delivery without dispatching it.

        Returns ``None`` for a duplicate. Raises :class:`WebhookVerificationError`
        for forged, stale or foreign deliveries.
        """
        await self.verify(headers, body)
        event = parse_event(headers, body)
        self._check_broadcaster(event)
        if self._seen_before(event.message_id):
            log.debug("dropping duplicate delivery %s", event.message_id)
            return None
        return event

    async def handle(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent | None:
        """Verify, parse and dispatch one delivery.

        Returns the event, or ``None`` if it was a duplicate. Raises
        :class:`WebhookVerificationError` for forged, stale or foreign
        requests: answer those with HTTP 401. Exceptions from your handlers
        propagate, and the delivery is not marked as seen, so Kick's retry
        gets another chance.
        """
        event = await self.accept(headers, body)
        if event is None:
            return None
        await self.dispatch(event)
        self._remember(event.message_id)
        return event

    async def dispatch(self, event: WebhookEvent) -> None:
        for func in [*self._handlers.get(event.type, []), *self._catch_all]:
            result = func(event)
            if inspect.isawaitable(result):
                await result

    # aiohttp integration --------------------------------------------------

    async def aiohttp_handler(self, request: web.Request) -> web.Response:
        """An aiohttp request handler. Answers quickly; handler errors are logged."""
        from aiohttp import web

        body = await request.read()
        try:
            event = await self.accept(request.headers, body)
        except WebhookVerificationError as exc:
            log.warning("rejected webhook from %s: %s", request.remote, exc)
            return web.Response(status=401, text="rejected")
        except Exception:
            log.exception("could not verify webhook (public key unavailable?)")
            return web.Response(status=503, text="try again")
        if event is not None:
            self._remember(event.message_id)
            task = asyncio.create_task(self._safe_dispatch(event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return web.Response(status=200, text="ok")

    async def _safe_dispatch(self, event: WebhookEvent) -> None:
        try:
            await self.dispatch(event)
        except Exception:
            log.exception("webhook handler failed for %s %s", event.type, event.message_id)

    def aiohttp_app(self, path: str = "/kick/webhook") -> web.Application:
        """A ready ``aiohttp.web.Application`` serving this handler at ``path``."""
        from aiohttp import web

        app = web.Application()
        app.router.add_post(path, self.aiohttp_handler)
        app.on_cleanup.append(self._drain)
        return app

    async def _drain(self, _app: web.Application | None = None) -> None:
        """Wait (up to 10 s) for handlers still running from accepted deliveries."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=10)

    def __repr__(self) -> str:
        n = sum(len(v) for v in self._handlers.values()) + len(self._catch_all)
        return f"<WebhookHandler handlers={n}>"


def _check(key: rsa.RSAPublicKey, message_id: str, timestamp: str, body: bytes, sig: str) -> bool:
    return verify_signature(
        key, message_id=message_id, timestamp=timestamp, body=body, signature=sig
    )
