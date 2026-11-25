"""Read public data with only an app token (no user authorization needed).

export KICK_CLIENT_ID=... KICK_CLIENT_SECRET=...
python examples/public_data.py berkaydigital
"""

from __future__ import annotations

import asyncio
import os
import sys

from kickpython import KickClient


async def main(slug: str) -> None:
    async with KickClient(os.environ["KICK_CLIENT_ID"], os.environ["KICK_CLIENT_SECRET"]) as kick:
        channel = await kick.channels.get_by_slug(slug)
        if channel is None:
            raise SystemExit(f"no channel called {slug}")
        status = "live" if channel.is_live else "offline"
        print(f"{channel.slug} ({channel.broadcaster_user_id}) is {status}")
        print("  title:", channel.stream_title or "-")
        print("  category:", channel.category.name if channel.category else "-")

        stats = await kick.livestreams.stats()
        print(f"\n{stats.total_count} streams live on Kick. Five Just Chatting streams in Turkish:")
        just_chatting = (await kick.categories.search("Just Chatting")).items[0]
        page = await kick.livestreams.list(
            category_ids=[just_chatting.id], language_codes=["tr"], limit=5
        )
        for stream in page:
            print(f"  {stream.slug:<24} {stream.viewer_count:>6} viewers  {stream.title[:50]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "berkaydigital"))
