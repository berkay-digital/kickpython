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
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
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


def _parse_timestamp(value: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
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

    Args:
        public_key: Kick's public key (PEM). If omitted it is fetched from the
            API on first use, and refetched once when a signature fails, since
            Kick may rotate the key at any time.
        client: Optional :class:`~kickpython.KickClient` used to fetch the key.
        max_age: Reject deliveries whose timestamp is older (or further in the
            future) than this many seconds. ``None`` turns the check off.
        dedupe_size: How many recent message IDs to remember, to drop the
            retries Kick sends when a previous delivery looked failed.
    """

    def __init__(
        self,
        public_key: str | bytes | None = None,
        *,
        client: KickClient | None = None,
        max_age: float | None = 600,
        dedupe_size: int = 2048,
    ) -> None:
        self._key = _load_key(public_key) if public_key else None
        self._pinned = public_key is not None
        self._client = client
        self._key_lock = asyncio.Lock()
        self.max_age = max_age
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._dedupe_size = dedupe_size
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
        if self._client is not None:
            pem = await self._client.public_key()
        else:
            import aiohttp

            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    PUBLIC_KEY_URL,
                    headers={"Accept": "application/json", "User-Agent": "kickpython-webhooks"},
                ) as resp,
            ):
                resp.raise_for_status()
                pem = (await resp.json())["data"]["public_key"]
        return _load_key(pem)

    async def _get_key(self, *, refresh: bool = False) -> rsa.RSAPublicKey:
        async with self._key_lock:
            if self._key is None or (refresh and not self._pinned):
                self._key = await self._fetch_key()
            return self._key

    # Processing ---------------------------------------------------------

    async def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        """Raise :class:`WebhookVerificationError` unless the delivery is genuine and fresh."""
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
        if verify_signature(
            await self._get_key(),
            message_id=message_id,
            timestamp=timestamp,
            body=body,
            signature=signature,
        ):
            return
        if not self._pinned and verify_signature(
            await self._get_key(refresh=True),
            message_id=message_id,
            timestamp=timestamp,
            body=body,
            signature=signature,
        ):
            log.info("Kick webhook public key changed; using the new key")
            return
        raise WebhookVerificationError("invalid Kick-Event-Signature")

    def _is_duplicate(self, message_id: str) -> bool:
        if message_id in self._seen:
            self._seen.move_to_end(message_id)
            return True
        self._seen[message_id] = None
        while len(self._seen) > self._dedupe_size:
            self._seen.popitem(last=False)
        return False

    async def handle(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent | None:
        """Verify, parse and dispatch one delivery.

        Returns the event, or ``None`` if it was a duplicate. Raises
        :class:`WebhookVerificationError` for forged or stale requests: answer
        those with HTTP 401/403. Exceptions from your handlers propagate.
        """
        await self.verify(headers, body)
        event = parse_event(headers, body)
        if self._is_duplicate(event.message_id):
            log.debug("dropping duplicate delivery %s", event.message_id)
            return None
        await self.dispatch(event)
        return event

    async def dispatch(self, event: WebhookEvent) -> None:
        for func in [*self._handlers.get(event.type, []), *self._catch_all]:
            result = func(event)
            if inspect.isawaitable(result):
                await result

    # aiohttp integration --------------------------------------------------

    async def aiohttp_handler(self, request: web.Request) -> web.Response:
        """An aiohttp request handler. Answers 200 quickly; handler errors are logged."""
        from aiohttp import web

        body = await request.read()
        try:
            await self.verify(request.headers, body)
            event = parse_event(request.headers, body)
        except WebhookVerificationError as exc:
            log.warning("rejected webhook from %s: %s", request.remote, exc)
            return web.Response(status=401, text="invalid signature")
        if not self._is_duplicate(event.message_id):
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
        return app

    def __repr__(self) -> str:
        n = sum(len(v) for v in self._handlers.values()) + len(self._catch_all)
        return f"<WebhookHandler handlers={n}>"
