# Live chat over websocket (unofficial)

`LiveChat` reads chat from the Pusher websocket feed that the kick.com website uses. It is not part of Kick's public API. Use it when you can't run a public webhook endpoint, for example a bot on your own computer.

It only reads. Send messages, timeouts and the rest through the official API with `KickClient`.

## Usage

```python
live = LiveChat()

@live.on_message
async def on_message(msg: LiveChatMessage):
    print(f"[{msg.channel.slug}] {msg.sender.username}: {msg.content}")

await live.join("berkaydigital")
await live.join("another-channel")       # one connection serves every channel
await live.run()                          # until live.stop()
```

You can also join or leave channels while `run()` is going.

Handlers run one at a time, in the order events arrive, in a task separate from the websocket. A slow handler delays the handlers after it but never the connection's pings. If handlers fall more than 10,000 events behind, new events are dropped with a warning.

`stop()` returns `run()` promptly, even mid-reconnect, and ends any `messages()` loops. Calling `stop()` before `run()` has started makes `run()` return at once. After `run()` returns you can call it again. With `reconnect=False`, `run()` raises `RealtimeError` when the connection fails instead of retrying.

To consume messages in a loop instead of a callback, start `run()` in a task and iterate. The loop ends when `run()` returns:

```python
task = asyncio.create_task(live.run())
async for msg in live.messages():
    ...
```

## Messages

`LiveChatMessage` has `id`, `content`, `created_at`, `sender` (`id`, `username`, `slug`, `identity.color`, `identity.badges`) and `channel`, the `ChannelRef` you joined.

- `msg.broadcaster_user_id` is the channel owner's user ID. Pass it to `kick.as_user(...)` to answer.
- `msg.sender.is_moderator`, `is_broadcaster` and `is_subscriber` read the sender's badges.
- `msg.is_reply` and `msg.reply_to_id` describe replies.

## Other events

```python
@live.on_event("banned")
def on_ban(ban: LiveUserBanned):        # also "unbanned" and "deleted"
    print(ban.user.username, "banned for", ban.duration, "minutes")

@live.on_event("App\\Events\\PinnedMessageCreatedEvent")
def on_pin(data: dict, channel: ChannelRef | None):
    ...

@live.on_event("*")                      # every raw event
def raw(data: dict, channel: ChannelRef | None):
    ...
```

## How it connects

1. `join("slug")` looks up the channel's chatroom ID at `https://kick.com/api/v2/channels/<slug>`. If Cloudflare blocks the request, install `kickpython[cloudflare]` and kickpython retries through `curl_cffi`. You can also skip the lookup with `live.join(ChannelRef(slug=..., chatroom_id=..., user_id=...))`.
2. `run()` asks Kick for the current Pusher app key, the same way the website does, and falls back to a built-in key if that fails. Kick has rotated this key before, which broke kickpython 0.x.
3. It subscribes to `chatrooms.<id>.v2`, answers Pusher's pings and reconnects with exponential backoff (up to `max_backoff` seconds) when the connection drops.

Messages sent while the connection is down are lost. When you can't afford that, use webhooks.
