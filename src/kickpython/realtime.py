"""Read live chat over Kick's websocket (unofficial).

.. warning::
   This uses the same Pusher feed as the kick.com website, not the official
   API. Kick can change it without notice. The official way to receive chat is
   webhooks (:mod:`kickpython.webhooks`), which need a public HTTPS endpoint.
   This listener needs nothing but outbound internet, which makes it handy
   for local bots, dashboards and testing.

Reading is anonymous. To *send* messages use the official API
(``client.as_user(...).chat.send``)::

    live = LiveChat()

    @live.on_message
    async def on_message(msg: LiveChatMessage) -> None:
        if msg.content == "!ping":
            await kick.as_user(msg.broadcaster_user_id).chat.send("pong", reply_to=msg.id)

    await live.join("berkaydigital")
    await live.run()
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import random
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

import aiohttp
from pydantic import BaseModel

from .errors import KickError
from .models import KickModel, OptionalDatetime, OptionalStr

__all__ = [
    "ChannelRef",
    "LiveBadge",
    "LiveChat",
    "LiveChatMessage",
    "LiveIdentity",
    "LiveMessageDeleted",
    "LiveSender",
    "LiveUserBanned",
    "LiveUserUnbanned",
    "RealtimeError",
]

log = logging.getLogger("kickpython.realtime")

# Fallback Pusher credentials, used when Kick's realtime config endpoint fails.
DEFAULT_PUSHER_KEY = "34bf7a0ff419a2a775b9"
DEFAULT_PUSHER_CLUSTER = "us2"
CHANNEL_INFO_URL = "https://kick.com/api/v2/channels/{slug}"
REALTIME_CONFIG_URL = "https://web.kick.com/api/v1/realtime/channels/{channel_id}/chat/connection"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

EV_MESSAGE = "App\\Events\\ChatMessageEvent"
EV_DELETED = "App\\Events\\MessageDeletedEvent"
EV_BANNED = "App\\Events\\UserBannedEvent"
EV_UNBANNED = "App\\Events\\UserUnbannedEvent"


class RealtimeError(KickError):
    """The live chat feed failed (channel lookup, credentials or connection)."""


# Models ------------------------------------------------------------------------


class ChannelRef(BaseModel):
    """IDs of a channel. ``user_id`` is what the official API calls broadcaster_user_id."""

    slug: str
    chatroom_id: int
    channel_id: int | None = None
    user_id: int | None = None


class LiveBadge(KickModel):
    type: str
    text: OptionalStr = None
    count: int | None = None


class LiveIdentity(KickModel):
    color: OptionalStr = None
    badges: list[LiveBadge] = []


class LiveSender(KickModel):
    id: int
    username: str
    slug: OptionalStr = None
    identity: LiveIdentity | None = None

    def has_badge(self, badge_type: str) -> bool:
        return self.identity is not None and any(b.type == badge_type for b in self.identity.badges)

    @property
    def is_broadcaster(self) -> bool:
        return self.has_badge("broadcaster")

    @property
    def is_moderator(self) -> bool:
        return self.has_badge("moderator") or self.is_broadcaster

    @property
    def is_subscriber(self) -> bool:
        return self.has_badge("subscriber")


class _OriginalSender(KickModel):
    id: int
    username: OptionalStr = None


class _OriginalMessage(KickModel):
    id: str
    content: OptionalStr = None


class LiveMessageMetadata(KickModel):
    original_sender: _OriginalSender | None = None
    original_message: _OriginalMessage | None = None
    message_ref: OptionalStr = None


class LiveChatMessage(KickModel):
    """A chat message from the websocket feed."""

    id: str
    chatroom_id: int
    content: str
    type: str = "message"
    """``"message"`` or ``"reply"``."""
    created_at: OptionalDatetime = None
    sender: LiveSender
    metadata: LiveMessageMetadata | None = None
    thread_parent_id: OptionalStr = None
    channel: ChannelRef | None = None
    """The channel this message was sent in, if it was joined through :meth:`LiveChat.join`."""

    @property
    def broadcaster_user_id(self) -> int | None:
        """Pass this to ``client.as_user(...)`` to answer with the official API."""
        return self.channel.user_id if self.channel else None

    @property
    def is_reply(self) -> bool:
        return self.type == "reply" or self.thread_parent_id is not None

    @property
    def reply_to_id(self) -> str | None:
        if self.metadata and self.metadata.original_message:
            return self.metadata.original_message.id
        return self.thread_parent_id


class _MessageRef(KickModel):
    id: str


class LiveMessageDeleted(KickModel):
    id: str
    message: _MessageRef
    channel: ChannelRef | None = None


class _UserRef(KickModel):
    id: int
    username: OptionalStr = None
    slug: OptionalStr = None


class LiveUserBanned(KickModel):
    id: str
    user: _UserRef
    banned_by: _UserRef | None = None
    permanent: bool = False
    duration: int | None = None
    """Timeout length in minutes."""
    expires_at: OptionalDatetime = None
    channel: ChannelRef | None = None


class LiveUserUnbanned(KickModel):
    id: str
    user: _UserRef
    unbanned_by: _UserRef | None = None
    permanent: bool = False
    channel: ChannelRef | None = None


# Listener ---------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])


async def _call(func: Callable[..., Any], *args: Any) -> None:
    try:
        result = func(*args)
        if inspect.isawaitable(result):
            await result
    except Exception:
        log.exception("live chat handler %r failed", getattr(func, "__name__", func))


class LiveChat:
    """Listens to one or more Kick chats over a single websocket connection.

    Args:
        session: An ``aiohttp.ClientSession`` to reuse.
        pusher_key, pusher_cluster: Override the Pusher app. By default they
            are fetched from Kick, with a built-in fallback.
        ws_url: Full websocket URL to connect to instead of Pusher (for tests
            or a proxy). Skips the credential lookup.
        reconnect: Reconnect with backoff when the connection drops. With
            ``False``, :meth:`run` raises :class:`RealtimeError` instead.

    Handlers run one at a time, in order, in a separate task from the
    websocket, so a slow handler delays later handlers but not the connection.
    """

    channel_info_url = CHANNEL_INFO_URL
    realtime_config_url = REALTIME_CONFIG_URL

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession | None = None,
        pusher_key: str | None = None,
        pusher_cluster: str | None = None,
        ws_url: str | None = None,
        reconnect: bool = True,
        max_backoff: float = 60.0,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self._key = pusher_key
        self._cluster = pusher_cluster
        self._ws_url = ws_url
        self.reconnect = reconnect
        self.max_backoff = max_backoff
        self._channels: dict[int, ChannelRef] = {}  # chatroom_id -> ref
        self._ws: aiohttp.ClientWebSocketResponse[bool] | None = None
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._connected = asyncio.Event()
        self._message_handlers: list[Callable[[LiveChatMessage], Any]] = []
        self._event_handlers: dict[str, list[Callable[..., Any]]] = {}
        self._queues: set[asyncio.Queue[LiveChatMessage | None]] = set()
        # Frames wait here for the dispatch worker, so slow handlers never
        # hold up pings or the receive loop.
        self._inbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=10_000)
        self._client_id = str(uuid.uuid4())

    # Handlers -------------------------------------------------------------

    def on_message(self, func: F) -> F:
        """Decorator: called with every :class:`LiveChatMessage`."""
        self._message_handlers.append(func)
        return func

    def on_event(self, *names: str) -> Callable[[F], F]:
        """Decorator for other feed events.

        Use ``"deleted"``, ``"banned"`` or ``"unbanned"`` for parsed models, or
        a raw Pusher event name such as ``"App\\\\Events\\\\PinnedMessageCreatedEvent"``
        (called with ``(data: dict, channel: ChannelRef | None)``). ``"*"``
        receives every raw event.
        """

        def register(func: F) -> F:
            for name in names:
                self._event_handlers.setdefault(name, []).append(func)
            return func

        return register

    async def messages(self) -> AsyncIterator[LiveChatMessage]:
        """Iterate over incoming messages (call :meth:`run` in another task).

        The iteration ends when :meth:`run` returns.
        """
        queue: asyncio.Queue[LiveChatMessage | None] = asyncio.Queue(maxsize=10_000)
        self._queues.add(queue)
        try:
            while True:
                msg = await queue.get()
                if msg is None:
                    return
                yield msg
        finally:
            self._queues.discard(queue)

    # Channels -------------------------------------------------------------

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            if not self._owns_session:
                raise RuntimeError("the aiohttp session passed to LiveChat is closed")
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    @property
    def channels(self) -> list[ChannelRef]:
        return list(self._channels.values())

    async def resolve(self, slug: str) -> ChannelRef:
        """Look up a channel's chatroom ID from its slug (kick.com/<slug>)."""
        slug = slug.strip().lower().removeprefix("https://kick.com/").strip("/")
        url = self.channel_info_url.format(slug=slug)
        headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
        data: Any = None
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json(content_type=None)
                except ValueError as exc:
                    raise RealtimeError(f"kick.com sent a non-JSON answer for {slug!r}") from exc
            elif resp.status == 404:
                raise RealtimeError(f"no Kick channel called {slug!r}")
            elif resp.status in (403, 429, 503):
                data = await self._resolve_impersonated(url)
            else:
                raise RealtimeError(f"looking up {slug!r} failed with HTTP {resp.status}")
        try:
            return ChannelRef(
                slug=data["slug"],
                chatroom_id=int(data["chatroom"]["id"]),
                channel_id=int(data["id"]),
                user_id=int(data["user_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RealtimeError(f"unexpected channel data for {slug!r}") from exc

    async def _resolve_impersonated(self, url: str) -> Any:
        try:
            from curl_cffi.requests import AsyncSession  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RealtimeError(
                "kick.com blocked the channel lookup (Cloudflare). Install "
                "'kickpython[cloudflare]' or pass a ChannelRef with the chatroom_id"
            ) from exc
        async with AsyncSession() as s:
            resp = await s.get(url, impersonate="chrome")
            if resp.status_code != 200:
                raise RealtimeError(f"channel lookup failed with HTTP {resp.status_code}")
            return resp.json()

    async def join(self, channel: str | ChannelRef) -> ChannelRef:
        """Start receiving a channel's chat. Accepts a slug or a :class:`ChannelRef`."""
        ref = channel if isinstance(channel, ChannelRef) else await self.resolve(channel)
        self._channels[ref.chatroom_id] = ref
        # Before the handshake, the connection subscribes every joined channel itself.
        if self._connected.is_set():
            await self._subscribe(ref)
        log.info("joined chat of %s (chatroom %s)", ref.slug, ref.chatroom_id)
        return ref

    async def leave(self, channel: str | ChannelRef) -> None:
        ref = self._find(channel)
        if ref is None:
            return
        self._channels.pop(ref.chatroom_id, None)
        if self._connected.is_set():
            for name in self._pusher_channels(ref):
                await self._send("pusher:unsubscribe", {"channel": name})

    def _find(self, channel: str | ChannelRef) -> ChannelRef | None:
        if isinstance(channel, ChannelRef):
            return self._channels.get(channel.chatroom_id)
        slug = channel.lower()
        return next((c for c in self._channels.values() if c.slug.lower() == slug), None)

    # Connection -----------------------------------------------------------

    async def _credentials(self) -> tuple[str, str]:
        if self._key:
            return self._key, self._cluster or DEFAULT_PUSHER_CLUSTER
        ref = next((c for c in self._channels.values() if c.channel_id), None)
        if ref is not None:
            body = {
                "client": {"id": self._client_id, "type": "web"},
                "capabilities": {"accepted_providers": [{"provider": "pusher"}]},
            }
            try:
                async with self.session.post(
                    self.realtime_config_url.format(channel_id=ref.channel_id),
                    json=body,
                    headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                ) as resp:
                    data = await resp.json(content_type=None) if resp.status == 200 else None
                for conn in ((data or {}).get("data") or {}).get("connections") or []:
                    creds = conn.get("credentials") or {}
                    if conn.get("provider") == "pusher" and creds.get("app_key"):
                        return str(creds["app_key"]), str(creds.get("cluster") or "us2")
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                log.debug("realtime config lookup failed: %s", exc)
        return DEFAULT_PUSHER_KEY, DEFAULT_PUSHER_CLUSTER

    @staticmethod
    def _pusher_channels(ref: ChannelRef) -> list[str]:
        return [f"chatrooms.{ref.chatroom_id}.v2"]

    async def _send(self, event: str, data: Any) -> None:
        if self._ws is None or self._ws.closed:
            return
        await self._ws.send_str(json.dumps({"event": event, "data": data}))

    async def _subscribe(self, ref: ChannelRef) -> None:
        for name in self._pusher_channels(ref):
            await self._send("pusher:subscribe", {"auth": "", "channel": name})

    async def wait_connected(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def run(self) -> None:
        """Connect and dispatch events until :meth:`stop` is called."""
        self._stopping = False
        self._stop_event.clear()
        worker = asyncio.create_task(self._dispatch_worker())
        backoff = 1.0
        try:
            while not self._stopping:
                if self._ws_url:
                    url = self._ws_url
                else:
                    key, cluster = await self._credentials()
                    url = (
                        f"wss://ws-{cluster}.pusher.com/app/{key}"
                        f"?protocol=7&client=js&version=8.4.0&flash=false"
                    )
                error: BaseException | None = None
                try:
                    clean = await self._run_once(url)
                    if clean:
                        backoff = 1.0
                except _FatalPusherError as exc:
                    if self._key:  # user-supplied key: nothing else to try
                        raise RealtimeError(str(exc)) from exc
                    error = exc
                    log.warning("Pusher refused the connection (%s); refetching credentials", exc)
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                    error = exc
                    log.warning("live chat connection failed: %s", exc)
                except Exception as exc:
                    error = exc
                    log.exception("unexpected error in live chat connection")
                finally:
                    self._connected.clear()
                    self._ws = None
                if self._stopping:
                    break
                if not self.reconnect:
                    if error is not None:
                        raise RealtimeError(f"live chat connection failed: {error}") from error
                    break
                delay = min(self.max_backoff, backoff) + random.uniform(0, 1)
                log.info("reconnecting to live chat in %.1fs", delay)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), delay)
                backoff = min(self.max_backoff, backoff * 2)
        finally:
            with contextlib.suppress(asyncio.QueueFull):
                self._inbox.put_nowait(None)
            try:
                await asyncio.wait_for(asyncio.shield(worker), 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                worker.cancel()
            for queue in list(self._queues):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)
            await self._close_session()

    async def _dispatch_worker(self) -> None:
        while True:
            frame = await self._inbox.get()
            if frame is None:
                return
            if self._stopping:
                continue  # drop what's left after stop()
            try:
                await self._dispatch(frame)
            except Exception:
                log.exception("live chat dispatch failed")

    async def _run_once(self, url: str) -> bool:
        """One connection. Returns True if it was established before ending."""
        established = False
        async with self.session.ws_connect(url, heartbeat=None, autoping=True) as ws:
            self._ws = ws
            timeout = 120.0
            while not self._stopping:
                try:
                    msg = await ws.receive(timeout=timeout if established else 15)
                except asyncio.TimeoutError:
                    if not established:
                        raise
                    await self._send("pusher:ping", {})
                    try:
                        msg = await ws.receive(timeout=30)
                    except asyncio.TimeoutError:
                        log.warning("live chat connection went quiet; reconnecting")
                        return established
                if msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    code = ws.close_code or 0
                    if 4000 <= code < 4100:
                        raise _FatalPusherError(f"closed with code {code}")
                    return established
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                frame = _maybe_json(msg.data)
                if not isinstance(frame, dict):
                    log.debug("ignoring malformed frame: %.200r", msg.data)
                    continue
                event = frame.get("event")
                if not isinstance(event, str):
                    continue
                if event == "pusher:connection_established":
                    info = _maybe_json(frame.get("data"))
                    info = info if isinstance(info, dict) else {}
                    try:
                        timeout = float(info.get("activity_timeout") or 120)
                    except (TypeError, ValueError):
                        timeout = 120.0
                    established = True
                    for ref in list(self._channels.values()):
                        await self._subscribe(ref)
                    self._connected.set()
                    log.info("connected to Kick live chat (%d channels)", len(self._channels))
                elif event == "pusher:ping":
                    await self._send("pusher:pong", {})
                elif event == "pusher:error":
                    err = _maybe_json(frame.get("data"))
                    err = err if isinstance(err, dict) else {}
                    try:
                        code = int(err.get("code") or 0)
                    except (TypeError, ValueError):
                        code = 0
                    if 4000 <= code < 4100:
                        raise _FatalPusherError(f"{code} {err.get('message')}")
                    log.warning("Pusher error: %s", err)
                elif not event.startswith("pusher"):
                    try:
                        self._inbox.put_nowait(frame)
                    except asyncio.QueueFull:
                        log.warning("live chat handlers are too slow; dropping %s", event)
        return established

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        event = str(frame.get("event"))
        data = _maybe_json(frame.get("data"))
        channel_name = str(frame.get("channel") or "")
        ref = self._ref_for(channel_name)
        if not isinstance(data, dict):
            return
        for func in self._event_handlers.get(event, []) + self._event_handlers.get("*", []):
            await _call(func, data, ref)
        try:
            if event == EV_MESSAGE:
                msg = LiveChatMessage.model_validate({**data, "channel": ref})
                for queue in list(self._queues):
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(msg)
                for func in self._message_handlers:
                    await _call(func, msg)
            elif event in (EV_DELETED, EV_BANNED, EV_UNBANNED):
                model, key = _PARSED_EVENTS[event]
                parsed = model.model_validate({**data, "channel": ref})
                for func in self._event_handlers.get(key, []):
                    await _call(func, parsed)
        except ValueError:
            log.warning("could not parse live chat event %s", event, exc_info=True)

    def _ref_for(self, pusher_channel: str) -> ChannelRef | None:
        # "chatrooms.<id>.v2"
        parts = pusher_channel.split(".")
        if len(parts) >= 2 and parts[1].isdigit():
            return self._channels.get(int(parts[1]))
        return None

    async def stop(self) -> None:
        """Disconnect and make :meth:`run` return."""
        self._stopping = True
        self._stop_event.set()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    async def _close_session(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> LiveChat:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()
        await self._close_session()


_PARSED_EVENTS: dict[str, tuple[type[KickModel], str]] = {
    EV_DELETED: (LiveMessageDeleted, "deleted"),
    EV_BANNED: (LiveUserBanned, "banned"),
    EV_UNBANNED: (LiveUserUnbanned, "unbanned"),
}


class _FatalPusherError(Exception):
    pass


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value
