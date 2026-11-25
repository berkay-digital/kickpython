"""Receive official webhook events and react to them.

Kick posts events to the webhook URL set in your app settings
(https://kick.com/settings/developer). It must be reachable from the
internet over HTTPS, so put this server behind a reverse proxy or tunnel.

    export KICK_CLIENT_ID=... KICK_CLIENT_SECRET=...
    python examples/webhook_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from kickpython import (
    ChannelFollowed,
    ChatMessage,
    EventType,
    KickClient,
    SQLiteTokenStore,
    WebhookEvent,
    WebhookHandler,
)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    kick = KickClient(
        os.environ["KICK_CLIENT_ID"],
        os.environ["KICK_CLIENT_SECRET"],
        token_store=SQLiteTokenStore("kick_tokens.db"),
    )
    hook = WebhookHandler(client=kick)

    @hook.on(EventType.CHAT_MESSAGE_SENT)
    async def on_chat(event: WebhookEvent) -> None:
        msg = event.payload
        assert isinstance(msg, ChatMessage)
        if msg.content.strip() == "!ping" and msg.broadcaster.user_id:
            await kick.as_user(msg.broadcaster.user_id).chat.reply(msg.message_id, "pong")

    @hook.on(EventType.CHANNEL_FOLLOWED)
    async def on_follow(event: WebhookEvent) -> None:
        follow = event.payload
        assert isinstance(follow, ChannelFollowed)
        if follow.broadcaster.user_id:
            await kick.as_user(follow.broadcaster.user_id).chat.send(
                f"Thanks for the follow, {follow.follower.username}!"
            )

    # Subscribe every authorized channel (safe to repeat).
    for user_id in await kick.token_store.user_ids():
        results = await kick.as_user(user_id).events.subscribe(
            [EventType.CHAT_MESSAGE_SENT, EventType.CHANNEL_FOLLOWED]
        )
        for r in results:
            logging.info("user %s: %s -> %s", user_id, r.name, r.subscription_id or r.error)

    runner = web.AppRunner(hook.aiohttp_app("/kick/webhook"))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8080).start()
    logging.info("listening on http://127.0.0.1:8080/kick/webhook")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await kick.close()


if __name__ == "__main__":
    asyncio.run(main())
