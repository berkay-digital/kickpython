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
    except WebhookKeyUnavailable:
        raise HTTPException(503)
    return "ok"
```

`handle` runs your handlers before it returns. Answer quickly: Kick unsubscribes apps whose endpoint keeps failing for more than a day.

## What gets checked

- **Signature.** Kick signs `"{Kick-Event-Message-Id}.{Kick-Event-Message-Timestamp}.{body}"` with RSA (PKCS#1 v1.5, SHA-256). The handler fetches Kick's public key from `GET /public/v1/public-key` with a 10 second timeout. When a signature fails it fetches the key again in case Kick rotated it, but at most once every `key_refresh_interval` seconds (default 300). That stops forged requests from turning into a flood of key fetches. Pass `public_key=` to pin a key and never fetch.
- **Age.** Deliveries whose timestamp is more than `max_age` seconds (default 600) from now are rejected. Set `max_age=None` to turn this off.
- **Duplicates.** Kick may deliver the same message twice. The handler remembers message IDs for twice `max_age`, so a replay inside the accepted window is dropped. This memory is per process. With `handle()`, a delivery that is still being handled also counts as seen, and an ID is only kept after your handlers succeed, so if one raises, Kick's retry runs them again.
- **Broadcaster.** Optional. With `broadcaster_user_ids=[...]`, deliveries for any other channel are rejected.

### What the signature doesn't cover

Kick signs deliveries for every app with the same key, and the `Kick-Event-Type`, `Kick-Event-Version` and `Kick-Event-Subscription-Id` headers are not signed. Someone with their own Kick app can capture a genuine delivery for a channel they subscribed to and post it to your endpoint within the `max_age` window. They can also change the type header.

So:

- Pass `broadcaster_user_ids` (for example `await kick.token_store.user_ids()`) when you only serve your own channels.
- Check fields in the payload, such as `msg.broadcaster.user_id`, before you hand out rewards.
- Keep handlers idempotent.

If the public key can't be fetched at all, `verify` raises `WebhookKeyUnavailable` instead of `WebhookVerificationError`. Answer it with 503 so Kick retries later; the aiohttp route does. After a failed fetch the handler fails fast for 15 seconds instead of fetching again on every request.

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

## Drops claims

If your organization runs [Drops campaigns](https://docs.kick.com/drops/drops-guide), Kick posts each claimed reward to the webhook URL you gave Kick when registering the organization. These requests differ from event webhooks. They have no `Kick-Event-Type` header and send the timestamp as `Kick-Event-Timestamp`, and Kick expects a synchronous answer: 200 only once the reward is delivered.

The same `WebhookHandler` takes them. kickpython labels them `DROPS_REWARD_CLAIMED`:

```python
from kickpython import DROPS_REWARD_CLAIMED, DropsRewardClaimed, WebhookEvent, WebhookHandler

hook = WebhookHandler()

@hook.on(DROPS_REWARD_CLAIMED)
async def fulfil(event: WebhookEvent):
    claim: DropsRewardClaimed = event.payload
    await give_item(kick_user_id=claim.user_id, reward=claim.external_id or claim.reward_id,
                    idempotency_key=claim.claim_id)
```

The aiohttp route runs Drops handlers before answering. It returns 200 when they succeed and 500 when one raises, so Kick tries again. A copy that arrives while the first is still running gets 503. Make fulfilment idempotent on `claim_id`: Kick can deliver the same claim twice, and the handler's duplicate memory lives in one process only.

The `broadcaster_user_ids` filter doesn't apply to Drops claims, since they belong to the organization rather than a channel.

To catch up on claims you missed, or to record your fulfilment status at Kick, use the claims API:

```python
page = await kick.drops.claims(campaign_id="01J...", limit=100)
await kick.drops.update_claims({c.claim_id: "processed" for c in page})
```

kickpython verifies Drops signatures the same way as other webhooks, over `"{message id}.{timestamp}.{body}"` with Kick's public key. Kick's Drops guide doesn't spell out the signed string separately, and this path hasn't been tested against real Drops deliveries, because those need an approved organization.

## Verifying by hand

```python
from kickpython import verify_signature, parse_event

ok = verify_signature(public_key_pem, message_id=..., timestamp=..., body=raw_body, signature=...)
event = parse_event(headers, raw_body)          # parses without verifying
```
