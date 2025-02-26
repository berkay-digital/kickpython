# Channel Management

This document covers the channel management functionality in the Kick Python API wrapper.

## Channel Information

### Get Channels

Retrieve information about channels:

```python
# Get all channels
channels = await api.get_channels()

# Get specific channel
channels = await api.get_channels(channel_id="123")
```

### Update Channel

Update a channel's metadata:

```python
await api.update_channel(
    channel_id="123",
    category_id="456",      # New category ID
    stream_title="New Title" # New stream title
)
```

## User Information

### Get Users

Retrieve user information:

```python
# Get all users
users = await api.get_users()

# Get users for specific channel
users = await api.get_users(channel_id="123")
```

### Get Broadcaster ID

Get the broadcaster ID from an access token:

```python
broadcaster_id = await api.get_broadcaster_id(access_token)
```

### Fetch Channel Username

Get a channel's username from its ID:

```python
username = await api.fetch_channel_username("channel_id")
```

## Categories

### Get All Categories

```python
# Get all categories
categories = await api.get_categories()

# Search categories
results = await api.get_categories(query="gaming")
```

### Get Specific Category

```python
category = await api.get_category(category_id="123")
```

## Error Handling

Channel methods may throw:
- `ValueError` for invalid inputs
- `Exception` for API errors

Example:
```python
try:
    await api.update_channel(
        channel_id="123",
        category_id="invalid",
        stream_title=""
    )
except ValueError as e:
    print("Invalid parameters:", e)
except Exception as e:
    print("API error:", e)
```