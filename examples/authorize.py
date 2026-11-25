"""Authorize a Kick account for your app and save its tokens.

    export KICK_CLIENT_ID=... KICK_CLIENT_SECRET=...
    export KICK_REDIRECT_URI=http://localhost:9765/callback   # must match your app settings
    python examples/authorize.py

Open the printed link and approve. If this machine can receive the redirect
(you are on it), the local server catches it. Otherwise the browser shows an
error page: copy its full URL and paste it here.

Tokens are saved to kick_tokens.db; the other examples read them from there.
"""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.parse

from aiohttp import web

from kickpython import KickClient, Scope, SQLiteTokenStore

SCOPES = [
    Scope.USER_READ,
    Scope.CHANNEL_READ,
    Scope.CHANNEL_WRITE,
    Scope.CHAT_WRITE,
    Scope.EVENTS_SUBSCRIBE,
    Scope.MODERATION_BAN,
    Scope.MODERATION_CHAT_MESSAGE_MANAGE,
]


async def main() -> None:
    redirect_uri = os.environ.get("KICK_REDIRECT_URI", "http://localhost:9765/callback")
    async with KickClient(
        os.environ["KICK_CLIENT_ID"],
        os.environ["KICK_CLIENT_SECRET"],
        token_store=SQLiteTokenStore("kick_tokens.db"),
    ) as kick:
        request = kick.authorization_url(redirect_uri, SCOPES)
        print("Open this link and approve:\n\n ", request.url, "\n")

        got: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        async def callback(req: web.Request) -> web.Response:
            if not got.done():
                got.set_result(str(req.url))
            return web.Response(text="Done, you can close this tab.")

        parsed = urllib.parse.urlparse(redirect_uri)
        app = web.Application()
        app.router.add_get(parsed.path or "/", callback)
        runner = web.AppRunner(app)
        await runner.setup()
        # Loopback only: the redirect comes from your own browser.
        await web.TCPSite(runner, "127.0.0.1", parsed.port or 80).start()

        async def read_paste() -> None:
            line = await asyncio.to_thread(sys.stdin.readline)
            if line.strip() and not got.done():
                got.set_result(line.strip())

        print("Waiting for the redirect (or paste the URL you landed on)...")
        paste = asyncio.create_task(read_paste())
        try:
            landed = await got
        finally:
            paste.cancel()
            await runner.cleanup()

        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(landed).query))
        if query.get("state") != request.state:
            sys.exit("state mismatch: start again")
        if "code" not in query:
            sys.exit(f"no code in the redirect: {query}")
        token = await kick.exchange_code(
            query["code"], code_verifier=request.code_verifier, redirect_uri=redirect_uri
        )
        user = await kick.as_user(token.user_id or 0).users.me()
        print(f"Saved tokens for {user.name} (user id {token.user_id}).")
        print("Granted scopes:", " ".join(token.scopes))
        missing = {str(s) for s in SCOPES} - set(token.scopes)
        if missing:
            print("Not granted (enable them in your app settings):", " ".join(sorted(missing)))


if __name__ == "__main__":
    asyncio.run(main())
