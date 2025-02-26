# Authentication

This document covers the OAuth 2.1 authentication functionality in the Kick Python API wrapper.

## OAuth 2.1 Flow

The API uses OAuth 2.1 with PKCE (Proof Key for Code Exchange) for secure authentication.

### Initialization

```python
from kickpy import KickAPI

api = KickAPI(
    client_id="your_client_id",
    client_secret="your_client_secret",
    redirect_uri="your_redirect_uri"
)
```

### Generate Authorization URL

```python
auth_data = api.get_auth_url([
    "user:read",
    "channel:read",
    "channel:write",
    "chat:write",
    "events:subscribe"
])

auth_url = auth_data["auth_url"]        # URL to redirect user to
code_verifier = auth_data["code_verifier"]  # Store this for token exchange
state = auth_data["state"]              # CSRF protection token
```

### Exchange Code for Tokens

After the user authorizes your application, exchange the code for tokens:

```python
token_data = await api.exchange_code(
    code="auth_code_from_redirect",
    code_verifier=code_verifier,
    channel_id="optional_channel_id"  # If not provided, will be fetched from token
)
```

The tokens are automatically stored in the SQLite database specified during initialization.

### Token Management

#### Automatic Token Refresh
```python
# Start automatic token refresh (refreshes hourly)
await api.start_token_refresh()

# Stop automatic token refresh
await api.stop_token_refresh()
```

#### Manual Token Operations
```python
# Manually refresh a token
new_token_data = await api.refresh_token(
    channel_id="123",
    refresh_token="optional_refresh_token"  # If not provided, fetched from DB
)

# Revoke a token
success = await api.revoke_token(
    channel_id="123",
    token_type="access_token"  # or "refresh_token"
)

# Check if token exists
has_token = api.check_token_exists("channel_id")
```

## Available Scopes

- `user:read` - Access user information
- `channel:read` - Access channel information
- `channel:write` - Update channel metadata
- `chat:write` - Send chat messages
- `events:subscribe` - Subscribe to channel events

## Error Handling

Authentication methods may throw:
- `ValueError` if required parameters are missing
- `Exception` with error details from the API

Example:
```python
try:
    await api.exchange_code(code="invalid_code", code_verifier="verifier")
except ValueError as e:
    print("Missing parameters:", e)
except Exception as e:
    print("Auth error:", e)
```