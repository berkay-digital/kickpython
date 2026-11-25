"""A chat bot for every channel that authorized your app.

Reads chat over the (unofficial) websocket feed, answers through the official
API as your app's bot account. Run examples/authorize.py first.

    export KICK_CLIENT_ID=... KICK_CLIENT_SECRET=...
    python examples/chat_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from kickpython import KickAPIError, KickClient, LiveChat, LiveChatMessage, SQLiteTokenStore


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    async with (
        KickClient(
            os.environ["KICK_CLIENT_ID"],
            os.environ["KICK_CLIENT_SECRET"],
            token_store=SQLiteTokenStore("kick_tokens.db"),
        ) as kick,
        LiveChat() as live,
    ):

        @live.on_message
        async def on_message(msg: LiveChatMessage) -> None:
            if msg.broadcaster_user_id is None:
                return
            channel = kick.as_user(msg.broadcaster_user_id)
            try:
                if msg.content.strip() == "!ping":
                    await channel.chat.reply(msg.id, f"pong, @{msg.sender.username}")
                elif msg.content.startswith("!title ") and msg.sender.is_moderator:
                    await channel.channels.update(stream_title=msg.content[7:].strip())
                    await channel.chat.send("Title updated.")
            except KickAPIError as exc:
                logging.warning("command failed in %s: %s", msg.channel, exc)

        user_ids = await kick.token_store.user_ids()
        if not user_ids:
            raise SystemExit("No authorized channels: run examples/authorize.py first")
        for channel in await kick.channels.get(user_ids=user_ids):
            await live.join(channel.slug)
        await live.run()


if __name__ == "__main__":
    asyncio.run(main())
