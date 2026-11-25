"""The Kick API client."""

from __future__ import annotations

import asyncio
import builtins
import urllib.parse
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import aiohttp
from pydantic import BaseModel, TypeAdapter

from ._http import DEFAULT_API_BASE, DEFAULT_OAUTH_BASE, AuthMode, Params, Transport
from .errors import KickAPIError, MissingTokenError, OAuthError
from .models import (
    AdBreak,
    AdBreakStatus,
    Category,
    Channel,
    ChatMessageSent,
    DropsClaim,
    EventSubscription,
    FailedRedemption,
    KicksLeaderboard,
    Livestream,
    LivestreamsStats,
    Page,
    RedemptionStatus,
    Reward,
    RewardRedemptions,
    SubscriptionResult,
    TokenInfo,
    User,
)
from .oauth import AuthorizationRequest, build_authorization_url
from .scopes import EventType, Scope
from .tokens import Token, TokenStore

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["KickClient"]

M = TypeVar("M", bound=BaseModel)
T = TypeVar("T")

MAX_CHAT_BYTES = 2048


def _data(body: Any) -> Any:
    return body.get("data") if isinstance(body, dict) else body


def _parse(model: type[M], value: Any) -> M:
    return model.model_validate(value)


def _parse_list(model: type[M], value: Any) -> list[M]:
    return TypeAdapter(list[model]).validate_python(value or [])  # type: ignore[valid-type]


def _chunks(values: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _ids(values: Iterable[int | str] | int | str | None) -> list[int] | None:
    if values is None:
        return None
    if isinstance(values, (int, str)):
        values = [values]
    return [int(v) for v in values]


def _strs(values: Iterable[str] | str | None) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        return [values]
    return [str(v) for v in values]


class KickClient:
    """Async client for the official Kick API (https://docs.kick.com).

    Use it as an async context manager, or call :meth:`close` when done::

        async with KickClient(client_id, client_secret) as kick:
            channel = await kick.channels.get_by_slug("berkaydigital")

    Without a bound user, requests use an app access token (client
    credentials), fetched and renewed for you. Endpoints that act on a user's
    behalf need that user's token: authorize them once with
    :meth:`authorization_url` and :meth:`exchange_code`, then use
    :meth:`as_user`::

        me = kick.as_user(user_id)
        await me.chat.send("hello chat")

    Args:
        client_id: Your app's client ID from https://kick.com/settings/developer.
        client_secret: Your app's client secret.
        access_token: Use this fixed token for every request instead of
            managing tokens. Handy for scripts; it is never refreshed.
        token_store: Where user tokens are saved. Defaults to memory. Use
            :class:`~kickpython.SQLiteTokenStore` or
            :class:`~kickpython.JSONFileTokenStore` to survive restarts.
        session: An existing ``aiohttp.ClientSession`` to reuse. The client
            won't close a session it didn't create.
        user_agent: User-Agent header for API requests.
        timeout: Total timeout per HTTP request, in seconds.
        max_retries: Retries for rate limits (429) and failed GET requests.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        access_token: str | None = None,
        token_store: TokenStore | None = None,
        session: aiohttp.ClientSession | None = None,
        user_agent: str | None = None,
        api_base: str = DEFAULT_API_BASE,
        oauth_base: str = DEFAULT_OAUTH_BASE,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._http = Transport(
            client_id=client_id,
            client_secret=client_secret,
            access_token=access_token,
            token_store=token_store,
            session=session,
            user_agent=user_agent,
            api_base=api_base,
            oauth_base=oauth_base,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._user_id: int | None = None
        self._root = True
        self._init_resources()

    def _init_resources(self) -> None:
        self.users = UsersAPI(self)
        self.channels = ChannelsAPI(self)
        self.chat = ChatAPI(self)
        self.moderation = ModerationAPI(self)
        self.rewards = RewardsAPI(self)
        self.livestreams = LivestreamsAPI(self)
        self.categories = CategoriesAPI(self)
        self.events = EventsAPI(self)
        self.kicks = KicksAPI(self)
        self.ads = AdsAPI(self)
        self.drops = DropsAPI(self)

    # Lifecycle --------------------------------------------------------------

    async def __aenter__(self) -> KickClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session (only if the client created it)."""
        if self._root:
            await self._http.close()

    def __repr__(self) -> str:
        bound = f" user_id={self._user_id}" if self._user_id is not None else ""
        return f"<KickClient{bound}>"

    # Users ------------------------------------------------------------------

    @property
    def user_id(self) -> int | None:
        """The user this view acts as, or ``None`` for the app itself."""
        return self._user_id

    @property
    def token_store(self) -> TokenStore:
        return self._http.store

    def as_user(self, user_id: int | str) -> KickClient:
        """A view of this client that acts as ``user_id``, using their stored token.

        The view shares the session, token store and refresh locks with this
        client. It is cheap; create one per request if you like.
        """
        view = object.__new__(KickClient)
        view._http = self._http
        view._user_id = int(user_id)
        view._root = False
        view._init_resources()
        return view

    def _require_user_id(self, value: int | str | None, what: str = "broadcaster_user_id") -> int:
        if value is not None:
            return int(value)
        if self._user_id is not None:
            return self._user_id
        raise ValueError(f"pass {what}, or call this on client.as_user(user_id)")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: AuthMode = "any",
        params: Params = None,
        json: Any = None,
    ) -> Any:
        return await self._http.request(
            method, path, auth=auth, user_id=self._user_id, params=params, json=json
        )

    # OAuth ------------------------------------------------------------------

    def authorization_url(
        self,
        redirect_uri: str,
        scopes: Iterable[Scope | str],
        *,
        state: str | None = None,
    ) -> AuthorizationRequest:
        """Start the authorization code flow (with PKCE).

        Redirect the user to ``request.url`` and keep ``request.state`` and
        ``request.code_verifier`` (in their session, for example) until Kick
        redirects back to ``redirect_uri`` with ``?code=...&state=...``.
        """
        if not self._http.client_id:
            raise MissingTokenError("client_id is required to build an authorization URL")
        return build_authorization_url(
            client_id=self._http.client_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
            oauth_base=self._http.oauth_base,
        )

    async def exchange_code(
        self, code: str, *, code_verifier: str, redirect_uri: str, store: bool = True
    ) -> Token:
        """Exchange the ``code`` from the OAuth callback for a user token.

        The returned token has ``user_id`` filled in, and is saved in the
        token store unless ``store=False``. Finding the user ID needs the
        ``user:read`` or ``channel:read`` scope. Without either, a
        :class:`MissingTokenError` is raised with the unsaved token on
        ``exc.token`` (with ``store=False`` the token is returned with
        ``user_id=None`` instead).
        """
        client_id, secret = self._http.require_credentials()
        data = await self._http.oauth_post(
            "/oauth/token",
            form={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": secret,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "code": code,
            },
        )
        token = Token.from_oauth_response(data)
        try:
            token = token.with_user(await self._lookup_user_id(token))
        except MissingTokenError as exc:
            if not store:
                return token
            exc.token = token  # type: ignore[attr-defined]
            raise
        if store:
            await self._http.store.set(token)
        return token

    async def add_user_token(self, token: Token) -> Token:
        """Save a token you obtained elsewhere. Looks up ``user_id`` if missing."""
        if token.user_id is None:
            token = token.with_user(await self._lookup_user_id(token))
        await self._http.store.set(token)
        return token

    async def _lookup_user_id(self, token: Token) -> int:
        temp = KickClient(
            access_token=token.access_token,
            session=self._http.session,
            api_base=self._http.api_base,
            user_agent=self._http.user_agent,
        )
        unknown = not token.scopes
        if unknown or token.has_scopes(Scope.USER_READ):
            try:
                return (await temp.users.me()).user_id
            except (KickAPIError, MissingTokenError):
                if not unknown:
                    raise
        if unknown or token.has_scopes(Scope.CHANNEL_READ):
            try:
                return (await temp.channels.me()).broadcaster_user_id
            except (KickAPIError, MissingTokenError):
                if not unknown:
                    raise
        raise MissingTokenError(
            "can't tell which user this token belongs to: request the user:read scope"
        )

    async def refresh_user_token(self, user_id: int | str | None = None) -> Token:
        """Refresh a user's token now and save the new one."""
        uid = self._require_user_id(user_id, "user_id")
        return await self._http.user_token(uid, force=True)

    async def refresh_all_user_tokens(
        self, *, concurrency: int = 5
    ) -> dict[int, Token | Exception]:
        """Refresh every stored user token.

        Kick refresh tokens expire after a period of inactivity (the window
        slides on every refresh), so long-idle bots can call this on a timer.
        Failures are returned, not raised.
        """
        ids = await self._http.store.user_ids()
        limit = asyncio.Semaphore(concurrency)

        async def one(uid: int) -> Token:
            async with limit:
                return await self._http.user_token(uid, force=True)

        results = await asyncio.gather(*(one(uid) for uid in ids), return_exceptions=True)
        out: dict[int, Token | Exception] = {}
        for uid, res in zip(ids, results, strict=True):
            if isinstance(res, BaseException) and not isinstance(res, Exception):
                raise res
            out[uid] = res
        return out

    async def get_user_token(self, user_id: int | str | None = None) -> Token:
        """A valid token for the user, refreshed first if it is about to expire."""
        return await self._http.user_token(self._require_user_id(user_id, "user_id"))

    async def get_app_token(self, *, force_new: bool = False) -> Token:
        """The cached app access token (client credentials), fetched if needed."""
        return await self._http.app_token(force=force_new)

    async def revoke_token(
        self, token: str, *, hint: Literal["access_token", "refresh_token"] | None = None
    ) -> None:
        """Revoke an access or refresh token."""
        await self._http.oauth_post(
            "/oauth/revoke", params={"token": token, "token_type_hint": hint}, form={}
        )

    async def revoke_user(self, user_id: int | str | None = None) -> None:
        """Revoke a user's tokens at Kick, then remove them from the store.

        Waits for any refresh in flight first, so the pair that gets revoked
        is the current one. If Kick refuses either revocation the error is
        raised and the token stays stored, so you can retry.
        """
        uid = self._require_user_id(user_id, "user_id")
        async with self._http.exclusive_user(uid):
            token = await self._http.store.get(uid)
            if token is not None:
                if token.refresh_token:
                    await self.revoke_token(token.refresh_token, hint="refresh_token")
                await self.revoke_token(token.access_token, hint="access_token")
            await self._http.store.delete(uid)

    async def introspect(self, access_token: str | None = None) -> TokenInfo:
        """Check a token. Defaults to the token this client would use.

        Revoked, expired or unknown tokens give ``active=False``.
        """
        if access_token is None:
            access_token, _ = await self._http._bearer("any", self._user_id)
        try:
            body = await self._http.oauth_post("/oauth/token/introspect", bearer=access_token)
        except OAuthError as exc:
            if exc.status == 401:  # Kick answers 401 for revoked or unknown tokens
                return TokenInfo(active=False)
            raise
        return _parse(TokenInfo, _data(body))

    async def public_key(self) -> str:
        """Kick's current public key (PEM) for verifying webhook signatures."""
        body = await self._request("GET", "/public/v1/public-key", auth="none")
        return str(_data(body)["public_key"])


class _Resource:
    def __init__(self, client: KickClient) -> None:
        self._client = client

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        return await self._client._request(method, path, **kw)


class UsersAPI(_Resource):
    async def get(self, ids: Iterable[int | str] | int | None = None) -> list[User]:
        """Users by ID. With no IDs, the authorized user (needs a user token)."""
        if ids is None:
            return [await self.me()]
        id_list = _ids(ids) or []
        out: list[User] = []
        for chunk in _chunks(id_list, 50):
            body = await self._req("GET", "/public/v1/users", params={"id": list(chunk)})
            out += _parse_list(User, _data(body))
        return out

    async def me(self) -> User:
        """The user this client acts as."""
        body = await self._req("GET", "/public/v1/users", auth="user")
        users = _parse_list(User, _data(body))
        if not users:
            raise MissingTokenError("Kick returned no user for this token")
        return users[0]


class ChannelsAPI(_Resource):
    async def get(
        self,
        *,
        user_ids: Iterable[int | str] | int | None = None,
        slugs: Iterable[str] | str | None = None,
    ) -> list[Channel]:
        """Channels by broadcaster user ID or by slug (not both).

        With neither, returns the authorized user's channel. Requests over the
        API's 50 item limit are split for you.
        """
        if user_ids is not None and slugs is not None:
            raise ValueError("pass user_ids or slugs, not both")
        if user_ids is None and slugs is None:
            return [await self.me()]
        values: list[Any]
        if user_ids is not None:
            key, values = "broadcaster_user_id", _ids(user_ids) or []
        else:
            key, values = "slug", _strs(slugs) or []
        out: list[Channel] = []
        for chunk in _chunks(values, 50):
            body = await self._req("GET", "/public/v1/channels", params={key: list(chunk)})
            out += _parse_list(Channel, _data(body))
        return out

    async def get_by_slug(self, slug: str) -> Channel | None:
        found = await self.get(slugs=[slug])
        return found[0] if found else None

    async def get_by_user_id(self, user_id: int | str) -> Channel | None:
        found = await self.get(user_ids=[user_id])
        return found[0] if found else None

    async def me(self) -> Channel:
        """The channel of the user this client acts as."""
        body = await self._req("GET", "/public/v1/channels", auth="user")
        channels = _parse_list(Channel, _data(body))
        if not channels:
            raise MissingTokenError("Kick returned no channel for this token")
        return channels[0]

    async def update(
        self,
        *,
        stream_title: str | None = None,
        category_id: int | None = None,
        custom_tags: Sequence[str] | None = None,
    ) -> None:
        """Update the livestream title, category and/or tags. Needs ``channel:write``."""
        body: dict[str, Any] = {}
        if stream_title is not None:
            body["stream_title"] = stream_title
        if category_id is not None:
            body["category_id"] = int(category_id)
        if custom_tags is not None:
            if len(custom_tags) > 10:
                raise ValueError("Kick allows at most 10 custom tags")
            body["custom_tags"] = list(custom_tags)
        if not body:
            raise ValueError("set at least one of stream_title, category_id, custom_tags")
        await self._req("PATCH", "/public/v1/channels", auth="user", json=body)


class ChatAPI(_Resource):
    async def send(
        self,
        content: str,
        *,
        sender: Literal["bot", "user"] = "bot",
        broadcaster_user_id: int | str | None = None,
        reply_to: str | None = None,
    ) -> ChatMessageSent:
        """Send a chat message. Needs ``chat:write``.

        ``sender="bot"`` (default) posts as your app's bot account into the
        chat of the user whose token is used; ``broadcaster_user_id`` is
        ignored. ``sender="user"`` posts as that user into
        ``broadcaster_user_id``'s chat (defaults to their own chat).
        ``reply_to`` is the ID of a message to reply to.

        Kick limits messages to 500 characters and 2048 UTF-8 bytes.
        """
        if not content or not content.strip():
            raise ValueError("content can't be empty")
        if len(content.encode("utf-8")) > MAX_CHAT_BYTES:
            raise ValueError(f"content is over Kick's {MAX_CHAT_BYTES} byte limit")
        body: dict[str, Any] = {"content": content, "type": sender}
        if sender == "user":
            body["broadcaster_user_id"] = self._client._require_user_id(broadcaster_user_id)
        if reply_to:
            body["reply_to_message_id"] = str(reply_to)
        data = _data(await self._req("POST", "/public/v1/chat", auth="user", json=body))
        return _parse(ChatMessageSent, data)

    async def reply(self, message_id: str, content: str, **kwargs: Any) -> ChatMessageSent:
        """Shortcut for ``send(content, reply_to=message_id)``."""
        return await self.send(content, reply_to=message_id, **kwargs)

    async def delete(self, message_id: str) -> None:
        """Delete a chat message. Needs ``moderation:chat_message:manage``."""
        await self._req("DELETE", f"/public/v1/chat/{_seg(message_id)}", auth="user")


class ModerationAPI(_Resource):
    async def ban(
        self,
        user_id: int | str,
        *,
        broadcaster_user_id: int | str | None = None,
        reason: str | None = None,
    ) -> None:
        """Permanently ban a user from a chat. Needs ``moderation:ban``."""
        await self._ban(user_id, broadcaster_user_id, None, reason)

    async def timeout(
        self,
        user_id: int | str,
        minutes: int,
        *,
        broadcaster_user_id: int | str | None = None,
        reason: str | None = None,
    ) -> None:
        """Time a user out for 1 to 10080 minutes (7 days). Needs ``moderation:ban``."""
        if not 1 <= int(minutes) <= 10080:
            raise ValueError("timeout must be between 1 and 10080 minutes")
        await self._ban(user_id, broadcaster_user_id, int(minutes), reason)

    async def _ban(
        self,
        user_id: int | str,
        broadcaster: int | str | None,
        minutes: int | None,
        reason: str | None,
    ) -> None:
        body: dict[str, Any] = {
            "broadcaster_user_id": self._client._require_user_id(broadcaster),
            "user_id": int(user_id),
        }
        if minutes is not None:
            body["duration"] = minutes
        if reason:
            if len(reason) > 100:
                raise ValueError("reason can be at most 100 characters")
            body["reason"] = reason
        await self._req("POST", "/public/v1/moderation/bans", auth="user", json=body)

    async def unban(
        self, user_id: int | str, *, broadcaster_user_id: int | str | None = None
    ) -> None:
        """Lift a ban or timeout. Needs ``moderation:ban``."""
        body = {
            "broadcaster_user_id": self._client._require_user_id(broadcaster_user_id),
            "user_id": int(user_id),
        }
        await self._req("DELETE", "/public/v1/moderation/bans", auth="user", json=body)


class RewardsAPI(_Resource):
    """Channel point rewards. Read needs ``channel:rewards:read``, the rest ``:write``."""

    async def list(self) -> list[Reward]:
        body = await self._req("GET", "/public/v1/channels/rewards", auth="user")
        return _parse_list(Reward, _data(body))

    async def create(
        self,
        title: str,
        cost: int,
        *,
        description: str | None = None,
        background_color: str | None = None,
        is_enabled: bool | None = None,
        is_user_input_required: bool | None = None,
        should_redemptions_skip_request_queue: bool | None = None,
    ) -> Reward:
        body = _drop_none(
            title=title,
            cost=int(cost),
            description=description,
            background_color=background_color,
            is_enabled=is_enabled,
            is_user_input_required=is_user_input_required,
            should_redemptions_skip_request_queue=should_redemptions_skip_request_queue,
        )
        data = _data(await self._req("POST", "/public/v1/channels/rewards", auth="user", json=body))
        return _parse(Reward, data)

    async def update(
        self,
        reward_id: str,
        *,
        title: str | None = None,
        cost: int | None = None,
        description: str | None = None,
        background_color: str | None = None,
        is_enabled: bool | None = None,
        is_paused: bool | None = None,
        is_user_input_required: bool | None = None,
        should_redemptions_skip_request_queue: bool | None = None,
    ) -> Reward:
        """Update a reward. Only the app that created a reward can change it."""
        body = _drop_none(
            title=title,
            cost=cost,
            description=description,
            background_color=background_color,
            is_enabled=is_enabled,
            is_paused=is_paused,
            is_user_input_required=is_user_input_required,
            should_redemptions_skip_request_queue=should_redemptions_skip_request_queue,
        )
        if not body:
            raise ValueError("set at least one field to update")
        data = _data(
            await self._req(
                "PATCH", f"/public/v1/channels/rewards/{_seg(reward_id)}", auth="user", json=body
            )
        )
        return _parse(Reward, data)

    async def delete(self, reward_id: str) -> None:
        """Delete a reward. Only the app that created a reward can delete it."""
        await self._req("DELETE", f"/public/v1/channels/rewards/{_seg(reward_id)}", auth="user")

    async def redemptions(
        self,
        *,
        reward_id: str | None = None,
        status: RedemptionStatus | None = None,
        ids: Iterable[str] | None = None,
        cursor: str | None = None,
    ) -> Page[RewardRedemptions]:
        """Redemptions grouped by reward. Kick defaults ``status`` to ``pending``."""
        id_list = _strs(ids)
        if id_list and (reward_id or status):
            raise ValueError("filtering by ids can't be combined with reward_id or status")
        params = {"reward_id": reward_id, "status": status, "id": id_list, "cursor": cursor}
        body = await self._req(
            "GET", "/public/v1/channels/rewards/redemptions", auth="user", params=params
        )

        async def fetch(next_cursor: str) -> Page[RewardRedemptions]:
            return await self.redemptions(
                reward_id=reward_id, status=status, ids=id_list, cursor=next_cursor
            )

        return _page(RewardRedemptions, body, fetch)

    async def accept(self, ids: Iterable[str] | str) -> builtins.list[FailedRedemption]:
        """Accept pending redemptions (25 per call). Returns the ones that failed."""
        return await self._resolve("accept", ids)

    async def reject(self, ids: Iterable[str] | str) -> builtins.list[FailedRedemption]:
        """Reject pending redemptions (25 per call). Returns the ones that failed."""
        return await self._resolve("reject", ids)

    async def _resolve(
        self, action: str, ids: Iterable[str] | str
    ) -> builtins.list[FailedRedemption]:
        unique = list(dict.fromkeys(_strs(ids) or []))
        failed: builtins.list[FailedRedemption] = []
        for chunk in _chunks(unique, 25):
            body = await self._req(
                "POST",
                f"/public/v1/channels/rewards/redemptions/{action}",
                auth="user",
                json={"ids": list(chunk)},
            )
            failed += _parse_list(FailedRedemption, _data(body))
        return failed


class LivestreamsAPI(_Resource):
    async def list(
        self,
        *,
        category_ids: Iterable[int] | int | None = None,
        language_codes: Iterable[str] | str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[Livestream]:
        """Live streams, oldest first, with cursor pagination (up to 1000 per page)."""
        cats, langs = _ids(category_ids), _strs(language_codes)
        params = {"category_id": cats, "language_code": langs, "limit": limit, "cursor": cursor}
        body = await self._req("GET", "/public/v2/livestreams", params=params)

        async def fetch(next_cursor: str) -> Page[Livestream]:
            return await self.list(
                category_ids=cats, language_codes=langs, limit=limit, cursor=next_cursor
            )

        return _page(Livestream, body, fetch)

    async def for_users(self, user_ids: Iterable[int | str] | int) -> builtins.list[Livestream]:
        """Current livestreams of these broadcasters (offline ones are left out)."""
        out: builtins.list[Livestream] = []
        for chunk in _chunks(_ids(user_ids) or [], 100):
            body = await self._req(
                "GET", "/public/v1/users/livestreams", params={"user_id": list(chunk)}
            )
            out += _parse_list(Livestream, _data(body))
        return out

    async def stats(self) -> LivestreamsStats:
        return _parse(
            LivestreamsStats, _data(await self._req("GET", "/public/v1/livestreams/stats"))
        )


class CategoriesAPI(_Resource):
    async def list(
        self,
        *,
        names: Iterable[str] | str | None = None,
        tags: Iterable[str] | str | None = None,
        ids: Iterable[int] | int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[Category]:
        """Search categories by name or tag, or fetch them by ID. Paginated."""
        name_list, tag_list, id_list = _strs(names), _strs(tags), _ids(ids)
        params = {
            # The spec says comma separated, but the API only accepts repeated keys.
            "name": name_list,
            "tag": tag_list,
            "id": id_list,
            "limit": limit,
            "cursor": cursor,
        }
        body = await self._req("GET", "/public/v2/categories", params=params)

        async def fetch(next_cursor: str) -> Page[Category]:
            return await self.list(
                names=name_list, tags=tag_list, ids=id_list, limit=limit, cursor=next_cursor
            )

        return _page(Category, body, fetch)

    async def search(self, name: str, *, limit: int | None = None) -> Page[Category]:
        return await self.list(names=[name], limit=limit)

    async def get(self, category_id: int) -> Category | None:
        page = await self.list(ids=[category_id])
        return page.items[0] if page.items else None


EventSpec = EventType | str | tuple[EventType | str, int]


class EventsAPI(_Resource):
    """Webhook event subscriptions. See :mod:`kickpython.webhooks` to receive them."""

    async def list(
        self, *, broadcaster_user_id: int | str | None = None
    ) -> builtins.list[EventSubscription]:
        params = {"broadcaster_user_id": broadcaster_user_id}
        body = await self._req("GET", "/public/v1/events/subscriptions", params=params)
        return _parse_list(EventSubscription, _data(body))

    async def subscribe(
        self,
        events: Iterable[EventSpec] | EventSpec,
        *,
        broadcaster_user_id: int | str | None = None,
    ) -> builtins.list[SubscriptionResult]:
        """Subscribe to webhook events.

        Items are event names, optionally as ``(name, version)`` tuples
        (default version 1). With an app token, ``broadcaster_user_id`` is
        required; with a user token Kick uses the token's user. Check
        ``result.ok`` on each entry: Kick reports per-event errors in the body.
        """
        if isinstance(events, (str, EventType)) or _is_event_spec_tuple(events):
            events = [events]  # type: ignore[list-item]
        payload: builtins.list[dict[str, Any]] = []
        for ev in events:
            name, version = ev if isinstance(ev, tuple) else (ev, 1)
            payload.append({"name": str(name), "version": int(version)})
        body: dict[str, Any] = {"events": payload, "method": "webhook"}
        if broadcaster_user_id is not None:
            body["broadcaster_user_id"] = int(broadcaster_user_id)
        elif self._client.user_id is None and not self._client._http.static_token:
            raise ValueError("broadcaster_user_id is required when subscribing with an app token")
        data = _data(await self._req("POST", "/public/v1/events/subscriptions", json=body))
        return _parse_list(SubscriptionResult, data)

    async def unsubscribe(self, subscription_ids: Iterable[str] | str) -> None:
        ids = _strs(subscription_ids) or []
        for chunk in _chunks(ids, 50):
            await self._req("DELETE", "/public/v1/events/subscriptions", params={"id": list(chunk)})

    async def unsubscribe_all(self, *, broadcaster_user_id: int | str | None = None) -> int:
        """Remove every subscription visible to this token. Returns how many."""
        subs = await self.list(broadcaster_user_id=broadcaster_user_id)
        if subs:
            await self.unsubscribe([s.id for s in subs])
        return len(subs)


class KicksAPI(_Resource):
    async def leaderboard(self, *, top: int = 10) -> KicksLeaderboard:
        """KICKs gifting leaderboard (lifetime, month, week). Needs ``kicks:read``."""
        if not 1 <= top <= 100:
            raise ValueError("top must be between 1 and 100")
        body = await self._req(
            "GET", "/public/v1/kicks/leaderboard", auth="user", params={"top": top}
        )
        return _parse(KicksLeaderboard, _data(body))


class AdsAPI(_Resource):
    async def start_break(self, duration_seconds: int, *, id: str | None = None) -> AdBreak:
        """Start an ad break of 7 to 90 seconds. Needs ``ads:write``.

        ``id`` is an idempotency key (UUID); one is generated if omitted.
        Errors carry a reason in ``exc.data``, such as ``CHANNEL_IS_NOT_LIVE``.
        """
        if not 7 <= duration_seconds <= 90:
            raise ValueError("ad breaks must be 7 to 90 seconds long")
        body = {"break_duration_seconds": int(duration_seconds), "id": id or str(uuid.uuid4())}
        data = _data(await self._req("POST", "/public/v1/ads/ad-break", auth="user", json=body))
        return _parse(AdBreak, data)

    async def status(self) -> AdBreakStatus:
        """Ad settings and recent ad breaks. Needs ``ads:read`` or ``ads:write``."""
        body = await self._req("GET", "/public/v1/ads/ad-break-status", auth="user")
        return _parse(AdBreakStatus, _data(body))

    async def enroll(self) -> None:
        """Enroll the channel in ads. Needs ``ads:write``."""
        await self._req("POST", "/public/v1/ads/enroll", auth="user")


class DropsAPI(_Resource):
    """Drops reward claims. Only for apps owned by an organization; uses the app token."""

    async def claims(
        self,
        *,
        campaign_id: str | None = None,
        user_id: int | None = None,
        claim_id: str | None = None,
        external_status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[DropsClaim]:
        params = {
            "campaign_id": campaign_id,
            "user_id": user_id,
            "claim_id": claim_id,
            "external_status": external_status,
            "limit": limit,
            "cursor": cursor,
        }
        body = await self._req("GET", "/public/v1/drops/claims", auth="app", params=params)
        data = _data(body) or {}
        items = _parse_list(DropsClaim, data.get("claims"))

        async def fetch(next_cursor: str) -> Page[DropsClaim]:
            return await self.claims(
                campaign_id=campaign_id,
                user_id=user_id,
                claim_id=claim_id,
                external_status=external_status,
                limit=limit,
                cursor=next_cursor,
            )

        return Page(items=items, next_cursor=data.get("cursor") or None, _fetch=fetch)

    async def update_claims(self, statuses: Mapping[str, str]) -> None:
        """Set ``external_status`` per claim ID, e.g. ``{claim_id: "processed"}``."""
        items = [{"claim_id": k, "external_status": v} for k, v in statuses.items()]
        for chunk in _chunks(items, 100):
            await self._req(
                "PATCH", "/public/v1/drops/claims", auth="app", json={"claims": list(chunk)}
            )


def _is_event_spec_tuple(value: Any) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], (str, EventType))
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _seg(value: str) -> str:
    """Quote an ID for use as one URL path segment."""
    text = str(value)
    if text in ("", ".", ".."):
        raise ValueError(f"invalid ID {text!r}")
    return urllib.parse.quote(text, safe="")


def _drop_none(**fields: Any) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if v is not None}


def _page(model: type[M], body: Any, fetch: Any) -> Page[M]:
    items = _parse_list(model, _data(body))
    pagination = body.get("pagination") if isinstance(body, dict) else None
    cursor = pagination.get("next_cursor") if isinstance(pagination, dict) else None
    return Page(items=items, next_cursor=cursor or None, _fetch=fetch)
