"""Typed models for Kick API responses.

Every model keeps fields Kick adds later (``model_extra``), so a new field in
the API never breaks parsing. Use ``.model_dump()`` to get a plain dict.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict

__all__ = [
    "AdBreak",
    "AdBreakLimits",
    "AdBreakStatus",
    "AdBreakStatusItem",
    "Category",
    "Channel",
    "ChannelStream",
    "ChatMessageSent",
    "DropsClaim",
    "EventSubscription",
    "FailedRedemption",
    "KickModel",
    "KicksLeaderboard",
    "KicksLeaderboardEntry",
    "Livestream",
    "LivestreamCategory",
    "LivestreamUser",
    "LivestreamsStats",
    "MinimalReward",
    "Page",
    "Redeemer",
    "RedemptionStatus",
    "Reward",
    "RewardRedemption",
    "RewardRedemptions",
    "SubscriptionResult",
    "TokenInfo",
    "User",
]

T = TypeVar("T")


def _empty_to_none(value: Any) -> Any:
    # Kick sends "" or Go's zero time for "no value".
    if value in ("", None) or (isinstance(value, str) and value.startswith("0001-01-01")):
        return None
    return value


OptionalDatetime = Annotated[datetime | None, BeforeValidator(_empty_to_none)]
OptionalStr = Annotated[str | None, BeforeValidator(_empty_to_none)]


class KickModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# Users and channels ---------------------------------------------------------


class User(KickModel):
    user_id: int
    name: str
    email: OptionalStr = None
    """Only present for the authorized user, with the ``user:read`` scope."""
    profile_picture: OptionalStr = None


class Category(KickModel):
    id: int
    name: str
    thumbnail: OptionalStr = None
    tags: list[str] = []
    viewer_count: int | None = None


def _empty_category(value: Any) -> Any:
    if isinstance(value, dict) and not value.get("id"):
        return None
    return value


class ChannelStream(KickModel):
    is_live: bool = False
    is_mature: bool = False
    language: OptionalStr = None
    start_time: OptionalDatetime = None
    viewer_count: int = 0
    thumbnail: OptionalStr = None
    custom_tags: list[str] = []
    url: OptionalStr = None
    """RTMPS ingest URL. Needs the ``streamkey:read`` scope."""
    key: OptionalStr = None
    """Stream key. Needs the ``streamkey:read`` scope."""


class Channel(KickModel):
    broadcaster_user_id: int
    slug: str
    channel_description: OptionalStr = None
    banner_picture: OptionalStr = None
    stream_title: OptionalStr = None
    category: Annotated[Category | None, BeforeValidator(_empty_category)] = None
    stream: ChannelStream | None = None
    active_subscribers_count: int | None = None
    """Paid plus gifted active subscriptions."""
    active_gifted_subscribers_count: int | None = None
    canceled_subscribers_count: int | None = None

    @property
    def is_live(self) -> bool:
        return bool(self.stream and self.stream.is_live)

    @property
    def url(self) -> str:
        return f"https://kick.com/{self.slug}"


# Livestreams ------------------------------------------------------------------


class LivestreamUser(KickModel):
    id: int
    username: str
    profile_picture: OptionalStr = None


class LivestreamCategory(KickModel):
    id: int
    name: str
    thumbnail: OptionalStr = None


class _ChannelSlug(KickModel):
    slug: str


class Livestream(KickModel):
    id: str
    title: str
    language_code: OptionalStr = None
    tags: list[str] = []
    has_mature_content: bool = False
    viewer_count: int = 0
    thumbnail: OptionalStr = None
    started_at: OptionalDatetime = None
    category: LivestreamCategory | None = None
    channel: _ChannelSlug
    broadcaster_user: LivestreamUser

    @property
    def slug(self) -> str:
        return self.channel.slug

    @property
    def broadcaster_user_id(self) -> int:
        return self.broadcaster_user.id


class LivestreamsStats(KickModel):
    total_count: int


# Chat ---------------------------------------------------------------------------


class ChatMessageSent(KickModel):
    is_sent: bool
    message_id: OptionalStr = None


# Events -------------------------------------------------------------------------


class EventSubscription(KickModel):
    id: str
    app_id: OptionalStr = None
    broadcaster_user_id: int | None = None
    event: str
    version: int
    method: str = "webhook"
    created_at: OptionalDatetime = None
    updated_at: OptionalDatetime = None


class SubscriptionResult(KickModel):
    """One entry of the ``POST /events/subscriptions`` response."""

    name: str
    version: int
    subscription_id: OptionalStr = None
    error: OptionalStr = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.subscription_id is not None


# Channel rewards ------------------------------------------------------------------

RedemptionStatus = Literal["pending", "accepted", "rejected"]


class Reward(KickModel):
    id: str
    title: str
    cost: int
    description: OptionalStr = None
    background_color: OptionalStr = None
    is_enabled: bool = True
    is_paused: bool = False
    is_user_input_required: bool = False
    should_redemptions_skip_request_queue: bool = False


class MinimalReward(KickModel):
    id: str
    title: str
    cost: int | None = None
    description: OptionalStr = None
    can_manage: bool | None = None
    is_deleted: bool | None = None


class Redeemer(KickModel):
    user_id: int


class RewardRedemption(KickModel):
    id: str
    status: RedemptionStatus
    redeemed_at: OptionalDatetime = None
    user_input: OptionalStr = None
    redeemer: Redeemer


class RewardRedemptions(KickModel):
    """Redemptions grouped under the reward they belong to."""

    reward: MinimalReward
    redemptions: list[RewardRedemption] = []


class FailedRedemption(KickModel):
    id: str
    reason: str
    """``UNKNOWN``, ``NOT_PENDING``, ``NOT_FOUND`` or ``NOT_OWNED``."""


# KICKs ------------------------------------------------------------------------------


class KicksLeaderboardEntry(KickModel):
    rank: int
    user_id: int
    username: str
    gifted_amount: int


class KicksLeaderboard(KickModel):
    lifetime: list[KicksLeaderboardEntry] = []
    month: list[KicksLeaderboardEntry] = []
    week: list[KicksLeaderboardEntry] = []


# Ads ----------------------------------------------------------------------------------


class AdBreak(KickModel):
    id: str
    ad_break_id: OptionalStr = None
    remaining_ad_breaks: int | None = None


class AdBreakLimits(KickModel):
    max: int
    period_seconds: int


class AdBreakStatusItem(KickModel):
    id: str
    ad_break_id: OptionalStr = None
    break_duration_seconds: int | None = None
    created_at: OptionalDatetime = None
    source: OptionalStr = None
    status: OptionalStr = None


class AdBreakStatus(KickModel):
    opted_in: bool = False
    ads_blocked: bool = False
    auto_ads_active: bool = False
    remaining_ad_breaks: int | None = None
    limits: AdBreakLimits | None = None
    ad_breaks: list[AdBreakStatusItem] = []


# Drops ----------------------------------------------------------------------------------


class DropsClaim(KickModel):
    claim_id: str
    user_id: int
    campaign_id: str
    reward_id: str
    external_id: OptionalStr = None
    external_status: OptionalStr = None
    created_at: OptionalDatetime = None
    updated_at: OptionalDatetime = None


# OAuth ----------------------------------------------------------------------------------


class TokenInfo(KickModel):
    """Result of token introspection."""

    active: bool
    client_id: OptionalStr = None
    token_type: OptionalStr = None
    """``"user"`` or ``"app"``."""
    scope: OptionalStr = None
    exp: int | None = None

    @property
    def scopes(self) -> list[str]:
        return self.scope.split() if self.scope else []

    @property
    def expires_at(self) -> datetime | None:
        return datetime.fromtimestamp(self.exp, tz=timezone.utc) if self.exp else None


# Pagination -------------------------------------------------------------------------------


@dataclass
class Page(Generic[T]):
    """One page of a cursor-paginated endpoint.

    ``await page.next()`` fetches the following page, or returns ``None`` at the end.
    ``async for item in page.iter_all()`` walks this and every following page.
    """

    items: list[T]
    next_cursor: str | None
    _fetch: Callable[[str], Awaitable[Page[T]]] | None = field(default=None, repr=False)

    def __iter__(self) -> Any:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def has_next(self) -> bool:
        return bool(self.next_cursor) and self._fetch is not None

    async def next(self) -> Page[T] | None:
        if not self.has_next:
            return None
        if self._fetch is None or not self.next_cursor:
            return None
        return await self._fetch(self.next_cursor)

    async def iter_all(self, *, max_pages: int | None = None) -> AsyncIterator[T]:
        page: Page[T] | None = self
        seen = 0
        while page is not None:
            for item in page.items:
                yield item
            seen += 1
            if max_pages is not None and seen >= max_pages:
                return
            # Stop if the API hands back an empty page with a cursor, to avoid looping.
            if not page.items:
                return
            page = await page.next()
