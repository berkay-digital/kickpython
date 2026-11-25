# kickpython

Async Python client for the [Kick.com public API](https://docs.kick.com).

- OAuth 2.1 with PKCE, app tokens and user tokens. Tokens refresh on their own and persist in memory, a JSON file, SQLite or your own store.
- Every endpoint in Kick's API: users, channels, chat, moderation, channel rewards, livestreams, categories, event subscriptions, KICKs, ads and drops.
- Typed responses (pydantic models), cursor pagination, automatic retries on rate limits.
- Webhook receiver with signature checks, replay protection and typed event payloads.
- A websocket chat reader for bots that can't expose a public webhook URL (unofficial, see below).

```bash
pip install kickpython
```

Python 3.10 or newer.

## Quick start

Create an app at [kick.com/settings/developer](https://kick.com/settings/developer) to get a client ID and secret.

```python
import asyncio
from kickpython import KickClient

async def main():
    async with KickClient("CLIENT_ID", "CLIENT_SECRET") as kick:
        channel = await kick.channels.get_by_slug("berkaydigital")
        print(channel.slug, channel.is_live, channel.stream_title)

        page = await kick.livestreams.list(language_codes=["en"], limit=10)
        for stream in page:
            print(stream.slug, stream.viewer_count)

asyncio.run(main())
```

Without a user, requests use an app access token. The client fetches it with your client credentials and renews it when it expires.

## Acting as a user

Sending chat, editing a channel, moderating and managing rewards need a user's permission. Send the user through the OAuth flow once, then save their tokens:

```python
from kickpython import KickClient, Scope, SQLiteTokenStore

kick = KickClient("CLIENT_ID", "CLIENT_SECRET", token_store=SQLiteTokenStore("kick_tokens.db"))

# 1. Send the user to request.url. Keep state and code_verifier for step 2.
request = kick.authorization_url(
    "http://localhost:9765/callback",
    [Scope.USER_READ, Scope.CHAT_WRITE, Scope.CHANNEL_WRITE],
)

# 2. Kick redirects back with ?code=...&state=... Check the state, then:
token = await kick.exchange_code(code, code_verifier=request.code_verifier,
                                 redirect_uri=request.redirect_uri)

# 3. From now on, act as that user. Expired tokens refresh themselves.
me = kick.as_user(token.user_id)
await me.chat.send("Hello chat!")
await me.channels.update(stream_title="Building a bot")
```

[`examples/authorize.py`](https://github.com/berkay-digital/kickpython/blob/main/examples/authorize.py) does steps 1 and 2 for you from the command line.

Kick only grants the scopes you enabled for the app in its settings, even if you request more. Check `token.scopes` after the exchange. When a call fails because of a missing scope, the error says so and lists the scopes the token has.

More in [docs/authentication.md](https://github.com/berkay-digital/kickpython/blob/main/docs/authentication.md).

## Chat

```python
me = kick.as_user(user_id)

await me.chat.send("posted by your app's bot account")
await me.chat.send("posted as the user", sender="user")
await me.chat.reply(message_id, "a threaded reply")
await me.chat.delete(message_id)                 # moderation:chat_message:manage
```

With `sender="bot"` (the default) the message goes to the chat of the user whose token you use. It appears under your app's bot account.

## Moderation, rewards and more

```python
await me.moderation.timeout(user_id, minutes=10, reason="spam")
await me.moderation.ban(user_id)
await me.moderation.unban(user_id)

reward = await me.rewards.create("Hydrate", cost=500, is_user_input_required=False)
pending = await me.rewards.redemptions(status="pending")
failed = await me.rewards.accept(r.id for group in pending for r in group.redemptions)

board = await me.kicks.leaderboard(top=10)
await me.ads.start_break(60)
```

The full list of methods is in [docs/api.md](https://github.com/berkay-digital/kickpython/blob/main/docs/api.md).

## Pagination

Paginated endpoints return a `Page`. Iterate it, ask for the next one, or walk all of them:

```python
page = await kick.categories.list(names=["just"])
print([c.name for c in page])

next_page = await page.next()          # None on the last page

async for stream in (await kick.livestreams.list(limit=100)).iter_all(max_pages=5):
    ...
```

## Webhooks

Kick sends events (chat messages, follows, subs, gifts, bans, stream start and stop, reward redemptions) to the webhook URL in your app settings. `WebhookHandler` checks each request's RSA signature against Kick's public key, rejects old or replayed deliveries and parses the payload:

```python
from kickpython import EventType, KickClient, WebhookEvent, WebhookHandler

kick = KickClient("CLIENT_ID", "CLIENT_SECRET")
hook = WebhookHandler(client=kick)

@hook.on(EventType.CHAT_MESSAGE_SENT)
async def on_chat(event: WebhookEvent):
    msg = event.payload                    # kickpython.ChatMessage
    print(msg.sender.username, msg.content)

# Subscribe a channel (with that user's token, or an app token plus broadcaster_user_id)
await kick.as_user(user_id).events.subscribe([EventType.CHAT_MESSAGE_SENT])

# Serve it with aiohttp, or call `await hook.handle(headers, body)` from any framework
app = hook.aiohttp_app("/kick/webhook")
```

See [docs/webhooks.md](https://github.com/berkay-digital/kickpython/blob/main/docs/webhooks.md) and [`examples/webhook_bot.py`](https://github.com/berkay-digital/kickpython/blob/main/examples/webhook_bot.py).

## Live chat without webhooks (unofficial)

Webhooks need a public HTTPS endpoint. For local bots, `LiveChat` reads chat from the same websocket feed the kick.com website uses. It needs no credentials and no public URL. Answer through the official API:

```python
from kickpython import KickClient, LiveChat, LiveChatMessage, SQLiteTokenStore

kick = KickClient("CLIENT_ID", "CLIENT_SECRET", token_store=SQLiteTokenStore("kick_tokens.db"))
live = LiveChat()

@live.on_message
async def on_message(msg: LiveChatMessage):
    if msg.content == "!ping":
        await kick.as_user(msg.broadcaster_user_id).chat.reply(msg.id, "pong")

await live.join("berkaydigital")
await live.run()          # reconnects on its own
```

This feed is not part of Kick's public API, and Kick has changed it before. kickpython looks up the current connection details from Kick at startup to cope with that, but it can still break. Use webhooks for anything that must not miss messages. Details in [docs/live-chat.md](https://github.com/berkay-digital/kickpython/blob/main/docs/live-chat.md).

## Errors

All errors subclass `kickpython.KickError`. API errors are `KickAPIError` with `status`, `message` and `data`, and there is a subclass per status: `BadRequestError`, `UnauthorizedError`, `ForbiddenError`, `NotFoundError`, `ConflictError`, `RateLimitError` (with `retry_after`) and `ServerError`. `MissingTokenError` means there is no usable token for the call, for example when the user never authorized your app or their refresh token expired.

```python
from kickpython import BadRequestError

try:
    await me.ads.start_break(30)
except BadRequestError as exc:
    print(exc.data)          # e.g. "CHANNEL_IS_NOT_LIVE"
```

The client retries 429 responses (honoring `Retry-After`) and failed GET requests up to `max_retries` times.

## Logging

kickpython logs to the `kickpython` logger and never configures logging itself. Tokens never appear in logs or in `repr()`.

```python
import logging
logging.basicConfig(level=logging.INFO)
```

## Upgrading from 0.x

1.0 is a rewrite with a new API. See [docs/migrating.md](https://github.com/berkay-digital/kickpython/blob/main/docs/migrating.md). Existing `kick_tokens.db` files work with `SQLiteTokenStore`.

## Development

```bash
uv sync
uv run pytest                 # offline tests against a fake Kick server
uv run ruff check . && uv run mypy
KICK_CLIENT_ID=... KICK_CLIENT_SECRET=... uv run pytest -m live   # read-only checks against Kick
```

## License

MIT
