import aiohttp
import asyncio
import websockets
import json
import logging
import sqlite3
import os
import base64
import hashlib
import secrets
import time
import urllib.parse
from typing import Optional, Dict, List, Any, Callable, Tuple
from curl_cffi import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KickAPI:
    def __init__(self, client_id=None, client_secret=None, redirect_uri=None, 
                 base_url="https://api.kick.com/public/v1", 
                 oauth_base_url="https://id.kick.com",
                 db_path="kick_tokens.db", 
                 proxy=None, proxy_auth=None, **kwargs):
        self.base_url = base_url
        self.oauth_base_url = oauth_base_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.db_path = db_path
        self.proxy = proxy
        self.proxy_auth = proxy_auth
        self.extra_kwargs = kwargs
        self.ws = None
        self.message_handlers = []
        self.session = None
        self.refresh_task = None
        self._refresh_interval = 3600  # Refresh tokens every hour
        self._should_refresh = False
        
        # Initialize database if needed
        self._init_db()
        
    def _init_db(self):
        """Initialize SQLite database for token storage"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS tokens (
            channel_id TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            scope TEXT
        )
        ''')
        
        # Add a table to store chatroom IDs if it doesn't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS kick_users (
            kick_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            slug TEXT,
            is_banned INTEGER,
            playback_url TEXT,
            vod_enabled INTEGER,
            subscription_enabled INTEGER,
            followers_count INTEGER,
            verified INTEGER,
            can_host INTEGER,
            username TEXT UNIQUE,
            bio TEXT,
            profile_pic TEXT,
            chatroom_id INTEGER NOT NULL
        )
        ''')
        
        conn.commit()
        conn.close()
        
    def _get_headers_with_token(self, token, content_type=None):
        headers = {
            "Accept": "application/json",
            "User-Agent": "KickPython/1.0",
            "Authorization": f"Bearer {token}"
        }
        
        if content_type:
            headers["Content-Type"] = content_type
            
        return headers

    def _get_headers(self, content_type=None, channel_id=None):
        """Get headers with authentication token if available"""
        headers = {
            "Accept": "application/json",
            "User-Agent": "KickPython/1.0"
        }
        
        # Try to get token for specific channel if provided
        if channel_id:
            token = self._get_token_for_channel(channel_id)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        
        if content_type:
            headers["Content-Type"] = content_type
            
        return headers

    def _get_token_for_channel(self, channel_id):
        """Get valid access token for a specific channel"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT access_token, refresh_token, expires_at FROM tokens WHERE channel_id = ?", 
            (channel_id,)
        )
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return None
        
        access_token, refresh_token, expires_at = result
        
        # Check if token has expired or will expire soon (within 5 minutes)
        current_time = int(time.time())
        if expires_at - current_time < 300:  # Less than 5 minutes remaining
            # Refresh the token
            new_tokens = asyncio.run(self.refresh_token(channel_id, refresh_token))
            if new_tokens:
                access_token = new_tokens["access_token"]
        
        return access_token
    
    def _generate_code_verifier(self):
        """Generate a random code verifier for PKCE"""
        code_verifier = secrets.token_urlsafe(64)
        # Trim to a valid length (between 43-128 chars)
        if len(code_verifier) > 128:
            code_verifier = code_verifier[:128]
        return code_verifier

    def _generate_code_challenge(self, code_verifier):
        """Generate code challenge from verifier using S256 method"""
        code_challenge = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        return base64.urlsafe_b64encode(code_challenge).decode('utf-8').rstrip('=')
    
    def _store_token(self, channel_id, access_token, refresh_token, expires_in, scope):
        """Store token in the database"""
        expires_at = int(time.time()) + int(expires_in)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO tokens 
            (channel_id, access_token, refresh_token, expires_at, scope) 
            VALUES (?, ?, ?, ?, ?)
            """,
            (channel_id, access_token, refresh_token, expires_at, scope)
        )
        conn.commit()
        conn.close()

    async def _init_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession()

    async def close(self):
        await self.stop_token_refresh()
        if self.session:
            await self.session.close()
            self.session = None
        if self.ws:
            await self.ws.close()
            self.ws = None

    def get_auth_url(self, scopes, state=None):
        """
        Generate authorization URL for OAuth flow
        
        Args:
            scopes (list): List of permission scopes to request
            state (str, optional): Random state string for CSRF protection
            
        Returns:
            dict: Dictionary containing auth_url and code_verifier
        """
        if not self.client_id or not self.redirect_uri:
            raise ValueError("client_id and redirect_uri must be provided to generate auth URL")
        
        # Generate PKCE code verifier and challenge
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        
        # Generate random state if not provided
        if state is None:
            state = secrets.token_urlsafe(16)
            
        # Construct scope string
        scope_str = " ".join(scopes)
            
        # Construct auth URL
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope_str,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state
        }
        
        auth_url = f"{self.oauth_base_url}/oauth/authorize?{urllib.parse.urlencode(params)}"
        
        return {
            "auth_url": auth_url,
            "code_verifier": code_verifier,
            "state": state
        }
    
    async def exchange_code(self, code, code_verifier, channel_id=None):
        """
        Exchange authorization code for tokens
        
        Args:
            code (str): Authorization code from redirect
            code_verifier (str): PKCE code verifier used during authorization
            channel_id (str): Channel ID for storing the tokens
            
        Returns:
            dict: Token response containing access and refresh tokens
        """
        if not self.client_id or not self.client_secret or not self.redirect_uri:
            raise ValueError("client_id, client_secret, and redirect_uri must be provided")
        
        await self._init_session()
        
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
            "code": code
        }
        
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        async with self.session.post(
            f"{self.oauth_base_url}/oauth/token",
            headers=headers,
            data=data
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Failed to exchange code: {error_text}")
                return None
            
            token_data = await response.json()
            
            # Get channel_id if not provided
            if not channel_id:
                channel_id = await self.get_broadcaster_id(token_data["access_token"])
                if not channel_id:
                    logger.error("Could not determine broadcaster ID from token")
                    return None
            
            # Store token in database
            self._store_token(
                channel_id,
                token_data["access_token"],
                token_data["refresh_token"],
                token_data["expires_in"],
                token_data["scope"]
            )
            
            return token_data
    
    async def refresh_token(self, channel_id, refresh_token=None):
        """
        Refresh an access token
        
        Args:
            channel_id (str): Channel ID whose token to refresh
            refresh_token (str, optional): Refresh token to use. If not provided, will be fetched from DB
            
        Returns:
            dict: New token data or None if failed
        """
        if not self.client_id or not self.client_secret:
            raise ValueError("client_id and client_secret must be provided to refresh tokens")
            
        await self._init_session()
        
        # Get refresh token from DB if not provided
        if not refresh_token:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT refresh_token FROM tokens WHERE channel_id = ?", (channel_id,))
            result = cursor.fetchone()
            conn.close()
            
            if not result:
                logger.error(f"No refresh token found for channel {channel_id}")
                return None
                
            refresh_token = result[0]
        
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token
        }
        
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        async with self.session.post(
            f"{self.oauth_base_url}/oauth/token",
            headers=headers,
            data=data
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Failed to refresh token: {error_text}")
                return None
                
            token_data = await response.json()
            
            # Store updated tokens
            self._store_token(
                channel_id,
                token_data["access_token"],
                token_data["refresh_token"],
                token_data["expires_in"],
                token_data["scope"]
            )
            
            return token_data
    
    async def revoke_token(self, channel_id, token_type="access_token"):
        """
        Revoke a token
        
        Args:
            channel_id (str): Channel ID whose token to revoke
            token_type (str): Type of token to revoke ('access_token' or 'refresh_token')
            
        Returns:
            bool: True if successful, False otherwise
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT {token_type} FROM tokens WHERE channel_id = ?", (channel_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            logger.error(f"No {token_type} found for channel {channel_id}")
            return False
            
        token = result[0]
        
        await self._init_session()
        
        params = {
            "token": token,
            "token_hint_type": token_type
        }
        
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        async with self.session.post(
            f"{self.oauth_base_url}/oauth/revoke",
            headers=headers,
            params=params
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Failed to revoke token: {error_text}")
                return False
                
            # Remove token from database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tokens WHERE channel_id = ?", (channel_id,))
            conn.commit()
            conn.close()
            
            return True

    async def get_categories(self, query=None, **kwargs):
        await self._init_session()
        params = {}
        if query:
            params["q"] = query
            
        async with self.session.get(
            f"{self.base_url}/categories",
            headers=self._get_headers(),
            params=params,
            **kwargs
        ) as response:
            return await response.json()

    async def get_category(self, category_id, **kwargs):
        await self._init_session()
        async with self.session.get(
            f"{self.base_url}/categories/{category_id}",
            headers=self._get_headers(),
            **kwargs
        ) as response:
            return await response.json()

    async def post_chat(self, channel_id, content):
        await self._init_session()
        
        url = f"{self.base_url}/chat"
        data = {
            "content": content,
            "type": "bot"
        }
        print(data)
        print(url)
        print(self._get_headers(content_type="application/json", channel_id=channel_id))
        
        if not content:
            raise ValueError("Content cannot be empty")
        if not channel_id:
            raise ValueError("Channel ID cannot be empty")
        async with self.session.post(
            url,
            headers=self._get_headers(content_type="application/json", channel_id=channel_id),
            json=data
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"Failed to send message: {error_text}")
            return await response.json()

    def add_message_handler(self, handler: Callable):
        self.message_handlers.append(handler)

    async def _handle_ws_message(self, message: str):
        try:
            data = json.loads(message)
            event = data.get('event')
            
            if "ChatMessageEvent" in event:
                chat_message = json.loads(data.get('data', '{}'))
                channel = data.get('channel', '').split('.')
                chat_id = channel[1] if len(channel) > 1 else None
                
                if chat_id:
                    message_info = {
                        'sender_username': chat_message.get('sender', {}).get('username'),
                        'content': chat_message.get('content'),
                        'badges': [badge.get('text') for badge in chat_message.get('sender', {}).get('identity', {}).get('badges', [])],
                        'created_at': chat_message.get('created_at'),
                        'chat_id': chat_id
                    }
                    
                    for handler in self.message_handlers:
                        await handler(message_info)
                        
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def get_chatroom_id(self, username):
        """
        Get the chatroom ID for a Kick.com username
        
        Args:
            username (str): Kick.com username to get chatroom ID for
            
        Returns:
            tuple: (chatroom_id, user_data) or (None, None) if not found
        """
        # First, try to get the user from the database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT chatroom_id FROM kick_users WHERE username = ?", (username.lower(),))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return result[0], None
            
        url_base = "https://kick.com/api/v2/channels/"
        proxies = None
        
        if self.proxy and self.proxy_auth:
            proxies = {
                "http": f"http://{self.proxy_auth}@{self.proxy}",
                "https": f"http://{self.proxy_auth}@{self.proxy}"
            }
        
        max_retries = 4
        username_variations = [
            username,
            username.replace('_', '-'),
            username.replace('-', '_'),
            username.replace('_', '-').replace('-', '_')
        ]
        
        for attempt in range(max_retries):
            current_username = username_variations[attempt % len(username_variations)]
            url = url_base + current_username
            
            try:
                response_kwargs = {"impersonate": "chrome"}
                if proxies:
                    response_kwargs["proxies"] = proxies
                
                response = requests.get(url, **response_kwargs)
                
                if response.status_code == 200:
                    try:
                        data = json.loads(response.text)
                        chatroom_id = data['chatroom']['id']
                        
                        # Save the data to the database
                        conn = sqlite3.connect(self.db_path)
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO kick_users 
                            (kick_id, user_id, slug, is_banned, playback_url, vod_enabled, 
                            subscription_enabled, followers_count, verified, can_host, 
                            username, bio, profile_pic, chatroom_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                data['id'],
                                data['user_id'],
                                data['slug'],
                                1 if data['is_banned'] else 0,
                                data['playback_url'],
                                1 if data['vod_enabled'] else 0,
                                1 if data['subscription_enabled'] else 0,
                                data['followers_count'],
                                1 if data['verified'] else 0,
                                1 if data['can_host'] else 0,
                                data['user']['username'].lower(),
                                data['user']['bio'],
                                data['user']['profile_pic'],
                                chatroom_id
                            )
                        )
                        conn.commit()
                        conn.close()
                        
                        logger.info(f"Found and saved chatroom ID: {chatroom_id} for user: {current_username}")
                        return chatroom_id, data
                        
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Error processing JSON for {current_username}: {str(e)}")
                else:
                    logger.warning(f"Failed to load page for {current_username}, status code: {response.status_code} on attempt {attempt + 1}")
                    
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"An error occurred while fetching chatroom ID for {current_username} on attempt {attempt + 1}: {e}")
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    
        logger.error(f"Failed to retrieve chatroom ID for {username} after {max_retries} attempts")
        return None, None

    async def connect_to_chatroom(self, username_or_chatroom_id: str):
        """
        Connect to a chatroom by username or chatroom ID
        
        Args:
            username_or_chatroom_id (str): Username or chatroom ID to connect to
        """
        # Check if input is numeric (chatroom ID) or a username
        chatroom_id = None
        if username_or_chatroom_id.isdigit():
            chatroom_id = username_or_chatroom_id
        else:
            # Fetch chatroom ID for the username
            chatroom_id, _ = await self.get_chatroom_id(username_or_chatroom_id)
            
        if not chatroom_id:
            raise ValueError(f"Could not find chatroom ID for {username_or_chatroom_id}")
            
        APP_KEY = "32cbd69e4b950bf97679"
        CLUSTER = "us2"
        
        ws_url = f"wss://ws-{CLUSTER}.pusher.com/app/{APP_KEY}?protocol=7&client=js&version=8.4.0-rc2&flash=false"
        
        async with websockets.connect(ws_url) as websocket:
            self.ws = websocket
            
            # Subscribe to the chatroom
            subscribe_message = {
                "event": "pusher:subscribe",
                "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"}
            }
            await websocket.send(json.dumps(subscribe_message))
            
            logger.info(f"Connected to chatroom {chatroom_id}")
            
            try:
                while True:
                    message = await websocket.recv()
                    await self._handle_ws_message(message)
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            finally:
                self.ws = None

    def get_channel_id_from_chatroom(self, chatroom_id):
        """
        Get the channel ID from a chatroom ID
        
        Args:
            chatroom_id (str): Chatroom ID to get the channel ID for
            
        Returns:
            str: Channel ID or None if not found
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM kick_users WHERE chatroom_id = ?", (chatroom_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return str(result[0])
            return None
        except Exception as e:
            logger.error(f"Database error: {e}")
            return None

    def check_token_exists(self, channel_id):
        """
        Check if a token exists for a given channel ID
        
        Args:
            channel_id (str): Channel ID to check for token existence
            
        Returns:
            bool: True if token exists, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT access_token FROM tokens WHERE channel_id = ?", (channel_id,))
            result = cursor.fetchone()
            conn.close()
            
            return result is not None
        except Exception as e:
            logger.error(f"Database error: {e}")
            return False

    async def fetch_channel_username(self, channel_id):
        """
        Fetch the username for a given channel ID
        
        Args:
            channel_id (str): Channel ID to fetch the username for
            
        Returns:
            str: Username or a default string if not found
        """
        try:
            logger.info(f"Fetching username for channel ID: {channel_id}")
            response = await self.get_channels(channel_id=channel_id)
            
            if not response or not response.get('data'):
                logger.warning(f"No channel data returned for channel_id: {channel_id}")
                return f"channel_{channel_id}"
            
            channel_data = response['data'][0]
            return channel_data.get('slug', f"channel_{channel_id}")
        except Exception as e:
            logger.error(f"Error fetching channel username: {e}")
            return f"channel_{channel_id}"

    async def start_chat_listener(self, username_or_chatroom_id: str):
        while True:
            try:
                await self.connect_to_chatroom(username_or_chatroom_id)
            except Exception as e:
                logger.error(f"Connection error: {e}")
                await asyncio.sleep(5)  # Wait before reconnecting

    def run_chat_listener(self, username_or_chatroom_id: str):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.start_chat_listener(username_or_chatroom_id))

    # Keep other methods as async
    async def update_channel(self, channel_id, category_id, stream_title, **kwargs):
        await self._init_session()
        data = {
            "category_id": category_id,
            "stream_title": stream_title
        }
        
        async with self.session.patch(
            f"{self.base_url}/channels",
            headers=self._get_headers(content_type="application/json", channel_id=channel_id),
            json=data,
            **kwargs
        ) as response:
            return await response.json()

    async def get_users(self, channel_id=None, **kwargs):
        await self._init_session()
        async with self.session.get(
            f"{self.base_url}/users",
            headers=self._get_headers(channel_id=channel_id),
            **kwargs
        ) as response:
            return await response.json()
    
    async def get_channels(self, channel_id=None, **kwargs):
        await self._init_session()
        async with self.session.get(
            f"{self.base_url}/channels",
            headers=self._get_headers(channel_id=channel_id),
            **kwargs
        ) as response:
            return await response.json()

    async def start_token_refresh(self):
        self._should_refresh = True
        if self.refresh_task is None:
            self.refresh_task = asyncio.create_task(self._token_refresh_loop())

    async def stop_token_refresh(self):
        self._should_refresh = False
        if self.refresh_task:
            self.refresh_task.cancel()
            try:
                await self.refresh_task
            except asyncio.CancelledError:
                pass
            self.refresh_task = None

    async def _token_refresh_loop(self):
        while self._should_refresh:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT channel_id FROM tokens")
                channels = cursor.fetchall()
                conn.close()

                for (channel_id,) in channels:
                    try:
                        await self.refresh_token(channel_id)
                    except Exception as e:
                        logger.error(f"Failed to refresh token for channel {channel_id}: {e}")

            except Exception as e:
                logger.error(f"Error in token refresh loop: {e}")

            await asyncio.sleep(self._refresh_interval)

    async def get_broadcaster_id(self, access_token):
        await self._init_session()
        
        headers = self._get_headers_with_token(access_token)
        
        async with self.session.get(
            f"{self.base_url}/channels",
            headers=headers
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Failed to get broadcaster ID: {error_text}")
                return None
                
            data = await response.json()
            if 'data' in data and len(data['data']) > 0:
                return data['data'][0]['broadcaster_user_id']
            
            return None

    async def get_all_chatroom_ids(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT username, chatroom_id FROM kick_users")
        user_results = cursor.fetchall()
        
        cursor.execute("SELECT channel_id FROM tokens")
        token_results = cursor.fetchall()
        
        conn.close()
        
        channels = []
        for (channel_id,) in token_results:
            found = False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT chatroom_id FROM kick_users WHERE user_id = ?", (channel_id,))
            chat_result = cursor.fetchone()
            conn.close()
            
            if chat_result:
                found = True
                logger.info(f"Found existing chatroom ID {chat_result[0]} for channel ID {channel_id}")
            
            if not found:
                try:
                    username = await self.fetch_channel_username(channel_id)
                    chatroom_id, _ = await self.get_chatroom_id(username)
                    
                    if chatroom_id:
                        logger.info(f"Found chatroom ID {chatroom_id} for channel {channel_id} with username {username}")
                except Exception as e:
                    logger.error(f"Error getting chatroom ID for channel {channel_id}: {e}")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT username, chatroom_id FROM kick_users")
        updated_user_results = cursor.fetchall()
        
        conn.close()
        
        for username, chatroom_id in updated_user_results:
            channels.append({
                'username': username,
                'chatroom_id': str(chatroom_id)
            })
        
        logger.info(f"Found {len(channels)} channels to monitor")
        return channels