"""Exceptions raised by kickpython."""

from __future__ import annotations

from typing import Any

__all__ = [
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "KickAPIError",
    "KickError",
    "MissingTokenError",
    "NotFoundError",
    "OAuthError",
    "RateLimitError",
    "ServerError",
    "UnauthorizedError",
    "WebhookVerificationError",
]


class KickError(Exception):
    """Base class for every error raised by kickpython."""


class MissingTokenError(KickError):
    """No usable token for the request (not authorized, or the refresh token expired)."""


class OAuthError(KickError):
    """The OAuth server (id.kick.com) rejected a request."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class WebhookVerificationError(KickError):
    """A webhook request failed signature, header or timestamp checks."""


class KickAPIError(KickError):
    """The Kick API answered with an error status.

    ``message`` and ``data`` come from Kick's JSON error envelope when there is one.
    For some endpoints ``data`` holds a machine readable reason, for example
    ``"CHANNEL_IS_NOT_LIVE"`` when creating an ad break.
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        data: Any = None,
        method: str | None = None,
        url: str | None = None,
    ) -> None:
        where = f" ({method} {url})" if method and url else ""
        super().__init__(f"{status} {message}{where}")
        self.status = status
        self.message = message
        self.data = data
        self.method = method
        self.url = url


class BadRequestError(KickAPIError):
    """HTTP 400."""


class UnauthorizedError(KickAPIError):
    """HTTP 401. The token is invalid, expired or revoked."""


class ForbiddenError(KickAPIError):
    """HTTP 403. Usually a missing scope or acting on a channel you don't control."""


class NotFoundError(KickAPIError):
    """HTTP 404."""


class ConflictError(KickAPIError):
    """HTTP 409."""


class RateLimitError(KickAPIError):
    """HTTP 429. ``retry_after`` is in seconds when Kick sent a Retry-After header."""

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ServerError(KickAPIError):
    """HTTP 5xx."""


_STATUS_MAP: dict[int, type[KickAPIError]] = {
    400: BadRequestError,
    401: UnauthorizedError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}


def error_for_status(status: int) -> type[KickAPIError]:
    if status >= 500:
        return ServerError
    return _STATUS_MAP.get(status, KickAPIError)
