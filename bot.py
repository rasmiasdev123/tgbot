#!/usr/bin/env python3
"""
Sequential Telegram Video Downloader Bot
Fixed media fetching for private channels
"""
import os
import asyncio
import time
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, ChannelPrivateError
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle

print("="*70)
print("🚀 TELEGRAM VIDEO DOWNLOADER BOT - SEQUENTIAL MODE (FIXED)")
print("="*70)

# ============ CONFIGURATION ============
API_ID = 5041713
API_HASH = '9c27474d00a8b8236307692d4b6f0434'
BOT_TOKEN = '8477203017:AAHarKtQkBdnfMR7DidTCd6tvE0ziAu0wFc'
PHONE = '+917540892472'

# Settings
DRIVE_FOLDER_ID = '1e1KS9b8iqNMMX4c3nlJvrUCaO2sO5ANO'
FOLDER_ID_FILE = 'folder_id.txt'
TOKEN_PICKLE = 'token.pickle'
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Load custom folder ID if exists
if os.path.exists(FOLDER_ID_FILE):
    try:
        with open(FOLDER_ID_FILE, 'r') as f:
            custom_id = f.read().strip()
            if custom_id:
                DRIVE_FOLDER_ID = custom_id
                print(f"📁 Using custom folder ID: {DRIVE_FOLDER_ID}")
    except:
        pass

# Performance settings
PROGRESS_UPDATE_INTERVAL = 5
CONNECTION_RETRIES = 3
MESSAGE_DELAY = 0.5

# Track processing
user_queue = {}
user_tasks = {}
last_message_time = {}

# ============ GOOGLE DRIVE ============
def get_drive_service():
    with open(TOKEN_PICKLE, 'rb') as token:
        creds = pickle.load(token)
    return build('drive', 'v3', credentials=creds)

async def upload_to_drive_async(file_path, file_name):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, upload_to_drive_sync, file_path, file_name)

def upload_to_drive_sync(file_path, file_name):
    global DRIVE_FOLDER_ID
    try:
        service = get_drive_service()
        file_metadata = {'name': file_name, 'parents': [DRIVE_FOLDER_ID]}
        media = MediaFileUpload(file_path, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        service.permissions().create(fileId=file.get('id'), body={'type': 'anyone', 'role': 'reader'}).execute()
        return True
    except Exception as e:
        print(f"Upload error: {e}")
        return False

# ============ RATE LIMITER ============
async def safe_send_message(bot_client, chat_id, text, user_id=None):
    if user_id:
        now = time.time()
        last_time = last_message_time.get(user_id, 0)
        wait_time = MESSAGE_DELAY - (now - last_time)
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        last_message_time[user_id] = time.time()
    
    try:
        return await bot_client.send_message(chat_id, text)
    except Exception as e:
        if "wait" in str(e).lower():
            import re
            match = re.search(r'(\d+) seconds', str(e))
            if match:
                wait = int(match.group(1))
                print(f"Rate limited, waiting {wait}s")
                await asyncio.sleep(wait + 1)
                return await bot_client.send_message(chat_id, text)
        raise

async def safe_edit_message(message, text):
    try:
        return await message.edit(text)
    except Exception as e:
        if "wait" in str(e).lower():
            print(f"Rate limit on edit, skipping")
            return None
        raise

# ============ IMPROVED LINK PARSER ============
def parse_link(text):
    """Parse Telegram link and return channel_id, topic_id, msg_id, is_private"""
    try:
        text = text.strip()
        is_private = False
        topic_id = None
        
        if '/c/' in text:
            is_private = True
            # Format: t.me/c/CHANNEL_ID/TOPIC_ID/MESSAGE_ID or t.me/c/CHANNEL_ID/MESSAGE_ID
            parts = text.split('/c/')[-1].split('/')
            channel_id = int('-100' + parts[0])
            
            if len(parts) >= 3:
                # Has topic ID
                topic_id = int(parts[1])
                msg_id = int(parts[2].split('?')[0])
            else:
                # No topic, just message
                msg_id = int(parts[1].split('?')[0])
        else:
            # Public channel format: t.me/channel/123 or t.me/channel/topic/123
            parts = text.split('t.me/')[-1].split('/')
            channel_username = parts[0].strip('@')
            
            if len(parts) >= 3:
                topic_id = int(parts[1])
                msg_id = int(parts[2].split('?')[0])
            else:
                msg_id = int(parts[1].split('?')[0])
            
            channel_id = channel_username
        
        return channel_id, topic_id, msg_id, is_private
    except Exception as e:
        print(f"Parse error: {e}")
        return None, None, None, False

# ============ IMPROVED MESSAGE FETCHER ============
async def fetch_message_from_channel(user_client, channel_id, topic_id, msg_id, is_private):
    """Fetch message with proper handling for both public and private channels"""
    try:
        if is_private:
            await user_client.get_dialogs()
            
            if topic_id:
                message = await user_client.get_messages(channel_id, ids=msg_id, reply_to=topic_id)
            else:
                message = await user_client.get_messages(channel_id, ids=msg_id)
        else:
            entity = await user_client.get_entity(channel_id)
            if topic_id:
                message = await user_client.get_messages(entity, ids=msg_id, reply_to=topic_id)
            else:
                message = await user_client.get_messages(entity, ids=msg_id)
        
        return message
        
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        raise

# ============ PROGRESS TRACKER ============
class DownloadProgress:
    def __init__(self, file_name, message_obj, idx, total):
        self.file_name = file_name
        self.message = message_obj
        self.idx = idx
        self.total = total
        self.last_update = 0
        self.last_percent = 0
        self.start_time = time.time()
        
    async def update(self, current, total):
        percent = (current / total) * 100 if total > 0 else 0
        now = time.time()
        
        if (now - self.last_update >= PROGRESS_UPDATE_INTERVAL or 
            percent >= 99.9 or percent - self.last_percent >= 20):
            
            self.last_update = now
            self.last_percent = percent
            time_diff = now - self.start_time
            speed = current / time_diff / (1024 * 1024) if time_diff > 0 else 0
            
            if speed > 0:
                remaining_mb = (total - current) / (1024 * 1024)
                eta_seconds = remaining_mb / speed
                eta_str = f"{int(eta_seconds//60)}m {int(eta_seconds%60)}s"
            else:
                eta_str = "calculating..."
            
            try:
                await safe_edit_message(
                    self.message,
                    f"📥 **Downloading [{self.idx}/{self.total}]**\n\n"
                    f"📄 `{self.file_name[:40]}{'...' if len(self.file_name) > 40 else ''}`\n\n"
                    f"**Progress:** {percent:.1f}%\n"
                    f"**Downloaded:** {current/(1024*1024):.1f} MB / {total/(1024*1024):.1f} MB\n"
                    f"**Speed:** {speed:.2f} MB/s\n"
                    f"**ETA:** {eta_str}"
                )
            except Exception as e:
                print(f"Progress update error: {e}")

# ============ SINGLE FILE PROCESSOR (FIXED) ============
async def process_single_file(bot_client, user_client, chat_id, link_text, idx, total, batch_summary_msg=None, user_id=None):
    file_name = "Unknown"
    status_msg = None
    
    try:
        status_msg = await safe_send_message(bot_client, chat_id, f"📍 [{idx}/{total}] Initializing...", user_id)
        await asyncio.sleep(0.3)
        
        channel_id, topic_id, msg_id, is_private = parse_link(link_text)
        if not channel_id:
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Invalid link format")
            return {'success': False, 'error': 'Invalid link', 'file_name': f'Link {idx}'}
        
        print(f"[{idx}/{total}] Processing: channel={channel_id}, topic={topic_id}, msg={msg_id}, private={is_private}")
        await safe_edit_message(status_msg, f"🔍 [{idx}/{total}] Fetching message...")
        
        try:
            message = await fetch_message_from_channel(user_client, channel_id, topic_id, msg_id, is_private)
            print(f"[{idx}/{total}] Message fetched successfully")
        except Exception as e:
            error_msg = str(e)
            print(f"[{idx}/{total}] Fetch error: {error_msg}")
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] {error_msg[:50]}")
            return {'success': False, 'error': error_msg[:50], 'file_name': f'Link {idx}'}
        
        if not message:
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Message not found")
            return {'success': False, 'error': 'Message not found', 'file_name': f'Link {idx}'}
        
        # Debug: Print message type
        print(f"[{idx}/{total}] Message type: {type(message).__name__}")
        
        # Check if it's a service message (topic creation, etc.)
        if type(message).__name__ == 'MessageService':
            print(f"[{idx}/{total}] Service message detected: {message.action}")
            
            # The fetch function should have already tried to get media from topic
            # If we're still here with a service message, no media was found
            if hasattr(message.action, 'title'):
                topic_title = message.action.title
                await safe_edit_message(
                    status_msg, 
                    f"❌ [{idx}/{total}] No media found\n\n"
                    f"This is a topic: '{topic_title}'\n"
                    f"Searched the topic but found no media files.\n\n"
                    f"Please check if files exist in this topic."
                )
            else:
                await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Service message, no media")
            
            return {'success': False, 'error': 'No media in topic', 'file_name': f'Link {idx}'}
        
        # Debug: Print media info
        print(f"[{idx}/{total}] Has media attr: {hasattr(message, 'media')}")
        print(f"[{idx}/{total}] Media value: {message.media if hasattr(message, 'media') else 'NO ATTR'}")
        
        # Check for any type of media
        if not hasattr(message, 'media') or message.media is None:
            await safe_edit_message(
                status_msg, 
                f"❌ [{idx}/{total}] No media in message\n\n"
                f"**Possible reasons:**\n"
                f"• Wrong message link\n"
                f"• Message has only text\n"
                f"• Topic/thread header instead of actual file\n\n"
                f"Make sure you're linking to a message with an actual file!"
            )
            return {'success': False, 'error': 'No media', 'file_name': f'Link {idx}'}
        
        # Debug: Print media type and details
        print(f"[{idx}/{total}] Media type: {type(message.media).__name__}")
        
        # Get file name based on media type
        file_name = f"file_{msg_id}"
        
        if hasattr(message.media, 'document') and message.media.document:
            print(f"[{idx}/{total}] Document detected")
            for attr in message.media.document.attributes:
                if hasattr(attr, 'file_name') and attr.file_name:
                    file_name = attr.file_name
                    print(f"[{idx}/{total}] File name: {file_name}")
                    break
            # Fallback to mime type
            if file_name == f"file_{msg_id}" and hasattr(message.media.document, 'mime_type'):
                mime = message.media.document.mime_type
                print(f"[{idx}/{total}] MIME type: {mime}")
                if 'pdf' in mime:
                    file_name = f"document_{msg_id}.pdf"
                elif 'video' in mime:
                    ext = mime.split('/')[-1]
                    file_name = f"video_{msg_id}.{ext}"
        elif hasattr(message.media, 'photo'):
            file_name = f"photo_{msg_id}.jpg"
            print(f"[{idx}/{total}] Photo detected")
        else:
            print(f"[{idx}/{total}] Unknown media type: {type(message.media).__name__}")
        
        print(f"[{idx}/{total}] File name: {file_name}")
        
        progress = DownloadProgress(file_name, status_msg, idx, total)
        await safe_edit_message(status_msg, f"📥 [{idx}/{total}] Starting download...\n📄 `{file_name[:40]}`")
        
        file_path = None
        for attempt in range(CONNECTION_RETRIES):
            try:
                print(f"[{idx}/{total}] Download attempt {attempt + 1}/{CONNECTION_RETRIES}")
                file_path = await user_client.download_media(
                    message.media,
                    file=DOWNLOAD_DIR,
                    progress_callback=progress.update
                )
                if file_path:
                    print(f"[{idx}/{total}] Downloaded to: {file_path}")
                    break
            except Exception as e:
                print(f"[{idx}/{total}] Download error (attempt {attempt + 1}): {e}")
                if attempt == CONNECTION_RETRIES - 1:
                    await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Download failed: {str(e)[:30]}")
                    return {'success': False, 'error': str(e)[:50], 'file_name': file_name}
                await safe_edit_message(status_msg, f"⚠️ [{idx}/{total}] Retrying... ({attempt+2}/{CONNECTION_RETRIES})")
                await asyncio.sleep(2 ** attempt)
        
        if not file_path or not os.path.exists(file_path):
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Download failed")
            return {'success': False, 'error': 'Download failed', 'file_name': file_name}
        
        actual_file_name = os.path.basename(file_path)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        print(f"[{idx}/{total}] File downloaded: {actual_file_name} ({file_size_mb:.1f} MB)")
        
        await safe_edit_message(
            status_msg,
            f"☁️ [{idx}/{total}] Uploading to Drive...\n\n"
            f"📄 `{actual_file_name[:40]}`\n"
            f"📦 Size: {file_size_mb:.1f} MB"
        )
        
        upload_success = await upload_to_drive_async(file_path, actual_file_name)
        
        try:
            os.remove(file_path)
            print(f"[{idx}/{total}] File cleaned up")
        except Exception as e:
            print(f"[{idx}/{total}] Cleanup error: {e}")
        
        if upload_success:
            await safe_edit_message(
                status_msg,
                f"✅ [{idx}/{total}] **COMPLETED**\n\n"
                f"📄 `{actual_file_name[:40]}`\n"
                f"📦 {file_size_mb:.1f} MB\n"
                f"⏱️ {int(time.time() - progress.start_time)}s"
            )
            return {'success': True, 'file_name': actual_file_name, 'size': file_size_mb}
        else:
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Upload failed")
            return {'success': False, 'error': 'Upload failed', 'file_name': actual_file_name}
    
    except Exception as e:
        print(f"[{idx}/{total}] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        if status_msg:
            try:
                await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Error: {str(e)[:50]}")
            except:
                pass
        return {'success': False, 'error': str(e)[:50], 'file_name': file_name}

# ============ SEQUENTIAL BATCH PROCESSOR ============
async def process_sequential_batch(bot_client, user_client, chat_id, links, user_id):
    total = len(links)
    start_time = time.time()
    cancelled = False
    
    print(f"\n{'='*50}")
    print(f"Starting batch: {total} files for chat {chat_id}")
    print(f"{'='*50}\n")
    
    batch_msg = await bot_client.send_message(
        chat_id,
        f"📦 **BATCH QUEUED**\n\n"
        f"📊 Total files: {total}\n"
        f"🔄 Processing mode: Sequential\n"
        f"📍 Status: Starting...\n\n"
        f"💡 Use /clear to cancel"
    )
    
    results = []
    completed_count = 0
    failed_count = 0
    
    for idx, link in enumerate(links, 1):
        if user_id in user_queue and not user_queue[user_id]:
            cancelled = True
            print(f"\n❌ Batch cancelled at file {idx}/{total}")
            break
            
        print(f"\n--- Processing file {idx}/{total} ---")
        result = await process_single_file(bot_client, user_client, chat_id, link, idx, total, batch_msg, user_id)
        results.append(result)
        
        if result and result.get('success'):
            completed_count += 1
        else:
            failed_count += 1
        
        try:
            elapsed = int(time.time() - start_time)
            await batch_msg.edit(
                f"📦 **PROCESSING**\n\n"
                f"📊 Total: {total}\n"
                f"⏱️ Time: {elapsed//60}m {elapsed%60}s\n"
                f"Progress: {idx}/{total} ({(idx/total)*100:.0f}%)\n"
                f"✅ Done: {completed_count}\n"
                f"❌ Failed: {failed_count}\n"
                f"⏳ Left: {total - idx}"
            )
        except:
            pass
    
    total_time = int(time.time() - start_time)
    completed = [r for r in results if isinstance(r, dict) and r.get('success')]
    failed = [r for r in results if not isinstance(r, dict) or not r.get('success')]
    total_size = sum(r.get('size', 0) for r in completed)
    
    if cancelled:
        summary = f"🛑 **CANCELLED**\n\n"
    else:
        summary = f"🎉 **COMPLETED!**\n\n"
    
    summary += f"📊 Total: {total}\n"
    summary += f"✅ Success: {len(completed)}\n"
    summary += f"❌ Failed: {len(failed)}\n"
    summary += f"📦 Size: {total_size:.1f} MB\n"
    summary += f"⏱️ Time: {total_time//60}m {total_time%60}s\n"
    
    if completed:
        summary += f"\n**✅ Completed ({len(completed)}):**\n"
        for r in completed[:10]:
            summary += f"• `{r['file_name'][:35]}` ({r['size']:.1f}MB)\n"
        if len(completed) > 10:
            summary += f"• ... +{len(completed)-10} more\n"
    
    if failed:
        summary += f"\n**❌ Failed ({len(failed)}):**\n"
        for r in failed[:5]:
            if isinstance(r, dict):
                summary += f"• {r.get('file_name', 'Unknown')}: {r.get('error', '?')}\n"
        if len(failed) > 5:
            summary += f"• ... +{len(failed)-5} more\n"
    
    await safe_edit_message(batch_msg, summary)
    
    try:
        for file in os.listdir(DOWNLOAD_DIR):
            os.remove(os.path.join(DOWNLOAD_DIR, file))
    except:
        pass
    
    if user_id in user_queue:
        del user_queue[user_id]
    if user_id in user_tasks:
        del user_tasks[user_id]

# ============ MESSAGE HANDLER ============
async def handle_message(event, bot_client, user_client):
    user_id = event.sender_id
    chat_id = event.chat_id
    text = event.raw_text
    
    if user_id in user_queue and user_queue[user_id]:
        await event.respond("⚠️ Already processing! Wait for completion.")
        return
    
    try:
        user_queue[user_id] = True
        
        links = [line.strip() for line in text.split('\n') if 't.me/' in line]
        
        if not links:
            await event.respond(
                "❌ No valid links\n\n"
                "Formats:\n"
                "• t.me/channel/123\n"
                "• https://t.me/c/123/456"
            )
            del user_queue[user_id]
            return
        
        if len(links) == 1:
            await process_single_file(bot_client, user_client, chat_id, links[0], 1, 1, None, user_id)
            del user_queue[user_id]
        else:
            await event.respond(f"📝 Processing {len(links)} links sequentially...")
            task = asyncio.create_task(process_sequential_batch(bot_client, user_client, chat_id, links, user_id))
            user_tasks[user_id] = task
            await task
    
    except Exception as e:
        print(f"Handler error: {e}")
        await event.respond(f"❌ Error: {str(e)}")
        if user_id in user_queue:
            del user_queue[user_id]

# ============ MAIN ============
async def main():
    print("\n" + "="*70)
    print("🚀 STARTING BOT (FIXED)")
    print("="*70)
    
    if not os.path.exists(TOKEN_PICKLE):
        print("\n❌ token.pickle not found!")
        return
    
    bot = TelegramClient('bot', API_ID, API_HASH)
    user = TelegramClient('user', API_ID, API_HASH)
    
    try:
        await bot.start(bot_token=BOT_TOKEN)
        bot_me = await bot.get_me()
        print(f"✅ Bot: @{bot_me.username}")
        
        if os.path.exists('user.session'):
            await user.connect()
            if not await user.is_user_authorized():
                print("⚠️ Session expired")
                return
            await user.start(phone=PHONE)
        else:
            await user.start(phone=PHONE)
        
        user_me = await user.get_me()
        print(f"✅ User: {user_me.first_name}")
        
    except Exception as e:
        print(f"❌ Auth failed: {e}")
        return
    
    @bot.on(events.NewMessage(pattern='/start'))
    async def start_cmd(event):
        if event.is_private:
            await event.respond(
                "🚀 **FILE DOWNLOADER BOT** (FIXED)\n\n"
                "**Features:**\n"
                "• Downloads ANY media type (videos, PDFs, documents, photos)\n"
                "• Supports forum topics\n"
                "• Sequential processing\n"
                "• Progress tracking\n\n"
                "**Usage:**\n"
                "Send Telegram message links containing media\n\n"
                "**Commands:**\n"
                "/start - Help\n"
                "/list [link] - List all media in a topic\n"
                "/clear - Cancel batch\n"
                "/folder [ID] - Change Drive folder\n"
                "/stats - Statistics\n\n"
                "**Example:**\n"
                "`/list https://t.me/c/123/2/456` - Lists all media in topic 2"
            )
    
    @bot.on(events.NewMessage(pattern='/list'))
    async def list_cmd(event):
        if event.is_private:
            # Get the link from the message
            text = event.raw_text.strip()
            parts = text.split(maxsplit=1)
            
            if len(parts) < 2:
                await event.respond(
                    "📋 **List Media in Topic**\n\n"
                    "**Usage:**\n"
                    "`/list https://t.me/c/.../TOPIC_ID/MSG_ID`\n\n"
                    "This will show all media files in that topic with their links."
                )
                return
            
            link = parts[1].strip()
            channel_id, topic_id, msg_id, is_private = parse_link(link)
            
            if not channel_id or not topic_id:
                await event.respond("❌ Invalid link or not a topic link")
                return
            
            status_msg = await event.respond(f"🔍 Scanning topic {topic_id}...")
            
            try:
                # Get all messages from the topic
                messages_with_media = []
                
                if is_private:
                    await user.get_dialogs()
                    # Get messages from topic
                    async for msg in user.iter_messages(channel_id, reply_to=topic_id, limit=500):
                        if hasattr(msg, 'media') and msg.media:
                            # Get file name
                            file_name = "Unknown"
                            file_type = "Unknown"
                            
                            if hasattr(msg.media, 'document') and msg.media.document:
                                for attr in msg.media.document.attributes:
                                    if hasattr(attr, 'file_name') and attr.file_name:
                                        file_name = attr.file_name
                                        break
                                
                                if hasattr(msg.media.document, 'mime_type'):
                                    mime = msg.media.document.mime_type
                                    if 'pdf' in mime:
                                        file_type = "PDF"
                                    elif 'video' in mime:
                                        file_type = "Video"
                                    elif 'image' in mime:
                                        file_type = "Image"
                                    else:
                                        file_type = mime.split('/')[-1].upper()
                            elif hasattr(msg.media, 'photo'):
                                file_name = f"Photo_{msg.id}"
                                file_type = "Photo"
                            
                            messages_with_media.append({
                                'id': msg.id,
                                'name': file_name,
                                'type': file_type,
                                'link': f"https://t.me/c/{str(channel_id)[4:]}/{topic_id}/{msg.id}"
                            })
                
                if not messages_with_media:
                    await status_msg.edit("❌ No media found in this topic")
                    return
                
                # Update status
                await status_msg.edit(f"📋 Found {len(messages_with_media)} media files! Sending list...")
                
                # Split into chunks of 15 files per message to avoid message length limit
                chunk_size = 15
                for chunk_idx in range(0, len(messages_with_media), chunk_size):
                    chunk = messages_with_media[chunk_idx:chunk_idx + chunk_size]
                    
                    if chunk_idx == 0:
                        response = f"📋 **Topic {topic_id} - {len(messages_with_media)} files (Part 1)**\n\n"
                    else:
                        part_num = (chunk_idx // chunk_size) + 1
                        response = f"📋 **Topic {topic_id} (Part {part_num})**\n\n"
                    
                    for item in chunk:
                        idx = messages_with_media.index(item) + 1
                        response += f"**{idx}. [{item['type']}]** `{item['name'][:35]}`\n"
                        response += f"`{item['link']}`\n\n"
                    
                    if chunk_idx + chunk_size >= len(messages_with_media):
                        response += "💡 **Copy any link and send to download!**"
                    
                    await event.respond(response)
                    await asyncio.sleep(0.5)  # Small delay between messages
                
            except Exception as e:
                print(f"List error: {e}")
                import traceback
                traceback.print_exc()
                await status_msg.edit(f"❌ Error: {str(e)}")
    
    @bot.on(events.NewMessage(pattern='/clear'))
    async def clear_cmd(event):
        if event.is_private:
            user_id = event.sender_id
            if user_id not in user_queue or not user_queue[user_id]:
                await event.respond("ℹ️ No active downloads")
                return
            
            user_queue[user_id] = False
            try:
                cleaned = 0
                for file in os.listdir(DOWNLOAD_DIR):
                    os.remove(os.path.join(DOWNLOAD_DIR, file))
                    cleaned += 1
                await event.respond(f"🛑 Cancelled! Cleaned {cleaned} files")
                print(f"User {user_id} cleared queue")
            except Exception as e:
                await event.respond(f"⚠️ Error: {e}")
    
    @bot.on(events.NewMessage(pattern='/folder'))
    async def folder_cmd(event):
        if event.is_private:
            global DRIVE_FOLDER_ID
            text = event.raw_text.strip()
            
            if text == '/folder':
                await event.respond(
                    f"📁 **Current Folder**\n\n"
                    f"`{DRIVE_FOLDER_ID}`\n\n"
                    f"**Change:**\n"
                    f"`/folder YOUR_FOLDER_ID`"
                )
                return
            
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await event.respond("❌ Usage: `/folder YOUR_FOLDER_ID`")
                return
            
            new_id = parts[1].strip()
            if len(new_id) < 20:
                await event.respond("❌ Invalid folder ID")
                return
            
            try:
                with open(FOLDER_ID_FILE, 'w') as f:
                    f.write(new_id)
                old_id = DRIVE_FOLDER_ID
                DRIVE_FOLDER_ID = new_id
                await event.respond(f"✅ Updated!\n\nOld: `{old_id}`\nNew: `{new_id}`")
                print(f"Folder changed to: {new_id}")
            except Exception as e:
                await event.respond(f"❌ Failed: {e}")
    
    @bot.on(events.NewMessage(pattern='/stats'))
    async def stats_cmd(event):
        if event.is_private:
            await event.respond(
                f"📊 **Statistics**\n\n"
                f"🔄 Mode: Sequential\n"
                f"⏱️ Update: {PROGRESS_UPDATE_INTERVAL}s\n"
                f"🔁 Retries: {CONNECTION_RETRIES}\n"
                f"👥 Active: {len([v for v in user_queue.values() if v])}"
            )
    
    @bot.on(events.NewMessage)
    async def message_cmd(event):
        if event.is_private and not event.raw_text.startswith('/'):
            await handle_message(event, bot, user)
    
    print("✅ BOT RUNNING")
    print("⏳ Press Ctrl+C to stop\n")
    
    try:
        await bot.run_until_disconnected()
    except KeyboardInterrupt:
        print("\n👋 Stopping...")
    finally:
        await bot.disconnect()
        await user.disconnect()
        print("✅ Stopped")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
