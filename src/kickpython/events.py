"""Typed payloads for webhook events (https://docs.kick.com/events/event-types)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .models import KickModel, OptionalDatetime, OptionalStr
from .scopes import EventType

__all__ = [
    "Badge",
    "ChannelFollowed",
    "ChatMessage",
    "EventPayload",
    "EventUser",
    "Identity",
    "KicksGift",
    "KicksGifted",
    "LivestreamMetadata",
    "LivestreamMetadataUpdated",
    "LivestreamStatusUpdated",
    "ModerationBanMetadata",
    "ModerationBanned",
    "RedemptionReward",
    "RepliedMessage",
    "RewardRedemptionUpdated",
    "SubscriptionGifts",
    "SubscriptionNew",
    "SubscriptionRenewal",
    "WebhookEvent",
    "payload_model",
]


class Badge(KickModel):
    text: str
    type: str
    count: int | None = None


class Identity(KickModel):
    username_color: OptionalStr = None
    badges: list[Badge] = []


class EventUser(KickModel):
    """A user in an event payload. Fields are ``None`` for anonymous gifters."""

    is_anonymous: bool = False
    user_id: int | None = None
    username: OptionalStr = None
    is_verified: bool | None = None
    profile_picture: OptionalStr = None
    channel_slug: OptionalStr = None
    identity: Identity | None = None

    def has_badge(self, badge_type: str) -> bool:
        return bool(self.identity) and any(b.type == badge_type for b in self.identity.badges)  # type: ignore[union-attr]

    @property
    def is_moderator(self) -> bool:
        return self.has_badge("moderator") or self.has_badge("broadcaster")


class EventPayload(KickModel):
    """Base class for webhook payload bodies."""


class EmotePosition(KickModel):
    s: int
    e: int


class Emote(KickModel):
    emote_id: str
    positions: list[EmotePosition] = []


class RepliedMessage(KickModel):
    message_id: str
    content: str | None = None
    sender: EventUser | None = None


class ChatMessage(EventPayload):
    """``chat.message.sent``"""

    message_id: str
    broadcaster: EventUser
    sender: EventUser
    content: str
    emotes: list[Emote] | None = None
    replies_to: RepliedMessage | None = None
    created_at: OptionalDatetime = None


class ChannelFollowed(EventPayload):
    """``channel.followed``"""

    broadcaster: EventUser
    follower: EventUser


class SubscriptionRenewal(EventPayload):
    """``channel.subscription.renewal``"""

    broadcaster: EventUser
    subscriber: EventUser
    duration: int
    created_at: OptionalDatetime = None
    expires_at: OptionalDatetime = None


class SubscriptionNew(EventPayload):
    """``channel.subscription.new``"""

    broadcaster: EventUser
    subscriber: EventUser
    duration: int
    created_at: OptionalDatetime = None
    expires_at: OptionalDatetime = None


class SubscriptionGifts(EventPayload):
    """``channel.subscription.gifts``"""

    broadcaster: EventUser
    gifter: EventUser
    giftees: list[EventUser] = []
    created_at: OptionalDatetime = None
    expires_at: OptionalDatetime = None


class RedemptionReward(KickModel):
    id: str
    title: str
    cost: int | None = None
    description: OptionalStr = None


class RewardRedemptionUpdated(EventPayload):
    """``channel.reward.redemption.updated``"""

    id: str
    status: str
    user_input: OptionalStr = None
    redeemed_at: OptionalDatetime = None
    reward: RedemptionReward
    redeemer: EventUser
    broadcaster: EventUser


class LivestreamStatusUpdated(EventPayload):
    """``livestream.status.updated``: a stream started (``is_live``) or ended."""

    broadcaster: EventUser
    is_live: bool
    title: OptionalStr = None
    started_at: OptionalDatetime = None
    ended_at: OptionalDatetime = None


class _MetaCategory(KickModel):
    id: int
    name: str
    thumbnail: OptionalStr = None


class LivestreamMetadata(KickModel):
    title: OptionalStr = None
    language: OptionalStr = None
    has_mature_content: bool | None = None
    category: _MetaCategory | None = None


class LivestreamMetadataUpdated(EventPayload):
    """``livestream.metadata.updated``"""

    broadcaster: EventUser
    metadata: LivestreamMetadata


class ModerationBanMetadata(KickModel):
    reason: OptionalStr = None
    created_at: OptionalDatetime = None
    expires_at: OptionalDatetime = None
    """``None`` for permanent bans."""


class ModerationBanned(EventPayload):
    """``moderation.banned``"""

    broadcaster: EventUser
    moderator: EventUser
    banned_user: EventUser
    metadata: ModerationBanMetadata

    @property
    def is_permanent(self) -> bool:
        return self.metadata.expires_at is None


class KicksGift(KickModel):
    amount: int
    name: OptionalStr = None
    type: OptionalStr = None
    tier: OptionalStr = None
    message: OptionalStr = None
    pinned_time_seconds: int | None = None


class KicksGifted(EventPayload):
    """``kicks.gifted``"""

    broadcaster: EventUser
    sender: EventUser
    gift: KicksGift
    created_at: OptionalDatetime = None


_PAYLOADS: dict[str, type[EventPayload]] = {
    EventType.CHAT_MESSAGE_SENT.value: ChatMessage,
    EventType.CHANNEL_FOLLOWED.value: ChannelFollowed,
    EventType.CHANNEL_SUBSCRIPTION_RENEWAL.value: SubscriptionRenewal,
    EventType.CHANNEL_SUBSCRIPTION_GIFTS.value: SubscriptionGifts,
    EventType.CHANNEL_SUBSCRIPTION_NEW.value: SubscriptionNew,
    EventType.CHANNEL_REWARD_REDEMPTION_UPDATED.value: RewardRedemptionUpdated,
    EventType.LIVESTREAM_STATUS_UPDATED.value: LivestreamStatusUpdated,
    EventType.LIVESTREAM_METADATA_UPDATED.value: LivestreamMetadataUpdated,
    EventType.MODERATION_BANNED.value: ModerationBanned,
    EventType.KICKS_GIFTED.value: KicksGifted,
}


def payload_model(event_type: str) -> type[EventPayload] | None:
    """The payload model for an event name, or ``None`` if kickpython doesn't know it."""
    return _PAYLOADS.get(str(event_type))


class WebhookEvent(BaseModel):
    """A verified webhook delivery."""

    type: str
    version: int
    message_id: str
    subscription_id: str | None = None
    timestamp: datetime | None = None
    payload: EventPayload | None = None
    """Parsed body, or ``None`` for event types this version doesn't know."""
    raw: dict[str, Any]
    """The JSON body as sent by Kick."""
