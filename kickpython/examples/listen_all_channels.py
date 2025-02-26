import asyncio
import sqlite3
from kickpython import KickAPI
import logging
import os
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ChatListener:
    def __init__(self, proxy=None, proxy_auth=None, db_path="kick_tokens.db"):
        self.api = KickAPI(db_path=db_path, proxy=proxy, proxy_auth=proxy_auth)
        self.db_path = db_path
        self.channels = []
        self.running_tasks = {}
        self.api.add_message_handler(self.message_handler)

    async def message_handler(self, message):
        if message.get('content') == '!test':
            try:
                chatroom_id = message.get('chat_id')
                sender = message.get('sender_username')
                logger.info(f"Received !test command from {sender} in chatroom {chatroom_id}")
                
                channel_id = self.api.get_channel_id_from_chatroom(chatroom_id)
                if channel_id:
                    token_exists = self.api.check_token_exists(channel_id)
                    if token_exists:
                        await self.api.post_chat(channel_id, "Working!")
                        logger.info(f"Sent 'Working!' response to channel {channel_id} (chatroom {chatroom_id})")
                    else:
                        logger.warning(f"No token found for channel {channel_id} (chatroom {chatroom_id}), can't respond")
                else:
                    logger.warning(f"No channel ID mapping found for chatroom {chatroom_id}, can't respond")
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def start_listener_for_channel(self, chatroom_id, username):
        try:
            logger.info(f"Starting listener for {username} (chatroom ID: {chatroom_id})")
            while True:
                try:
                    await self.api.connect_to_chatroom(chatroom_id)
                except Exception as e:
                    logger.error(f"Connection error for {username}: {e}")
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info(f"Listener for {username} was cancelled")
            raise

    async def start_all_listeners(self):
        self.channels = await self.api.get_all_chatroom_ids()
        
        tasks = []
        for channel in self.channels:
            task = asyncio.create_task(
                self.start_listener_for_channel(
                    channel['chatroom_id'], 
                    channel['username']
                )
            )
            tasks.append(task)
            self.running_tasks[channel['chatroom_id']] = task
            
            await asyncio.sleep(1)
        
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Error in listeners: {e}")

async def main():
    listener = ChatListener(
        proxy=os.getenv('PROXY', None),
        proxy_auth=os.getenv('PROXY_AUTH', None),
    )
    
    await listener.start_all_listeners()

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")