# kickpython

Async Python client for the Kick.com public API, published on PyPI as `kickpython`.

## Layout

- `src/kickpython/client.py`: `KickClient` and one `*API` class per endpoint group.
- `src/kickpython/_http.py`: transport. Token selection, refresh with per-user locks, 401 handling (introspect, then refresh), retries, error mapping.
- `tokens.py` token model and stores, `oauth.py` PKCE and the authorize URL, `models.py` response models, `events.py` webhook payloads, `webhooks.py` verification and dispatch, `realtime.py` the unofficial Pusher chat reader.
- `tests/`: offline tests against a fake aiohttp server (`conftest.py`). `test_live.py` hits the real API and only runs with `-m live`.

## Commands

```bash
uv sync
uv run pytest
uv run ruff check src tests examples && uv run ruff format --check src tests examples
uv run mypy && uv run mypy --strict examples
KICK_CLIENT_ID=... KICK_CLIENT_SECRET=... uv run pytest -m live
```

## Rules

- Kick's spec lives at https://api.kick.com/swagger/doc.yaml, the docs at https://github.com/KickEngineering/KickDevDocs. Check both before adding or changing an endpoint, and test the change live. The spec has been wrong before: it says `/public/v2/categories` takes comma-separated lists, but the API only accepts repeated keys.
- Kick returns 401, not 403, for a valid token that lacks a scope.
- Never log or `repr()` tokens or client secrets.
- Models subclass `KickModel` (extra fields allowed). Convert Kick's `""` and `0001-01-01T00:00:00Z` to `None` with `OptionalStr` / `OptionalDatetime`.
- Don't wrap endpoints Kick marks deprecated.
- `realtime.py` is unofficial. Keep it isolated and keep the fallback Pusher key current (`DEFAULT_PUSHER_KEY`).

## Releasing

1. Bump `src/kickpython/_version.py` and add a `CHANGELOG.md` entry.
2. `uv build && uvx twine check --strict dist/*`
3. `uv publish` with a PyPI token, then tag `vX.Y.Z` and create a GitHub release.
