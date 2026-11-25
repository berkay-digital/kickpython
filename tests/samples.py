"""Webhook payload samples from https://docs.kick.com/events/event-types."""

from __future__ import annotations

from typing import Any


def _user(name: str, uid: int, identity: Any = None) -> dict[str, Any]:
    return {
        "is_anonymous": False,
        "user_id": uid,
        "username": name,
        "is_verified": False,
        "profile_picture": f"https://example.com/{name}.jpg",
        "channel_slug": name,
        "identity": identity,
    }


BROADCASTER = _user("broadcaster_name", 123456789)

SAMPLES: dict[str, dict[str, Any]] = {
    "chat.message.sent": {
        "message_id": "unique_message_id_123",
        "replies_to": {
            "message_id": "unique_message_id_456",
            "content": "This is the parent message!",
            "sender": _user("parent_sender_name", 12345),
        },
        "broadcaster": BROADCASTER,
        "sender": _user(
            "sender_name",
            987654321,
            {
                "username_color": "#FF5733",
                "badges": [
                    {"text": "Moderator", "type": "moderator"},
                    {"text": "Sub Gifter", "type": "sub_gifter", "count": 5},
                    {"text": "Subscriber", "type": "subscriber", "count": 3},
                ],
            },
        ),
        "content": "Hello [emote:4148074:HYPERCLAP] [emote:37226:KEKW]",
        "emotes": [
            {"emote_id": "4148074", "positions": [{"s": 6, "e": 30}]},
            {"emote_id": "37226", "positions": [{"s": 32, "e": 49}]},
        ],
        "created_at": "2025-01-14T16:08:06Z",
    },
    "channel.followed": {"broadcaster": BROADCASTER, "follower": _user("follower_name", 9)},
    "channel.subscription.renewal": {
        "broadcaster": BROADCASTER,
        "subscriber": _user("subscriber_name", 9),
        "duration": 3,
        "created_at": "2025-01-14T16:08:06Z",
        "expires_at": "2025-02-14T16:08:06Z",
    },
    "channel.subscription.gifts": {
        "broadcaster": BROADCASTER,
        "gifter": _user("gifter_name", 9),
        "giftees": [_user("giftee_name", 10)],
        "created_at": "2025-01-14T16:08:06Z",
        "expires_at": "2025-02-14T16:08:06Z",
    },
    "channel.subscription.new": {
        "broadcaster": BROADCASTER,
        "subscriber": _user("subscriber_name", 9),
        "duration": 1,
        "created_at": "2025-01-14T16:08:06Z",
        "expires_at": "2025-02-14T16:08:06Z",
    },
    "channel.reward.redemption.updated": {
        "id": "01KBHE78QE4HZY1617DK5FC7YD",
        "user_input": "unban me",
        "status": "rejected",
        "redeemed_at": "2025-12-02T22:54:19.323Z",
        "reward": {
            "id": "01KBHE7RZNHB0SKDV1H86CD4F3",
            "title": "Uban Request",
            "cost": 1000,
            "description": "Only good reasons pls",
        },
        "redeemer": {
            "user_id": 123,
            "username": "naughty-user",
            "is_verified": False,
            "profile_picture": "",
            "channel_slug": "naughty_user",
        },
        "broadcaster": {
            "user_id": 333,
            "username": "gigachad",
            "is_verified": True,
            "profile_picture": "",
            "channel_slug": "gigachad",
        },
    },
    "livestream.status.updated": {
        "broadcaster": BROADCASTER,
        "is_live": False,
        "title": "Stream Title",
        "started_at": "2025-01-01T11:00:00+11:00",
        "ended_at": "2025-01-01T15:00:00+11:00",
    },
    "livestream.metadata.updated": {
        "broadcaster": BROADCASTER,
        "metadata": {
            "title": "Stream Title",
            "language": "en",
            "has_mature_content": True,
            "category": {
                "id": 123,
                "name": "Category name",
                "thumbnail": "http://example.com/image123",
            },
        },
    },
    "moderation.banned": {
        "broadcaster": BROADCASTER,
        "moderator": _user("moderator_name", 9),
        "banned_user": _user("banned_user_name", 10),
        "metadata": {
            "reason": "banned reason",
            "created_at": "2025-01-14T16:08:05Z",
            "expires_at": "2025-01-14T16:10:06Z",
        },
    },
    "kicks.gifted": {
        "broadcaster": {
            "user_id": 123456789,
            "username": "broadcaster_name",
            "is_verified": True,
            "profile_picture": "",
            "channel_slug": "broadcaster_channel",
        },
        "sender": {
            "user_id": 987654321,
            "username": "gift_sender",
            "is_verified": False,
            "profile_picture": "",
            "channel_slug": "gift_sender_channel",
        },
        "gift": {
            "amount": 500,
            "name": "Rage Quit",
            "type": "LEVEL_UP",
            "tier": "MID",
            "message": "w",
            "pinned_time_seconds": 600,
        },
        "created_at": "2025-10-20T04:00:08.634Z",
    },
}
