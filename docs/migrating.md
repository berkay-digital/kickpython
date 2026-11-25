# Migrating from kickpython 0.x

1.0 is a rewrite. The old `KickAPI` class is gone, and most methods have new names. The table maps the old calls to the new ones.

| 0.x | 1.0 |
| --- | --- |
| `KickAPI(client_id, client_secret, redirect_uri, db_path=...)` | `KickClient(client_id, client_secret, token_store=SQLiteTokenStore(db_path))`. The redirect URI moves to `authorization_url()` and `exchange_code()` |
| `api.get_auth_url(scopes)` returns a dict | `kick.authorization_url(redirect_uri, scopes)` returns an `AuthorizationRequest` with `.url`, `.state`, `.code_verifier` |
| `await api.exchange_code(code, code_verifier)` | `await kick.exchange_code(code, code_verifier=..., redirect_uri=...)` returns a `Token` |
| `await api.refresh_token(channel_id)` | `await kick.refresh_user_token(user_id)`. Usually unnecessary: tokens refresh on use |
| `await api.start_token_refresh()` | Not needed. Call `refresh_all_user_tokens()` on a timer only if a bot can sit idle for weeks |
| `await api.revoke_token(channel_id)` | `await kick.revoke_user(user_id)` |
| `await api.post_chat(channel_id, content)` | `await kick.as_user(user_id).chat.send(content)` |
| `await api.update_channel(channel_id, category_id, stream_title)` | `await kick.as_user(user_id).channels.update(category_id=..., stream_title=...)` |
| `await api.get_channels(channel_id=...)` | `await kick.channels.get(user_ids=[...])` or `kick.as_user(user_id).channels.me()` |
| `await api.get_users(channel_id=...)` | `await kick.users.get([...])` or `kick.as_user(user_id).users.me()` |
| `await api.get_categories(query)` | `await kick.categories.search(query)` |
| `await api.get_category(id)` | `await kick.categories.get(id)` |
| `api.add_message_handler(fn)` and `await api.connect_to_chatroom(name)` | `live = LiveChat()`, `@live.on_message`, `await live.join(name)`, `await live.run()` |
| `api.get_channel_id_from_chatroom(chatroom_id)` | `msg.broadcaster_user_id` on every `LiveChatMessage` |
| `await api.get_all_chatroom_ids()` | `await kick.channels.get(user_ids=await kick.token_store.user_ids())` and join each slug |
| `api.check_token_exists(channel_id)` | `await kick.token_store.get(user_id) is not None` |

## Things that changed in behavior

- **Responses are models, not dicts.** `channel.slug` instead of `channel["slug"]`. Call `.model_dump()` for a dict.
- **Errors raise.** 0.x returned `None` or the error JSON on failure. 1.0 raises `KickAPIError` subclasses.
- **"channel_id" is now "user_id".** 0.x stored tokens under `channel_id`, but the value was always the broadcaster's user ID, which is what Kick's API calls `broadcaster_user_id`. The new API calls it `user_id`.
- **Your token database still works.** `SQLiteTokenStore("kick_tokens.db")` reads the 0.x `tokens` table and adds one column. The old `kick_users` table is no longer used; you can drop it.
- **No more logging setup on import, and no prints.** 0.x called `logging.basicConfig()` and printed request headers, including the bearer token. Configure logging yourself.
- **The websocket chat works again.** Kick rotated its Pusher key, which broke `connect_to_chatroom` in 0.x. `LiveChat` looks up the current key at runtime.
- **Dependencies.** `curl_cffi`, `websockets`, `python-dotenv` and the PyPI `asyncio` package are gone. `curl_cffi` is optional via `pip install "kickpython[cloudflare]"`. 1.0 needs Python 3.10 or newer.
