"""HTTP transport shared by a client and its user-bound views."""

from __future__ import annotations

import asyncio
import json as jsonlib
import logging
import random
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any, Literal

import aiohttp

from ._version import __version__
from .errors import (
    KickAPIError,
    MissingTokenError,
    OAuthError,
    RateLimitError,
    error_for_status,
)
from .tokens import MemoryTokenStore, Token, TokenStore

log = logging.getLogger("kickpython")

AuthMode = Literal["user", "any", "app", "none"]
"""How a request authenticates.

``user``: needs the bound user's token. ``any``: the bound user's token if
there is one, else an app token. ``app``: always an app token. ``none``: no
Authorization header.
"""

DEFAULT_API_BASE = "https://api.kick.com"
DEFAULT_OAUTH_BASE = "https://id.kick.com"
REFRESH_MARGIN = 60.0  # refresh access tokens this many seconds before they expire
MAX_RETRY_SLEEP = 60.0

Params = Mapping[str, Any] | Iterable[tuple[str, Any]] | None


def _encode_params(params: Params) -> list[tuple[str, str]]:
    """Flatten params. Lists become repeated keys; ``None`` values are dropped."""
    if params is None:
        return []
    items = params.items() if isinstance(params, Mapping) else params
    out: list[tuple[str, str]] = []
    for key, value in items:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            out.extend((key, _scalar(v)) for v in value)
        else:
            out.append((key, _scalar(value)))
    return out


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value.value if hasattr(value, "value") else value)


class Transport:
    def __init__(
        self,
        *,
        client_id: str | None,
        client_secret: str | None,
        access_token: str | None,
        token_store: TokenStore | None,
        session: aiohttp.ClientSession | None,
        user_agent: str | None,
        api_base: str,
        oauth_base: str,
        timeout: float,
        max_retries: int,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.static_token = access_token
        self.store: TokenStore = token_store if token_store is not None else MemoryTokenStore()
        self._session = session
        self._owns_session = session is None
        self.user_agent = (
            user_agent
            or f"kickpython/{__version__} (+https://github.com/berkay-digital/kickpython)"
        )
        self.api_base = api_base.rstrip("/")
        self.oauth_base = oauth_base.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max(0, max_retries)
        self._app_token: Token | None = None
        self._app_lock = asyncio.Lock()
        self._user_locks: dict[int, asyncio.Lock] = {}

    # Session --------------------------------------------------------------

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            if not self._owns_session:
                raise RuntimeError("the aiohttp session passed to KickClient is closed")
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # OAuth server ---------------------------------------------------------

    def require_credentials(self) -> tuple[str, str]:
        if not self.client_id or not self.client_secret:
            raise MissingTokenError(
                "client_id and client_secret are required for this (OAuth token requests)"
            )
        return self.client_id, self.client_secret

    async def oauth_post(
        self,
        path: str,
        *,
        form: Mapping[str, str] | None = None,
        params: Params = None,
        bearer: str | None = None,
    ) -> Any:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        url = f"{self.oauth_base}{path}"
        async with self.session.post(
            url,
            data=dict(form) if form is not None else None,
            params=_encode_params(params),
            headers=headers,
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise OAuthError(
                    f"{resp.status} from {path}: {_short(text)}", status=resp.status, body=text
                )
            if not text.strip():
                return None
            try:
                return await resp.json(content_type=None)
            except ValueError:
                return text

    async def fetch_app_token(self) -> Token:
        client_id, secret = self.require_credentials()
        data = await self.oauth_post(
            "/oauth/token",
            form={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
            },
        )
        return Token.from_oauth_response(data)

    async def app_token(self, *, force: bool = False, failed: str | None = None) -> Token:
        async with self._app_lock:
            tok = self._app_token
            if tok is not None and failed is not None and tok.access_token != failed:
                return tok  # someone else already replaced the failing token
            if force or tok is None or tok.is_expired(REFRESH_MARGIN):
                tok = await self.fetch_app_token()
                self._app_token = tok
                log.debug("fetched new app access token")
            return tok

    async def refresh(self, token: Token) -> Token:
        client_id, secret = self.require_credentials()
        if not token.refresh_token:
            raise MissingTokenError(f"token for user {token.user_id} has no refresh token")
        try:
            data = await self.oauth_post(
                "/oauth/token",
                form={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": secret,
                    "refresh_token": token.refresh_token,
                },
            )
        except OAuthError as exc:
            if exc.status in (400, 401):
                raise MissingTokenError(
                    f"refreshing the token for user {token.user_id} failed ({exc.status}); "
                    "the user needs to authorize the app again"
                ) from exc
            raise
        new = Token.from_oauth_response(data, user_id=token.user_id)
        if not new.scopes:
            new = replace(new, scopes=token.scopes)
        await self.store.set(new)
        log.info("refreshed access token for user %s", token.user_id)
        return new

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = self._user_locks[user_id] = asyncio.Lock()
        return lock

    async def user_token(
        self, user_id: int, *, force: bool = False, failed: str | None = None
    ) -> Token:
        async with self._lock_for(user_id):
            tok = await self.store.get(user_id)
            if tok is None:
                raise MissingTokenError(
                    f"no token stored for user {user_id}; run the OAuth flow for this user first"
                )
            if failed is not None and tok.access_token != failed:
                return tok
            if force or tok.is_expired(REFRESH_MARGIN):
                tok = await self.refresh(tok)
            return tok

    async def _introspect(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        try:
            body = await self.oauth_post("/oauth/token/introspect", bearer=token)
        except (OAuthError, aiohttp.ClientError, asyncio.TimeoutError):
            return None
        data = body.get("data") if isinstance(body, dict) else None
        return data if isinstance(data, dict) else None

    # API server -----------------------------------------------------------

    async def _bearer(self, auth: AuthMode, user_id: int | None) -> tuple[str | None, str]:
        """Return (access token, kind) where kind is static/user/app/none."""
        if auth == "none":
            return None, "none"
        if self.static_token:
            return self.static_token, "static"
        if auth in ("user", "any") and user_id is not None:
            return (await self.user_token(user_id)).access_token, "user"
        if auth == "user":
            raise MissingTokenError(
                "this endpoint needs a user access token: use client.as_user(user_id) "
                "or pass access_token="
            )
        return (await self.app_token()).access_token, "app"

    async def request(
        self,
        method: str,
        path: str,
        *,
        auth: AuthMode = "any",
        user_id: int | None = None,
        params: Params = None,
        json: Any = None,
    ) -> Any:
        """Call the API and return the decoded JSON body (``None`` for empty bodies)."""
        url = f"{self.api_base}{path}"
        query = _encode_params(params)
        token, kind = await self._bearer(auth, user_id)
        reauthed = False
        attempt = 0
        while True:
            headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                async with self.session.request(
                    method, url, params=query, json=json, headers=headers
                ) as resp:
                    status = resp.status
                    text = await resp.text()
                    retry_after = resp.headers.get("Retry-After")
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
                if method == "GET" and attempt < self.max_retries:
                    attempt += 1
                    await asyncio.sleep(_backoff(attempt))
                    log.warning("%s %s failed (%s), retrying", method, path, exc)
                    continue
                raise

            if status < 400:
                return _decode(text)

            if status == 401 and not reauthed and kind in ("user", "app"):
                reauthed = True
                # Kick also answers 401 when a valid token lacks the endpoint's
                # scope. Refreshing can't fix that, so check the token first.
                info = await self._introspect(token)
                if info is not None and info.get("active"):
                    err = _api_error(status, text, method, path, retry_after)
                    scopes = info.get("scope") or "none"
                    err.args = (
                        f"{err.args[0]}: the {kind} token is valid, so it most likely lacks "
                        f"the scope this endpoint needs (token scopes: {scopes})",
                    )
                    raise err
                log.info("%s %s returned 401, refreshing the %s token", method, path, kind)
                if kind == "user":
                    assert user_id is not None
                    token = (await self.user_token(user_id, force=True, failed=token)).access_token
                else:
                    token = (await self.app_token(force=True, failed=token)).access_token
                continue

            wait: float | None = None
            if status == 429:
                wait = _parse_retry_after(retry_after) or _backoff(attempt + 1)
            elif status >= 500 and method == "GET":
                wait = _backoff(attempt + 1)
            if wait is not None and attempt < self.max_retries and wait <= MAX_RETRY_SLEEP:
                attempt += 1
                log.warning("%s %s returned %s, retrying in %.1fs", method, path, status, wait)
                await asyncio.sleep(wait)
                continue

            raise _api_error(status, text, method, path, retry_after)


def _decode(text: str) -> Any:
    if not text.strip():
        return None
    try:
        return jsonlib.loads(text)
    except ValueError:
        return text


def _api_error(
    status: int, text: str, method: str, path: str, retry_after: str | None
) -> KickAPIError:
    body = _decode(text)
    message, data = "", None
    if isinstance(body, dict):
        message = str(body.get("message") or "")
        data = body.get("data")
        if not message and isinstance(data, dict):
            message = str(data.get("type") or data.get("message") or "")
    elif isinstance(body, str):
        message = _short(body)
    cls = error_for_status(status)
    message = message or "request failed"
    if cls is RateLimitError:
        return RateLimitError(
            status,
            message,
            data=data,
            method=method,
            url=path,
            retry_after=_parse_retry_after(retry_after),
        )
    return cls(status, message, data=data, method=method, url=path)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    delay: float = min(MAX_RETRY_SLEEP, 0.5 * 2.0 ** (attempt - 1))
    return delay + random.uniform(0, 0.25)


def _short(text: str, limit: int = 300) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."
