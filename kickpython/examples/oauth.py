import asyncio
import os
import sys
import logging
import sqlite3
import time
from kickpython import KickAPI
from flask import Flask, request, redirect, session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

api = KickAPI(
    client_id=os.environ.get("KICK_CLIENT_ID"),
    client_secret=os.environ.get("KICK_CLIENT_SECRET"),
    redirect_uri=os.environ.get("KICK_REDIRECT_URI", "http://localhost:5000/callback"),
    session_ttl=3600  # Sessions expire after 1 hour if not used
)

@app.route('/')
def home():
    return """
    <h1>Kick OAuth Example</h1>
    <p>Go to <a href="/auth">Start OAuth Flow</a> to begin authentication for a channel.</p>
    <p>Or go to <a href="/manage">Manage Tokens</a> to see your authenticated channels.</p>
    """

@app.route('/auth')
def start_auth():
    auth_data = api.get_auth_url([
        "user:read",
        "channel:read",
        "channel:write",
        "chat:write",
        "events:subscribe"
    ])
    
    # Store both state and code verifier in Flask session for later use
    session['kick_state'] = auth_data["state"]
    session['kick_code_verifier'] = auth_data["code_verifier"]
    
    # Redirect user to Kick auth page
    return redirect(auth_data["auth_url"])

@app.route('/callback')
def callback():
    # Get query parameters
    code = request.args.get('code')
    received_state = request.args.get('state')
    
    # Get session data from Flask session
    expected_state = session.get('kick_state')
    code_verifier = session.get('kick_code_verifier')
    
    if not expected_state or not code_verifier:
        return "Error: No session found. Please start over.", 400
    
    # Verify state parameter to prevent CSRF
    if received_state != expected_state:
        return "Error: State mismatch. Possible CSRF attack.", 400
    
    # Exchange code for token
    async def exchange():
        try:
            result = await api.exchange_code(code, code_verifier)
            if result:
                # Start the automatic token refresh
                await api.start_token_refresh()
            return result
        except ValueError as e:
            return {"error": str(e)}
    
    token_data = asyncio.run(exchange())
    
    if not token_data or "error" in token_data:
        error_msg = token_data.get("error", "Unknown error") if token_data else "Failed to exchange code for token"
        return f"Error: {error_msg}", 400
    
    return f"""
    <h1>Authentication successful!</h1>
    <p>Token has been saved for your channel</p>
    <p>Access token: {token_data['access_token'][:10]}...</p>
    <p>Refresh token: {token_data['refresh_token'][:10]}...</p>
    <p>Scopes: {token_data['scope']}</p>
    <p>Token expires in: {token_data['expires_in']} seconds</p>
    <p><a href="/manage">Manage your tokens</a></p>
    """

@app.route('/manage')
def manage_tokens():
    # List all tokens in the database
    conn = api._init_db()  # Reuse the db connection method
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, scope, expires_at FROM tokens")
    tokens = cursor.fetchall()
    conn.close()
    
    current_time = int(time.time())
    html = "<h1>Managed Tokens</h1>"
    
    if not tokens:
        html += "<p>No tokens found. <a href='/auth'>Authenticate a channel</a></p>"
    else:
        html += "<table border='1'><tr><th>Channel ID</th><th>Scopes</th><th>Expires In</th><th>Actions</th></tr>"
        for channel_id, scopes, expires_at in tokens:
            expires_in = expires_at - current_time
            status = "Valid" if expires_in > 0 else "Expired"
            html += f"""
            <tr>
                <td>{channel_id}</td>
                <td>{scopes}</td>
                <td>{expires_in} seconds ({status})</td>
                <td>
                    <a href='/refresh/{channel_id}'>Refresh</a> | 
                    <a href='/revoke/{channel_id}'>Revoke</a>
                </td>
            </tr>
            """
        html += "</table>"
        
    html += "<p><a href='/auth'>Authenticate a new channel</a></p>"
    return html

@app.route('/refresh/<channel_id>')
def refresh_token(channel_id):
    async def do_refresh():
        return await api.refresh_token(channel_id)
        
    result = asyncio.run(do_refresh())
    
    if not result:
        return "Failed to refresh token", 400
        
    return f"""
    <h1>Token Refreshed</h1>
    <p>New token for channel {channel_id} expires in {result['expires_in']} seconds.</p>
    <p><a href='/manage'>Back to token management</a></p>
    """
    
@app.route('/revoke/<channel_id>')
def revoke_token(channel_id):
    async def do_revoke():
        return await api.revoke_token(channel_id)
        
    result = asyncio.run(do_revoke())
    
    if not result:
        return "Failed to revoke token", 400
        
    return f"""
    <h1>Token Revoked</h1>
    <p>The access token for channel {channel_id} has been revoked.</p>
    <p><a href='/manage'>Back to token management</a></p>
    """

# Example of using stored tokens with the API
async def example_api_usage(channel_id):
    try:
        # Get channel information with the stored token
        channel_info = await api.get_channels(channel_id=channel_id)
        print(f"Channel info: {channel_info}")
        
        # Send a chat message
        chat_result = await api.post_chat(channel_id, "Hello from the OAuth example!")
        print(f"Chat message sent: {chat_result}")
        
        # Update channel info
        update_result = await api.update_channel(
            channel_id=channel_id,
            category_id=123,  # Replace with actual category ID
            stream_title="Updated via OAuth API"
        )
        print(f"Channel updated: {update_result}")
        
    except Exception as e:
        print(f"Error using API: {e}")
    finally:
        await api.close()

async def cleanup():
    await api.stop_token_refresh()
    await api.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "api_example":
        # Run the API usage example with a specified channel ID
        if len(sys.argv) > 2:
            channel_id = sys.argv[2]
            asyncio.run(example_api_usage(channel_id))
        else:
            print("Please provide a channel ID: python oauth_example.py api_example CHANNEL_ID")
    else:
        # Start the Flask OAuth server
        try:
            app.run(debug=True, use_reloader=False)
        finally:
            asyncio.run(cleanup())