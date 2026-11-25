# API reference

Every method is a coroutine. "User" in the token column means the call needs `kick.as_user(user_id)` (or a fixed `access_token`). "Any" works with an app token too.

Methods that take a list of IDs or slugs split it into several requests when it is longer than Kick's per-request limit.

## Users

| Method | Endpoint | Token |
| --- | --- | --- |
| `users.get(ids=None)` returns `list[User]` | `GET /public/v1/users` | Any. Without IDs, the authorized user |
| `users.me()` returns `User` | `GET /public/v1/users` | User |

## Channels

| Method | Endpoint | Token |
| --- | --- | --- |
| `channels.get(user_ids=..., slugs=...)` returns `list[Channel]` | `GET /public/v1/channels` | Any |
| `channels.get_by_slug(slug)`, `channels.get_by_user_id(id)` return `Channel` or `None` | same | Any |
| `channels.me()` returns `Channel` | same | User |
| `channels.update(stream_title=, category_id=, custom_tags=)` | `PATCH /public/v1/channels` | User, `channel:write` |

`Channel.category` is `None` when no category is set. `Channel.stream.key` and `.url` are only filled with `streamkey:read`. Kick accepts `custom_tags` on an offline channel, but `Channel.stream.custom_tags` stays empty until the channel goes live.

## Chat

| Method | Endpoint | Token |
| --- | --- | --- |
| `chat.send(content, sender="bot", broadcaster_user_id=None, reply_to=None)` returns `ChatMessageSent` | `POST /public/v1/chat` | User, `chat:write` |
| `chat.reply(message_id, content, **kwargs)` | same | User, `chat:write` |
| `chat.delete(message_id)` | `DELETE /public/v1/chat/{id}` | User, `moderation:chat_message:manage` |

`sender="bot"` posts as your app's bot into the token owner's chat and ignores `broadcaster_user_id`. `sender="user"` posts as the token owner into `broadcaster_user_id`'s chat, which defaults to their own. Messages are limited to 500 characters and 2048 UTF-8 bytes.

## Moderation

All need a user token with `moderation:ban`. `broadcaster_user_id` defaults to the bound user.

| Method | Endpoint |
| --- | --- |
| `moderation.ban(user_id, broadcaster_user_id=None, reason=None)` | `POST /public/v1/moderation/bans` |
| `moderation.timeout(user_id, minutes, broadcaster_user_id=None, reason=None)` | same, with `duration` (1 to 10080 minutes) |
| `moderation.unban(user_id, broadcaster_user_id=None)` | `DELETE /public/v1/moderation/bans` |

Kick answers a bare `400 Invalid request` (raised as `BadRequestError`) when the target can't be moderated: the broadcaster themselves, your app's own bot account, or a user ID that doesn't exist.

## Channel rewards

User token. Reading needs `channel:rewards:read`, everything else `channel:rewards:write`. Only the app that created a reward can change, delete or resolve it.

| Method | Endpoint |
| --- | --- |
| `rewards.list()` returns `list[Reward]` | `GET /public/v1/channels/rewards` |
| `rewards.create(title, cost, description=, background_color=, is_enabled=, is_user_input_required=, should_redemptions_skip_request_queue=)` | `POST /public/v1/channels/rewards` |
| `rewards.update(reward_id, ...)` | `PATCH /public/v1/channels/rewards/{id}` |
| `rewards.delete(reward_id)` | `DELETE /public/v1/channels/rewards/{id}` |
| `rewards.redemptions(reward_id=, status=, ids=, cursor=)` returns `Page[RewardRedemptions]` | `GET /public/v1/channels/rewards/redemptions` |
| `rewards.accept(ids)`, `rewards.reject(ids)` return the failed ones as `list[FailedRedemption]` | `POST .../redemptions/accept`, `.../reject` |

## Livestreams

| Method | Endpoint | Token |
| --- | --- | --- |
| `livestreams.list(category_ids=, language_codes=, limit=, cursor=)` returns `Page[Livestream]` | `GET /public/v2/livestreams` | Any |
| `livestreams.for_users(user_ids)` returns `list[Livestream]` | `GET /public/v1/users/livestreams` | Any |
| `livestreams.stats()` returns `LivestreamsStats` | `GET /public/v1/livestreams/stats` | Any |

## Categories

| Method | Endpoint | Token |
| --- | --- | --- |
| `categories.list(names=, tags=, ids=, limit=, cursor=)` returns `Page[Category]` | `GET /public/v2/categories` | Any |
| `categories.search(name)` | same | Any |
| `categories.get(category_id)` returns `Category` or `None` | same | Any |

Name and tag filters match on substrings: `names=["just"]` finds "Just Chatting" and "Just Dance".

## Event subscriptions

| Method | Endpoint | Token |
| --- | --- | --- |
| `events.list(broadcaster_user_id=None)` returns `list[EventSubscription]` | `GET /public/v1/events/subscriptions` | Any |
| `events.subscribe(events, broadcaster_user_id=None)` returns `list[SubscriptionResult]` | `POST /public/v1/events/subscriptions` | App token with `broadcaster_user_id`, or user token with `events:subscribe` |
| `events.unsubscribe(ids)` | `DELETE /public/v1/events/subscriptions` | Any |
| `events.unsubscribe_all(broadcaster_user_id=None)` returns the count | list, then delete | Any |

`events` takes `EventType` values, strings, or `(name, version)` tuples. Kick reports problems per event, so check `result.ok` and `result.error`.

## KICKs, ads, drops

| Method | Endpoint | Token |
| --- | --- | --- |
| `kicks.leaderboard(top=10)` returns `KicksLeaderboard` | `GET /public/v1/kicks/leaderboard` | User, `kicks:read` |
| `ads.start_break(duration_seconds, id=None)` returns `AdBreak` | `POST /public/v1/ads/ad-break` | User, `ads:write` |
| `ads.status()` returns `AdBreakStatus` | `GET /public/v1/ads/ad-break-status` | User, `ads:read` |
| `ads.enroll()` | `POST /public/v1/ads/enroll` | User, `ads:write` |
| `drops.claims(campaign_id=, user_id=, claim_id=, external_status=, limit=, cursor=)` returns `Page[DropsClaim]` | `GET /public/v1/drops/claims` | App token of an organization app |
| `drops.update_claims({claim_id: status})` | `PATCH /public/v1/drops/claims` | same |

Ad break errors carry Kick's reason in `exc.data`, for example `CHANNEL_IS_NOT_LIVE` or `MAX_AD_BREAKS_REACHED`.

## OAuth and keys

| Method | What it does |
| --- | --- |
| `authorization_url(redirect_uri, scopes, state=None)` | Builds the consent URL. Not a coroutine |
| `exchange_code(code, code_verifier=, redirect_uri=, store=True)` | Code for a user token |
| `refresh_user_token(user_id)`, `refresh_all_user_tokens()` | Refresh now |
| `get_user_token(user_id)`, `get_app_token()` | Current valid token |
| `add_user_token(token)` | Save a token obtained elsewhere |
| `introspect(access_token=None)` | `POST /oauth/token/introspect` |
| `revoke_token(token, hint=None)`, `revoke_user(user_id)` | `POST /oauth/revoke` |
| `public_key()` | Kick's webhook signing key (PEM) |

## Models

Responses are pydantic models. Unknown fields Kick adds later stay available in `model.model_extra`, and `model.model_dump()` gives a plain dict. Kick's empty strings and zero dates (`0001-01-01T00:00:00Z`) become `None`.

Deprecated Kick endpoints (`/public/v1/categories`, `/public/v1/livestreams`, `/public/v1/token/introspect`) are not wrapped. kickpython uses their replacements.
