"""OAuth scopes and event names defined by the Kick API."""

from __future__ import annotations

from enum import Enum

__all__ = ["EventType", "Scope"]


class Scope(str, Enum):
    """OAuth scopes. See https://docs.kick.com/scopes/scopes.

    Kick only grants scopes that are also enabled for your app in
    https://kick.com/settings/developer, even if you request more.
    """

    USER_READ = "user:read"
    CHANNEL_READ = "channel:read"
    CHANNEL_WRITE = "channel:write"
    CHANNEL_REWARDS_READ = "channel:rewards:read"
    CHANNEL_REWARDS_WRITE = "channel:rewards:write"
    CHAT_WRITE = "chat:write"
    STREAMKEY_READ = "streamkey:read"
    EVENTS_SUBSCRIBE = "events:subscribe"
    MODERATION_BAN = "moderation:ban"
    MODERATION_CHAT_MESSAGE_MANAGE = "moderation:chat_message:manage"
    KICKS_READ = "kicks:read"
    ADS_READ = "ads:read"
    ADS_WRITE = "ads:write"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def all(cls) -> list[Scope]:
        return list(cls)


class EventType(str, Enum):
    """Webhook event names. See https://docs.kick.com/events/event-types."""

    CHAT_MESSAGE_SENT = "chat.message.sent"
    CHANNEL_FOLLOWED = "channel.followed"
    CHANNEL_SUBSCRIPTION_RENEWAL = "channel.subscription.renewal"
    CHANNEL_SUBSCRIPTION_GIFTS = "channel.subscription.gifts"
    CHANNEL_SUBSCRIPTION_NEW = "channel.subscription.new"
    CHANNEL_REWARD_REDEMPTION_UPDATED = "channel.reward.redemption.updated"
    LIVESTREAM_STATUS_UPDATED = "livestream.status.updated"
    LIVESTREAM_METADATA_UPDATED = "livestream.metadata.updated"
    MODERATION_BANNED = "moderation.banned"
    KICKS_GIFTED = "kicks.gifted"

    def __str__(self) -> str:
        return self.value
