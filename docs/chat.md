# Chat API

This document covers the chat functionality in the Kick Python API wrapper.

## Chat Message Management

### Sending Messages

Send messages to a channel's chat:

```python
await api.post_chat(
    channel_id="123",
    content="Hello from the bot!"
)
```

### Listening to Chat Messages

Set up a WebSocket connection to receive chat messages in real-time.

#### Basic Chat Connection

```python
# Connect using username
await api.connect_to_chatroom("channel_username")

# Or connect using chatroom ID
await api.connect_to_chatroom("123456")
```

#### Message Handler

```python
# Define a message handler function
async def message_handler(message):
    print(f"Username: {message['sender_username']}")
    print(f"Message: {message['content']}")
    print(f"Badges: {message['badges']}")
    print(f"Chat ID: {message['chat_id']}")
    print(f"Timestamp: {message['created_at']}")

# Add the handler to receive messages
api.add_message_handler(message_handler)
```

#### Continuous Chat Listener

```python
# Run a continuous chat listener with auto-reconnect
api.run_chat_listener("channel_username")
```

### Chatroom Management

#### Get Chatroom ID

```python
# Get chatroom ID from username
chatroom_id, user_data = await api.get_chatroom_id("username")

# Get channel ID from chatroom ID
channel_id = api.get_channel_id_from_chatroom("chatroom_id")
```

#### Monitor Multiple Channels

```python
# Get all available chatroom IDs
channels = await api.get_all_chatroom_ids()
# Returns list of dicts with:
# - username
# - chatroom_id
```

## WebSocket Connection

The chat connection uses Pusher WebSocket protocol with the following details:
- Auto-reconnection on disconnection
- Handles connection lifecycle
- Parses incoming chat events

### Handling WebSocket Events

The WebSocket connection automatically handles:
- Connection establishment
- Channel subscription
- Message parsing
- Error handling
- Reconnection logic

## Error Handling

Chat methods may throw:
- `ValueError` for invalid inputs
- `Exception` for API/WebSocket errors

Example:
```python
try:
    await api.connect_to_chatroom("invalid_username")
except ValueError as e:
    print("Invalid username:", e)
except Exception as e:
    print("Connection error:", e)
```