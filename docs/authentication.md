# Authentication

Kick's API takes two kinds of token. Both come from `https://id.kick.com`.

| Token | How you get it | What it can do |
| --- | --- | --- |
| App access token | Client credentials (your client ID and secret) | Public data: channels, users, livestreams, categories, event subscriptions for any channel |
| User access token | The user approves your app (authorization code flow with PKCE) | Act for that user, limited to the scopes they granted |

## App tokens

Pass your credentials and kickpython handles the rest:

```python
kick = KickClient("CLIENT_ID", "CLIENT_SECRET")
await kick.channels.get_by_slug("berkaydigital")     # fetches and caches an app token
```

The token is cached in memory and replaced shortly before it expires, or when Kick rejects it. `await kick.get_app_token()` returns it if you need the raw value.

## User tokens

### 1. Build the authorization URL

```python
request = kick.authorization_url(
    redirect_uri="http://localhost:9765/callback",
    scopes=[Scope.USER_READ, Scope.CHAT_WRITE],
)
```

`request.url` is where you send the user. Store `request.state` and `request.code_verifier` (in the user's session, for example) until the callback arrives. The redirect URI must match one set in your app settings exactly.

If your redirect URI uses `127.0.0.1`, kickpython adds the extra query parameter Kick's docs recommend, because Kick's login page rewrites the first `127.0.0.1` in the URL to `localhost`.

### 2. Handle the callback

Kick redirects to `redirect_uri?code=...&state=...`. Compare `state` with the one you stored and reject the request if they differ. Then:

```python
token = await kick.exchange_code(code, code_verifier=request.code_verifier,
                                 redirect_uri=request.redirect_uri)
token.user_id     # the Kick user who approved
token.scopes      # what they actually granted
```

To find the user ID, kickpython calls `GET /users` (needs `user:read`) or, failing that, `GET /channels` (needs `channel:read`). Request at least one of those scopes.

The token goes into the client's token store. Pass `store=False` to skip that.

### 3. Use it

```python
me = kick.as_user(token.user_id)
await me.chat.send("hi")
```

`as_user` returns a lightweight view that shares the session, token store and locks of the client it came from. Create one per request or per channel; that costs nothing.

Before each request the view loads the user's token from the store. If it expires within a minute, the view refreshes it first and saves the new pair. Concurrent requests for the same user wait on one refresh instead of racing, which matters because Kick rotates the refresh token on every refresh.

If Kick answers 401, kickpython introspects the token. An inactive token gets refreshed and the request retried once. An active token means the problem is a missing scope, so kickpython raises `UnauthorizedError` and names the scopes the token has.

If the refresh itself fails (the user revoked access, or the refresh token expired), you get `MissingTokenError`. Send the user through the flow again.

## Scopes

`kickpython.Scope` lists all of them. Kick only grants scopes that are switched on for your app in [the developer settings](https://kick.com/settings/developer), whatever you request.

| Scope | Needed for |
| --- | --- |
| `user:read` | `users.me()`, email of the authorized user |
| `channel:read` | `channels.me()` |
| `channel:write` | `channels.update()` |
| `channel:rewards:read` | `rewards.list()`, `rewards.redemptions()` |
| `channel:rewards:write` | creating, changing and resolving rewards |
| `chat:write` | `chat.send()`, `chat.reply()` |
| `streamkey:read` | `channel.stream.url` and `channel.stream.key` |
| `events:subscribe` | `events.subscribe()` with a user token |
| `moderation:ban` | `moderation.ban()`, `timeout()`, `unban()` |
| `moderation:chat_message:manage` | `chat.delete()` |
| `kicks:read` | `kicks.leaderboard()` |
| `ads:read` / `ads:write` | `ads.status()` / `ads.start_break()`, `ads.enroll()` |

## Token stores

| Store | Use it when |
| --- | --- |
| `MemoryTokenStore` (default) | Tests and short scripts |
| `JSONFileTokenStore(path)` | One process, simple setup. Writes the file atomically with mode 0600 |
| `SQLiteTokenStore(path)` | One or a few processes on one machine. Reads kickpython 0.x `kick_tokens.db` files |

`SQLiteTokenStore` creates its file readable by the owner only, and tightens an existing file (such as a 0.x `kick_tokens.db`) to the same.

To use Redis, Postgres or anything else, write a class with these four async methods:

```python
class MyStore:
    async def get(self, user_id: int) -> Token | None: ...
    async def set(self, token: Token) -> None: ...        # token.user_id is set
    async def delete(self, user_id: int) -> None: ...
    async def user_ids(self) -> list[int]: ...
```

Refresh locks live in the client process. If several processes share one store, only one of them should refresh a given user's token, or a rotated refresh token can be lost.

## Other token operations

```python
await kick.refresh_user_token(user_id)        # refresh now
await kick.refresh_all_user_tokens()          # refresh every stored user; returns {user_id: Token | error}
await kick.get_user_token(user_id)            # valid token, refreshed if needed
await kick.add_user_token(token)              # import a token you got elsewhere
info = await kick.as_user(user_id).introspect()
await kick.revoke_user(user_id)               # revoke at Kick, then delete; keeps the token if Kick refuses
await kick.revoke_token(raw_token, hint="access_token")
```

Kick's refresh tokens expire after a period of inactivity, and every refresh resets that window. A bot that may sit idle for weeks can call `refresh_all_user_tokens()` once a day.

## A fixed token

For quick scripts you can skip all of this:

```python
kick = KickClient(access_token="...")
```

That token is used for every request and never refreshed. `kick.as_user(user_id)` still means that user's stored token: if there is none, you get `MissingTokenError`, never the fixed token.
