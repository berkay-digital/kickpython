# Changelog

## 1.1.0 (2026-11-25)

### Added

- Drops reward claim webhooks. `WebhookHandler` now accepts them (no `Kick-Event-Type` header, `Kick-Event-Timestamp` header) as `DROPS_REWARD_CLAIMED` with a `DropsRewardClaimed` payload. The aiohttp route handles them synchronously, as Kick requires: 200 after your handlers succeed, 500 if one fails, 503 for a copy that arrives while the first is still running.

### Docs

- Moderation: Kick's bare 400 for targets that can't be moderated.
- Offline `custom_tags` updates don't show until the channel is live.
- Drops endpoints return 500 for apps without an organization.

## 1.0.0 (2026-11-25)

A rewrite against the current Kick API. Not compatible with 0.x; see [docs/migrating.md](docs/migrating.md).

### Added

- `KickClient` with resource groups for every public endpoint: users, channels, chat (send, reply, delete), moderation (ban, timeout, unban), channel rewards and redemptions, livestreams v2 and per-user livestreams, categories v2, event subscriptions, KICKs leaderboard, ads and drops.
- App access tokens via client credentials, fetched and renewed automatically.
- `as_user(user_id)` views with lazy token refresh, one refresh at a time per user, and a retry after a 401 from an expired or revoked token.
- Clear errors for missing scopes: on a 401, the client introspects the token and reports its scopes instead of refreshing a valid token.
- Token stores: memory, JSON file, SQLite (reads 0.x databases), or your own.
- Typed pydantic models for every response and webhook payload, with `Page` cursor pagination.
- `WebhookHandler`: RSA signature checks with rate-limited key refetching for rotation, timestamp and duplicate checks, an optional broadcaster allowlist, typed events, and an aiohttp route.
- `LiveChat`: a websocket chat reader for many channels over one connection, with reconnects and ping handling. It looks up Kick's current Pusher key at runtime. Handlers run in order in their own task, so a slow handler never stalls the connection.
- Retries for 429 (with `Retry-After`) and failed GET requests.
- The 127.0.0.1 redirect URI workaround from Kick's docs.
- Tests against a fake Kick server, opt-in live tests, CI on Python 3.10 to 3.14.

### Removed

- `KickAPI` and its methods.
- Deprecated Kick endpoints (`/public/v1/categories`, `/public/v1/livestreams`, `/public/v1/token/introspect`).
- `logging.basicConfig()` on import and `print()` calls that leaked bearer tokens.
- The `asyncio`, `websockets`, `python-dotenv` and required `curl_cffi` dependencies.

### Fixed

- The websocket chat listener, broken since Kick rotated its Pusher key.
- Token refresh inside a running event loop (0.x called `asyncio.run()` from async code).
- Token revocation now sends `token_type_hint` as RFC 7009 and Kick's API spec define it.

## 0.1.5 and earlier

See the git history.
