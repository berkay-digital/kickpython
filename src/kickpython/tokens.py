"""OAuth tokens and pluggable storage for them.

User tokens are stored by Kick user ID. The client refreshes them on demand
and writes the new pair back to the store, so a bot keeps working across
restarts as long as its store persists.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "JSONFileTokenStore",
    "MemoryTokenStore",
    "SQLiteTokenStore",
    "Token",
    "TokenStore",
]


@dataclass(frozen=True)
class Token:
    """An access token, plus its refresh token for user tokens."""

    access_token: str
    expires_at: float | None = None
    """Unix time the access token expires, or ``None`` if unknown."""
    refresh_token: str | None = None
    scopes: tuple[str, ...] = ()
    token_type: str = "Bearer"
    user_id: int | None = None
    """The Kick user the token belongs to. ``None`` for app tokens."""
    refresh_expires_at: float | None = None
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __repr__(self) -> str:  # never print secrets
        return (
            f"Token(user_id={self.user_id!r}, scopes={self.scopes!r}, "
            f"expires_at={self.expires_at!r}, refreshable={self.refresh_token is not None})"
        )

    @property
    def is_user_token(self) -> bool:
        return self.refresh_token is not None

    def expires_in(self, now: float | None = None) -> float | None:
        if self.expires_at is None:
            return None
        return self.expires_at - (time.time() if now is None else now)

    def is_expired(self, margin: float = 0.0, now: float | None = None) -> bool:
        remaining = self.expires_in(now)
        return remaining is not None and remaining <= margin

    def has_scopes(self, *scopes: str) -> bool:
        return all(str(s) in self.scopes for s in scopes)

    def with_user(self, user_id: int) -> Token:
        return replace(self, user_id=user_id)

    @classmethod
    def from_oauth_response(cls, data: Mapping[str, Any], *, user_id: int | None = None) -> Token:
        """Build a token from the JSON that ``POST /oauth/token`` returns."""
        if not isinstance(data, Mapping) or not data.get("access_token"):
            from .errors import OAuthError

            raise OAuthError(f"unexpected token response from Kick: {str(data)[:200]!r}")
        now = time.time()
        expires_in = data.get("expires_in")
        refresh_in = data.get("refresh_expires_in")
        scope = data.get("scope") or ""
        known = {
            "access_token",
            "expires_in",
            "refresh_token",
            "scope",
            "token_type",
            "refresh_expires_in",
        }
        return cls(
            access_token=str(data["access_token"]),
            expires_at=now + float(expires_in) if expires_in not in (None, "") else None,
            refresh_token=data.get("refresh_token") or None,
            scopes=tuple(scope.split()) if isinstance(scope, str) else tuple(scope),
            token_type=str(data.get("token_type") or "Bearer"),
            user_id=user_id,
            refresh_expires_at=now + float(refresh_in) if refresh_in not in (None, "") else None,
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "refresh_token": self.refresh_token,
            "scopes": list(self.scopes),
            "token_type": self.token_type,
            "user_id": self.user_id,
            "refresh_expires_at": self.refresh_expires_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Token:
        user_id = data.get("user_id")
        return cls(
            access_token=data["access_token"],
            expires_at=data.get("expires_at"),
            refresh_token=data.get("refresh_token"),
            scopes=tuple(data.get("scopes") or ()),
            token_type=data.get("token_type") or "Bearer",
            user_id=int(user_id) if user_id is not None else None,
            refresh_expires_at=data.get("refresh_expires_at"),
        )


@runtime_checkable
class TokenStore(Protocol):
    """Where user tokens live. Implement these four methods to use your own database."""

    async def get(self, user_id: int) -> Token | None: ...

    async def set(self, token: Token) -> None:
        """Save ``token``. ``token.user_id`` is always set."""
        ...

    async def delete(self, user_id: int) -> None: ...

    async def user_ids(self) -> list[int]: ...


class MemoryTokenStore:
    """Keeps tokens in memory. The default; tokens are lost when the process exits."""

    def __init__(self, tokens: Iterable[Token] = ()) -> None:
        self._tokens: dict[int, Token] = {}
        for t in tokens:
            if t.user_id is None:
                raise ValueError("tokens in a store need a user_id")
            self._tokens[t.user_id] = t

    async def get(self, user_id: int) -> Token | None:
        return self._tokens.get(int(user_id))

    async def set(self, token: Token) -> None:
        if token.user_id is None:
            raise ValueError("token.user_id must be set")
        self._tokens[token.user_id] = token

    async def delete(self, user_id: int) -> None:
        self._tokens.pop(int(user_id), None)

    async def user_ids(self) -> list[int]:
        return list(self._tokens)


class JSONFileTokenStore:
    """Stores tokens in a JSON file, written atomically with 0600 permissions."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} does not contain a JSON object")
        return data

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".kick-tokens-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    async def get(self, user_id: int) -> Token | None:
        async with self._lock:
            data = await asyncio.to_thread(self._read)
        entry = data.get(str(int(user_id)))
        return Token.from_dict(entry) if entry else None

    async def set(self, token: Token) -> None:
        if token.user_id is None:
            raise ValueError("token.user_id must be set")
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            data[str(token.user_id)] = token.to_dict()
            await asyncio.to_thread(self._write, data)

    async def delete(self, user_id: int) -> None:
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            if data.pop(str(int(user_id)), None) is not None:
                await asyncio.to_thread(self._write, data)

    async def user_ids(self) -> list[int]:
        async with self._lock:
            data = await asyncio.to_thread(self._read)
        return [int(k) for k in data]


def _private_file(path: str) -> None:
    """Make sure ``path`` exists and only its owner can read it."""
    if os.path.exists(path):
        if os.name == "posix" and os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o600)
            logging.getLogger("kickpython").info("restricted %s to owner-only access", path)
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    with contextlib.suppress(FileExistsError):  # created concurrently: fine
        os.close(os.open(path, flags, 0o600))


class SQLiteTokenStore:
    """Stores tokens in SQLite.

    Uses the same ``tokens`` table as kickpython 0.x (``channel_id`` holds the
    Kick user ID), so an existing ``kick_tokens.db`` keeps working. Newer columns
    are added on first use.
    """

    def __init__(self, path: str | os.PathLike[str] = "kick_tokens.db") -> None:
        self.path = str(path)
        self._lock = asyncio.Lock()
        self._ready = False
        # An in-memory database only lives as long as its connection.
        self._memory = (
            sqlite3.connect(":memory:", check_same_thread=False)
            if (self.path == ":memory:")
            else None
        )

    def _connect(self) -> sqlite3.Connection:
        if self._memory is not None:
            conn = self._memory
        else:
            if not self._ready:
                _private_file(self.path)
            conn = sqlite3.connect(self.path)
        if not self._ready:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tokens (
                    channel_id TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    scope TEXT
                )"""
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tokens)")}
            if "refresh_expires_at" not in cols:
                conn.execute("ALTER TABLE tokens ADD COLUMN refresh_expires_at INTEGER")
            conn.commit()
            self._ready = True
        return conn

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            if conn is not self._memory:
                conn.close()

    def _get(self, user_id: int) -> Token | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT access_token, refresh_token, expires_at, scope, refresh_expires_at "
                "FROM tokens WHERE channel_id = ?",
                (str(user_id),),
            ).fetchone()
        if row is None:
            return None
        access, refresh, expires_at, scope, refresh_expires_at = row
        return Token(
            access_token=access,
            refresh_token=refresh or None,
            expires_at=float(expires_at) if expires_at else None,  # 0 means unknown
            scopes=tuple((scope or "").split()),
            user_id=int(user_id),
            refresh_expires_at=float(refresh_expires_at) if refresh_expires_at else None,
        )

    def _set(self, token: Token) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tokens (channel_id, access_token, refresh_token, "
                "expires_at, scope, refresh_expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(token.user_id),
                    token.access_token,
                    token.refresh_token or "",
                    int(token.expires_at or 0),
                    " ".join(token.scopes),
                    int(token.refresh_expires_at) if token.refresh_expires_at else None,
                ),
            )
            conn.commit()

    def _delete(self, user_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM tokens WHERE channel_id = ?", (str(user_id),))
            conn.commit()

    def _ids(self) -> list[int]:
        with self._conn() as conn:
            rows = conn.execute("SELECT channel_id FROM tokens").fetchall()
        return [int(r[0]) for r in rows if str(r[0]).isdigit()]

    async def get(self, user_id: int) -> Token | None:
        async with self._lock:
            return await asyncio.to_thread(self._get, int(user_id))

    async def set(self, token: Token) -> None:
        if token.user_id is None:
            raise ValueError("token.user_id must be set")
        async with self._lock:
            await asyncio.to_thread(self._set, token)

    async def delete(self, user_id: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete, int(user_id))

    async def user_ids(self) -> list[int]:
        async with self._lock:
            return await asyncio.to_thread(self._ids)
