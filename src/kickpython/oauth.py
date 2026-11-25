"""OAuth 2.1 helpers: PKCE and authorization URLs.

The network calls (code exchange, refresh, revoke, introspect) live on
:class:`kickpython.KickClient`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass

from .scopes import Scope

__all__ = [
    "AuthorizationRequest",
    "build_authorization_url",
    "code_challenge",
    "generate_code_verifier",
]

DEFAULT_OAUTH_BASE = "https://id.kick.com"


def generate_code_verifier(length: int = 64) -> str:
    """A random PKCE code verifier (43 to 128 URL-safe characters)."""
    if not 43 <= length <= 128:
        raise ValueError("PKCE code verifier length must be between 43 and 128")
    return secrets.token_urlsafe(96)[:length]


def code_challenge(verifier: str) -> str:
    """The S256 code challenge for ``verifier``."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class AuthorizationRequest:
    """Send the user to ``url``. Keep ``state`` and ``code_verifier`` until the callback."""

    url: str
    state: str
    code_verifier: str
    redirect_uri: str
    scopes: tuple[str, ...]


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: Iterable[Scope | str],
    state: str | None = None,
    code_verifier: str | None = None,
    oauth_base: str = DEFAULT_OAUTH_BASE,
) -> AuthorizationRequest:
    """Build the ``/oauth/authorize`` URL for the authorization code flow with PKCE."""
    scope_list = tuple(dict.fromkeys(str(s) for s in scopes))
    if not scope_list:
        raise ValueError("request at least one scope")
    state = state or secrets.token_urlsafe(16)
    code_verifier = code_verifier or generate_code_verifier()
    params: list[tuple[str, str]] = [
        ("response_type", "code"),
        ("client_id", client_id),
    ]
    # Kick's frontend (Next.js) rewrites the first "127.0.0.1" in the URL to
    # "localhost", which breaks exact redirect URI matching. A sacrificial
    # parameter placed before redirect_uri absorbs the rewrite.
    # https://docs.kick.com/getting-started/generating-tokens-oauth2-flow#workaround-for-127.0.0.1-host-redirect-uri
    if "127.0.0.1" in redirect_uri:
        params.append(("redirect", "127.0.0.1"))
    params += [
        ("redirect_uri", redirect_uri),
        ("scope", " ".join(scope_list)),
        ("code_challenge", code_challenge(code_verifier)),
        ("code_challenge_method", "S256"),
        ("state", state),
    ]
    url = f"{oauth_base.rstrip('/')}/oauth/authorize?{urllib.parse.urlencode(params)}"
    return AuthorizationRequest(
        url=url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        scopes=scope_list,
    )
