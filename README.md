# KickPython - Python API Wrapper

A Python wrapper for the Kick.com API that provides easy access to Kick.com's public API endpoints and WebSocket chat functionality.

## Installation

```bash
pip install kickpython
```

## Features

- OAuth 2.1 authentication with PKCE support
- Automatic token storage and refresh
- Channel management
- Chat message sending and receiving
- WebSocket-based chat listener
- Support for channel category management
- Proxy support

## Usage

### Basic Initialization

```python
from kickpython import KickAPI

# For OAuth 2.1 authentication (recommended)
api = KickAPI(
    client_id="your_client_id",
    client_secret="your_client_secret",
    redirect_uri="your_redirect_uri",
    db_path="kick_tokens.db"  # Optional, defaults to kick_tokens.db
)

# With proxy support
api = KickAPI(
    client_id="your_client_id",
    client_secret="your_client_secret",
    redirect_uri="your_redirect_uri",
    proxy="proxy.example.com:8080",
    proxy_auth="username:password"
)
```

### OAuth Authentication

```python
# Generate authorization URL with PKCE
auth_data = api.get_auth_url([
    "user:read",
    "channel:read",
    "channel:write",
    "chat:write",
    "events:subscribe"
])

# The auth URL for user authorization
auth_url = auth_data["auth_url"]
code_verifier = auth_data["code_verifier"]
state = auth_data["state"]

# After user authorization, exchange the code for tokens
token_data = await api.exchange_code(code, code_verifier)

# Tokens are automatically stored and managed
await api.start_token_refresh()  # Start automatic token refresh
```

### Chat Functionality

#### Sending Messages

```python
# Send a chat message
await api.post_chat(
    channel_id="123",
    content="Hello from the bot!"
)
```

#### Listening to Chat Messages

```python
# Define a message handler
async def message_handler(message):
    username = message['sender_username']
    content = message['content']
    badges = message['badges']
    chat_id = message['chat_id']
    print(f"{username}: {content}")

# Add the message handler
api.add_message_handler(message_handler)

# Connect to a channel's chat
await api.connect_to_chatroom("channel_username")
# Or connect using chatroom ID
await api.connect_to_chatroom("123456")
```

### Channel Management

```python
# Get channel information
channels = await api.get_channels()

# Update channel information
await api.update_channel(
    channel_id="123",
    category_id="456",
    stream_title="New Stream Title"
)
```

### Category Management

```python
# Get list of categories
categories = await api.get_categories()

# Search for specific categories
results = await api.get_categories(query="games")

# Get specific category
category = await api.get_category(category_id="123")
```

## Complete Examples

### OAuth Web Server

See [examples/oauth.py](kickpython/examples/oauth.py) for a complete Flask-based OAuth implementation that includes:
- OAuth authorization flow with PKCE
- Token management interface
- Token refresh and revocation
- Example API usage

### Chat Bot

See [examples/listen_all_channels.py](kickpython/examples/listen_all_channels.py) for a chat bot example that:
- Connects to multiple channels
- Handles chat messages
- Responds to commands
- Handles reconnection

## Error Handling

The API methods throw exceptions for error conditions:
- `ValueError` for invalid input parameters
- `Exception` for API errors with error messages

Example:
```python
try:
    await api.post_chat(channel_id="123", content="")
except ValueError as e:
    print("Invalid parameters:", e)
except Exception as e:
    print("API error:", e)
```

## Available Scopes

- `user:read` - Access user information
- `channel:read` - Access channel information
- `channel:write` - Update channel metadata
- `chat:write` - Send chat messages
- `events:subscribe` - Subscribe to channel events

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
