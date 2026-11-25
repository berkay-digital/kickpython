from __future__ import annotations

import json
import sqlite3
import time
import urllib.parse
from pathlib import Path

import pytest

from kickpython import (
    JSONFileTokenStore,
    MemoryTokenStore,
    Scope,
    SQLiteTokenStore,
    Token,
    build_authorization_url,
)
from kickpython.oauth import code_challenge, generate_code_verifier


def test_code_challenge_matches_rfc7636_example() -> None:
    # RFC 7636, appendix B
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_code_verifier_length() -> None:
    assert len(generate_code_verifier()) == 64
    assert len(generate_code_verifier(43)) == 43
    with pytest.raises(ValueError):
        generate_code_verifier(20)


def _query(url: str) -> list[tuple[str, str]]:
    return urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)


def test_authorization_url() -> None:
    req = build_authorization_url(
        client_id="cid",
        redirect_uri="http://localhost:9765/cb",
        scopes=[Scope.CHAT_WRITE, "user:read", Scope.CHAT_WRITE],
        state="st",
    )
    q = dict(_query(req.url))
    assert req.url.startswith("https://id.kick.com/oauth/authorize?")
    assert q["response_type"] == "code"
    assert q["client_id"] == "cid"
    assert q["redirect_uri"] == "http://localhost:9765/cb"
    assert q["scope"] == "chat:write user:read"
    assert q["state"] == "st" == req.state
    assert q["code_challenge"] == code_challenge(req.code_verifier)
    assert q["code_challenge_method"] == "S256"
    assert "redirect" not in q


def test_authorization_url_127_workaround() -> None:
    req = build_authorization_url(
        client_id="c", redirect_uri="http://127.0.0.1:8000/cb", scopes=["user:read"]
    )
    keys = [k for k, _ in _query(req.url)]
    assert keys.index("redirect") < keys.index("redirect_uri")


def test_authorization_url_needs_scopes() -> None:
    with pytest.raises(ValueError):
        build_authorization_url(client_id="c", redirect_uri="http://x", scopes=[])


def test_token_from_oauth_response() -> None:
    before = time.time()
    tok = Token.from_oauth_response(
        {
            "access_token": "SECRET-ACCESS",
            "refresh_token": "SECRET-REFRESH",
            "expires_in": 7200,
            "refresh_expires_in": 2592000,
            "scope": "user:read chat:write",
            "token_type": "Bearer",
        },
        user_id=5,
    )
    assert tok.user_id == 5
    assert tok.scopes == ("user:read", "chat:write")
    assert tok.has_scopes(Scope.USER_READ, "chat:write")
    assert not tok.has_scopes(Scope.MODERATION_BAN)
    assert tok.expires_at is not None and before + 7199 <= tok.expires_at <= time.time() + 7200
    assert tok.refresh_expires_at is not None
    assert not tok.is_expired()
    assert tok.is_expired(margin=8000)
    assert "SECRET" not in repr(tok)
    assert Token.from_dict(tok.to_dict()) == tok


@pytest.mark.parametrize("kind", ["memory", "json", "sqlite"])
async def test_token_stores(kind: str, tmp_path: Path) -> None:
    store = {
        "memory": lambda: MemoryTokenStore(),
        "json": lambda: JSONFileTokenStore(tmp_path / "t.json"),
        "sqlite": lambda: SQLiteTokenStore(tmp_path / "t.db"),
    }[kind]()
    tok = Token(
        "acc", expires_at=1234567890.0, refresh_token="ref", scopes=("user:read",), user_id=42
    )
    assert await store.get(42) is None
    await store.set(tok)
    got = await store.get(42)
    assert got is not None
    assert (got.access_token, got.refresh_token, got.scopes, got.user_id) == (
        "acc",
        "ref",
        ("user:read",),
        42,
    )
    assert int(got.expires_at or 0) == 1234567890
    assert await store.user_ids() == [42]
    await store.delete(42)
    assert await store.get(42) is None
    with pytest.raises(ValueError):
        await store.set(Token("x"))


async def test_json_store_permissions(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    await JSONFileTokenStore(path).set(Token("a", user_id=1, refresh_token="r"))
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["1"]["access_token"] == "a"


async def test_sqlite_store_reads_legacy_database(tmp_path: Path) -> None:
    db = tmp_path / "kick_tokens.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE tokens (channel_id TEXT PRIMARY KEY, access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL, expires_at INTEGER NOT NULL, scope TEXT)""")
    conn.execute(
        "INSERT INTO tokens VALUES ('1710384', 'old-acc', 'old-ref', 1700000000, "
        "'chat:write user:read')"
    )
    conn.commit()
    conn.close()
    store = SQLiteTokenStore(db)
    tok = await store.get(1710384)
    assert tok is not None
    assert tok.refresh_token == "old-ref"
    assert tok.scopes == ("chat:write", "user:read")
    assert tok.is_expired()
    await store.set(
        Token(
            "new",
            expires_at=2e9,
            refresh_token="new-ref",
            user_id=1710384,
            refresh_expires_at=2.1e9,
        )
    )
    again = await store.get(1710384)
    assert again is not None and again.refresh_expires_at == 2.1e9


async def test_sqlite_store_is_private_and_handles_unknown_expiry(tmp_path: Path) -> None:
    import os

    path = tmp_path / "new.db"
    old = os.umask(0o022)
    try:
        store = SQLiteTokenStore(path)
        await store.set(Token("a", refresh_token="r", user_id=1))
    finally:
        os.umask(old)
    assert path.stat().st_mode & 0o777 == 0o600
    tok = await store.get(1)
    assert tok is not None and tok.expires_at is None and not tok.is_expired()


def test_single_scope_string_is_not_split_into_letters() -> None:
    req = build_authorization_url(client_id="c", redirect_uri="http://x", scopes=Scope.USER_READ)
    assert req.scopes == ("user:read",)
    req = build_authorization_url(
        client_id="c", redirect_uri="http://x", scopes="user:read chat:write"
    )
    assert req.scopes == ("user:read", "chat:write")


async def test_sqlite_memory_store() -> None:
    store = SQLiteTokenStore(":memory:")
    await store.set(Token("a", refresh_token="r", user_id=1, expires_at=2e9))
    got = await store.get(1)
    assert got is not None and got.access_token == "a"
    assert await store.user_ids() == [1]


async def test_sqlite_store_creates_parent_dirs(tmp_path: Path) -> None:
    store = SQLiteTokenStore(tmp_path / "nested" / "dir" / "t.db")
    await store.set(Token("a", refresh_token="r", user_id=1))
    assert (tmp_path / "nested" / "dir" / "t.db").exists()
