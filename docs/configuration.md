# Configuration

## Basic Setup

The KickPython package provides a simple interface to interact with the Kick.com API. Here's how to get started:

```python
from kickpython import KickAPI

# Basic initialization
api = KickAPI()

# With OAuth credentials (recommended)
api = KickAPI(
    client_id="your_client_id",
    client_secret="your_client_secret",
    redirect_uri="your_redirect_uri"
)
```

## Configuration Options

The `KickAPI` class accepts several configuration options:

- `client_id`: OAuth client ID from Kick.com (required for OAuth)
- `client_secret`: OAuth client secret from Kick.com (required for OAuth)
- `redirect_uri`: OAuth redirect URI (required for OAuth)
- `base_url`: API base URL (default: "https://api.kick.com/public/v1")
- `oauth_base_url`: OAuth endpoint base URL (default: "https://id.kick.com")
- `db_path`: Path to SQLite database file (default: "kick_tokens.db")
- `proxy`: Proxy server address (optional)
- `proxy_auth`: Proxy authentication credentials (optional)

## Database Management

KickPython uses SQLite to store OAuth tokens and chatroom information. The database is created automatically at the specified `db_path` when initializing the API client.

### Database Structure

Two main tables are used:

1. `tokens`: Stores OAuth tokens
   - `channel_id`: Channel ID (primary key)
   - `access_token`: OAuth access token
   - `refresh_token`: OAuth refresh token
   - `expires_at`: Token expiration timestamp
   - `scope`: Token scopes

2. `kick_users`: Stores chatroom information
   - `kick_id`: Kick.com user ID
   - `user_id`: User ID
   - `chatroom_id`: Chatroom ID
   - Other user metadata fields

## Proxy Configuration

To use a proxy server:

```python
api = KickAPI(
    client_id="your_client_id",
    client_secret="your_client_secret",
    redirect_uri="your_redirect_uri",
    proxy="proxy.example.com:8080",
    proxy_auth="username:password"
)
```

## Error Handling

The API methods will raise exceptions for various error conditions:

- `ValueError`: Invalid input parameters
- `Exception`: API errors with error messages

Example error handling:

```python
try:
    await api.post_chat(channel_id="123", content="")
except ValueError as e:
    print("Invalid parameters:", e)
except Exception as e:
    print("API error:", e)
```