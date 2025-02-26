# KickPython Documentation

Welcome to the KickPython documentation. This documentation provides detailed information about using the Kick.com API wrapper for Python.

## Table of Contents

### [Configuration and Setup](configuration.md)
- API Initialization
- Proxy Configuration
- Database Management
- Error Handling

### [Authentication](auth.md)
- OAuth 2.1 Flow
- Token Management
- Available Scopes
- Error Handling

### [Chat API](chat.md)
- Sending Messages
- Real-time Message Listening
- Chatroom Management
- WebSocket Connection
- Error Handling

### [Channel Management](channels.md)
- Channel Information
- User Information
- Categories
- Error Handling

## Quick Start

1. Install the package:
```bash
pip install kickpython
```

2. Initialize the API:
```python
from kickpython import KickAPI

api = KickAPI(
    client_id="your_client_id",
    client_secret="your_client_secret",
    redirect_uri="your_redirect_uri"
)
```

3. Follow the authentication guide in [auth.md](auth.md) to get started.

## Examples

Find complete examples in the examples directory:
- [OAuth Web Server Example](../kickapi/examples/oauth.py)
- [Chat Bot Example](../kickapi/examples/listen_all_channels.py)

## Need Help?

If you encounter any issues or need help:
1. Check the error handling sections in each document
2. Look at the example code
3. File an issue on the GitHub repository