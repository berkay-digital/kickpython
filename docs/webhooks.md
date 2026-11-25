# Webhooks

Webhooks are Kick's official way to deliver events. Kick sends a POST request to the webhook URL in your app settings for every event you subscribed to.

## Setup

1. In [your app settings](https://kick.com/settings/developer), turn on webhooks and enter a public HTTPS URL. Kick can't reach `localhost`; put your server behind a reverse proxy or a tunnel.
2. Subscribe to events for each channel:

```python
# With the channel owner's token (needs events:subscribe):
await kick.as_user(user_id).events.subscribe([EventType.CHAT_MESSAGE_SENT, EventType.CHANNEL_FOLLOWED])

# Or with your app token, for any channel:
await kick.events.subscribe([EventType.LIVESTREAM_STATUS_UPDATED], broadcaster_user_id=1710384)
```

Subscribing twice to the same event is harmless. An app can hold 10,000 subscriptions per event type, but unverified apps are limited to 1,000 for `chat.message.sent`.

## Receiving events

```python
hook = WebhookHandler(client=kick)

@hook.on(EventType.CHAT_MESSAGE_SENT)
async def on_chat(event: WebhookEvent):
    msg: ChatMessage = event.payload
    ...

@hook.on()              # no arguments: every event
def log_everything(event: WebhookEvent):
    print(event.type, event.message_id)
```

Handlers can be plain functions or coroutines.

### With aiohttp

```python
app = hook.aiohttp_app("/kick/webhook")
web.run_app(app, host="127.0.0.1", port=8080)
```

The route answers 200 as soon as the signature checks out and runs your handlers in the background, so a slow handler doesn't make Kick retry. Requests that fail verification get 401. Handler exceptions are logged.

### With any other framework

Pass the headers and the raw body (bytes, exactly as received) to `handle`:

```python
@app.post("/kick/webhook")                       # FastAPI
async def kick_webhook(request: Request):
    try:
        await hook.handle(request.headers, await request.body())
    except WebhookVerificationError:
        raise HTTPException(401)
    return "ok"
```

`handle` runs your handlers before it returns. Answer quickly: Kick unsubscribes apps whose endpoint keeps failing for more than a day.

## What gets checked

- **Signature.** Kick signs `"{Kick-Event-Message-Id}.{Kick-Event-Message-Timestamp}.{body}"` with RSA (PKCS#1 v1.5, SHA-256). The handler fetches Kick's public key from `GET /public/v1/public-key`. If a signature fails, it fetches the key once more in case Kick rotated it. Pass `public_key=` to pin a key and skip fetching.
- **Age.** Deliveries whose timestamp is more than `max_age` seconds (default 600) from now are rejected. Set `max_age=None` to turn this off.
- **Duplicates.** Kick may deliver the same message twice. The handler remembers the last `dedupe_size` (default 2048) message IDs and drops repeats. This memory is per process.

## Event types

| `EventType` | Name | Payload model |
| --- | --- | --- |
| `CHAT_MESSAGE_SENT` | `chat.message.sent` | `ChatMessage` |
| `CHANNEL_FOLLOWED` | `channel.followed` | `ChannelFollowed` |
| `CHANNEL_SUBSCRIPTION_NEW` | `channel.subscription.new` | `SubscriptionNew` |
| `CHANNEL_SUBSCRIPTION_RENEWAL` | `channel.subscription.renewal` | `SubscriptionRenewal` |
| `CHANNEL_SUBSCRIPTION_GIFTS` | `channel.subscription.gifts` | `SubscriptionGifts` |
| `CHANNEL_REWARD_REDEMPTION_UPDATED` | `channel.reward.redemption.updated` | `RewardRedemptionUpdated` |
| `LIVESTREAM_STATUS_UPDATED` | `livestream.status.updated` | `LivestreamStatusUpdated` |
| `LIVESTREAM_METADATA_UPDATED` | `livestream.metadata.updated` | `LivestreamMetadataUpdated` |
| `MODERATION_BANNED` | `moderation.banned` | `ModerationBanned` |
| `KICKS_GIFTED` | `kicks.gifted` | `KicksGifted` |

An event type this version of kickpython doesn't know still reaches your handlers, with `event.payload = None`. The JSON body is always in `event.raw`.

Anonymous gifters arrive with `is_anonymous=True` and every other user field set to `None`.

## Verifying by hand

```python
from kickpython import verify_signature, parse_event

ok = verify_signature(public_key_pem, message_id=..., timestamp=..., body=raw_body, signature=...)
event = parse_event(headers, raw_body)          # parses without verifying
```
