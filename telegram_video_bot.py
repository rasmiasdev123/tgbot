import os
import asyncio
from telethon import TelegramClient, events
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
import pickle

# ============ CONFIGURATION ============
# Replace these with your actual values
API_ID = '5041713'  # From my.telegram.org
API_HASH = '9c27474d00a8b8236307692d4b6f0434'  # From my.telegram.org
BOT_TOKEN = '8477203017:AAHarKtQkBdnfMR7DidTCd6tvE0ziAu0wFc'  # From @BotFather
PHONE_NUMBER = '+917540892472'  # Your Telegram phone number with country code

# Google Drive settings
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CREDENTIALS_FILE = 'credentials.json'  # Your downloaded Google credentials (OAuth)
SERVICE_ACCOUNT_FILE = 'service_account.json'  # Service account credentials (alternative)
TOKEN_PICKLE = 'token.pickle'
DRIVE_FOLDER_NAME = 'Telegram Videos'  # Folder name in Google Drive (will be created if doesn't exist)
USE_SERVICE_ACCOUNT = False  # Set to True if using service account instead of OAuth

# Download directory
DOWNLOAD_DIR = 'downloads'

# ============ GOOGLE DRIVE SETUP ============
def get_drive_service():
    """Authenticate and return Google Drive service"""
    if USE_SERVICE_ACCOUNT:
        # Use Service Account (no browser popup needed)
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return build('drive', 'v3', credentials=creds)
    else:
        # Use OAuth (requires browser authentication)
        creds = None
        
        # Load saved credentials
        if os.path.exists(TOKEN_PICKLE):
            with open(TOKEN_PICKLE, 'rb') as token:
                creds = pickle.load(token)
        
        # If no valid credentials, authenticate
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=8080)  # Specify port explicitly
            
            # Save credentials for future use
            with open(TOKEN_PICKLE, 'wb') as token:
                pickle.dump(creds, token)
        
        return build('drive', 'v3', credentials=creds)

def get_or_create_folder(service, folder_name):
    """Use an existing shared Google Drive folder (already shared with the service account)."""
    try:
        # Directly use the shared folder ID
        SHARED_FOLDER_ID = '1BQ17L--j7pOZ4RSP6d2fV8ptSCjStugJ'
        print(f"📁 Using shared folder ID: {SHARED_FOLDER_ID}")
        return SHARED_FOLDER_ID
    except Exception as e:
        print(f"Folder error: {e}")
        return None

def upload_to_drive(file_path, file_name):
    """Upload file to Google Drive"""
    try:
        service = get_drive_service()
        
        # Get or create the target folder
        folder_id = get_or_create_folder(service, DRIVE_FOLDER_NAME)
        
        if not folder_id:
            print("Failed to get/create folder")
            return None
        
        # Upload to specific folder
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]  # Upload to specific folder
        }
        media = MediaFileUpload(file_path, resumable=True)
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        # Make file accessible
        service.permissions().create(
            fileId=file.get('id'),
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        return file.get('webViewLink')
    except Exception as e:
        print(f"Drive upload error: {e}")
        return None

# ============ TELEGRAM BOT SETUP ============
# Create download directory
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Initialize single client that will handle everything
client = TelegramClient('session_name', API_ID, API_HASH)

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Handle /start command"""
    # Only respond to messages sent to the bot
    if event.is_private:
        await event.respond(
            "🤖 **Video Downloader Bot**\n\n"
            "Send me a Telegram video link and I'll download it to your Google Drive!\n\n"
            "**How to use:**\n"
            "1. Copy video link from any Telegram channel\n"
            "2. Send it to me\n"
            "3. Wait for upload to complete\n"
            "4. Get your Google Drive link!\n\n"
            "**Supported formats:**\n"
            "- t.me/channel/message_id\n"
            "- https://t.me/c/channel_id/message_id\n"
            "- Direct message links"
        )

@client.on(events.NewMessage)
async def message_handler(event):
    """Handle incoming messages with links"""
    # Only respond to private messages (not groups)
    if not event.is_private:
        return
    
    if event.raw_text.startswith('/'):
        return
    
    message_text = event.raw_text
    
    # Check if message contains a link
    if 't.me/' not in message_text and 'telegram.me/' not in message_text:
        await event.respond("❌ Please send a valid Telegram link")
        return
    
    await event.respond("⏳ Processing your request...")
    
    try:
        # Parse the link to get channel and message ID
        # Extract message details from various link formats
        if '/c/' in message_text:
            # Private channel link format
            parts = message_text.split('/c/')[-1].split('/')
            channel_id = int('-100' + parts[0])
            message_id = int(parts[1].split('?')[0])
        else:
            # Public channel link format
            parts = message_text.split('t.me/')[-1].split('/')
            channel_username = parts[0]
            message_id = int(parts[1].split('?')[0])
            channel_id = channel_username
        
        # Connect and download
        await client.connect()
        
        if not await client.is_user_authorized():
            await event.respond("⚠️ Bot needs authorization. Check console.")
            return
        
        await event.respond("📥 Downloading video...")
        
        # Get the message
        message = await client.get_messages(channel_id, ids=message_id)
        
        if not message or not message.media:
            await event.respond("❌ No video found in this link")
            return
        
        # Download the video
        file_path = await client.download_media(
            message.media,
            file=DOWNLOAD_DIR
        )
        
        if not file_path:
            await event.respond("❌ Failed to download video")
            return
        
        await event.respond("☁️ Uploading to Google Drive...")
        
        # Upload to Google Drive
        file_name = os.path.basename(file_path)
        drive_link = upload_to_drive(file_path, file_name)
        
        if drive_link:
            await event.respond(
                f"✅ **Upload Complete!**\n\n"
                f"📁 File: `{file_name}`\n"
                f"🔗 Link: {drive_link}\n\n"
                f"Your video is now in Google Drive!"
            )
        else:
            await event.respond("❌ Failed to upload to Google Drive")
        
        # Clean up downloaded file
        os.remove(file_path)
        
    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")
        print(f"Error details: {e}")

# ============ MAIN FUNCTION ============
async def main():
    """Main function to run the bot"""
    print("🤖 Starting bot...")
    
    # Start client with phone number (for user access)
    await client.start(phone=PHONE_NUMBER)
    print("✅ Client connected and authorized")
    print("✅ Bot is running!")
    print(f"📱 Logged in as: {(await client.get_me()).first_name}")
    print("\n💡 Now you can:")
    print("   1. Send messages to yourself (Saved Messages)")
    print("   2. Or create a channel and add yourself as admin")
    print("   3. Send video links there and the bot will respond\n")
    
    # Keep the bot running
    await client.run_until_disconnected()

# ============ RUN THE BOT ============
if __name__ == '__main__':
    asyncio.run(main())