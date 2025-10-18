#!/usr/bin/env python3
"""
Sequential Telegram Video Downloader Bot
Processes multiple links one after another - no timeouts
"""
import os
import asyncio
import time
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle

print("="*70)
print("🚀 TELEGRAM VIDEO DOWNLOADER BOT - SEQUENTIAL MODE")
print("="*70)

# ============ CONFIGURATION ============
API_ID = 5041713
API_HASH = '9c27474d00a8b8236307692d4b6f0434'
BOT_TOKEN = '8477203017:AAHarKtQkBdnfMR7DidTCd6tvE0ziAu0wFc'
PHONE = '+917540892472'

# Settings
DRIVE_FOLDER_ID = '1e1KS9b8iqNMMX4c3nlJvrUCaO2sO5ANO'
TOKEN_PICKLE = 'token.pickle'
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Performance settings
PROGRESS_UPDATE_INTERVAL = 5  # Increased to reduce API calls
CONNECTION_RETRIES = 3
BATCH_STATUS_UPDATE_INTERVAL = 10  # Only update batch status every 10 seconds
MESSAGE_DELAY = 0.5  # Delay between messages to avoid flood

# Track processing - allow queue
user_queue = {}
user_tasks = {}  # Store running tasks for each user
last_message_time = {}  # Track last message time per user

# ============ GOOGLE DRIVE ============
def get_drive_service():
    with open(TOKEN_PICKLE, 'rb') as token:
        creds = pickle.load(token)
    return build('drive', 'v3', credentials=creds)

async def upload_to_drive_async(file_path, file_name):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, upload_to_drive_sync, file_path, file_name)

def upload_to_drive_sync(file_path, file_name):
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
    """Send message with rate limiting"""
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
            # Extract wait time and wait
            import re
            match = re.search(r'(\d+) seconds', str(e))
            if match:
                wait = int(match.group(1))
                print(f"Rate limited, waiting {wait}s")
                await asyncio.sleep(wait + 1)
                return await bot_client.send_message(chat_id, text)
        raise

async def safe_edit_message(message, text):
    """Edit message with rate limiting"""
    try:
        return await message.edit(text)
    except Exception as e:
        if "wait" in str(e).lower():
            # Silently ignore rate limit on edits
            print(f"Rate limit on edit, skipping")
            return None
        raise

# ============ LINK PARSER ============
def parse_link(text):
    try:
        text = text.strip()
        if '/c/' in text:
            parts = text.split('/c/')[-1].split('/')
            channel_id = int('-100' + parts[0])
            msg_id = int(parts[1].split('?')[0])
        else:
            parts = text.split('t.me/')[-1].split('/')
            channel_id = parts[0].strip('@')
            msg_id = int(parts[1].split('?')[0])
        return channel_id, msg_id
    except Exception as e:
        print(f"Parse error: {e}")
        return None, None

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
        self.current_bytes = 0
        self.total_bytes = 0
        
    async def update(self, current, total):
        self.current_bytes = current
        self.total_bytes = total
        percent = (current / total) * 100 if total > 0 else 0
        now = time.time()
        
        # Less frequent updates to avoid rate limits
        if (now - self.last_update >= PROGRESS_UPDATE_INTERVAL or 
            percent >= 99.9 or 
            percent - self.last_percent >= 20):  # Only update every 20%
            
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
                    f"**ETA:** {eta_str}\n\n"
                )
            except Exception as e:
                print(f"Progress update error: {e}")

# ============ SINGLE FILE PROCESSOR ============
async def process_single_file(bot_client, user_client, chat_id, link_text, idx, total, batch_summary_msg=None, user_id=None):
    """Process a single file download"""
    file_name = "Unknown"
    status_msg = None
    
    try:
        # Send status message with rate limiting
        status_msg = await safe_send_message(bot_client, chat_id, f"📍 [{idx}/{total}] Initializing...", user_id)
        await asyncio.sleep(0.3)  # Small delay between operations
        
        # Parse link
        channel_id, msg_id = parse_link(link_text)
        if not channel_id:
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Invalid link format")
            return {'success': False, 'error': 'Invalid link', 'file_name': f'Link {idx}', 'link': link_text}
        
        print(f"[{idx}/{total}] Processing: channel={channel_id}, msg={msg_id}")
        
        await safe_edit_message(status_msg, f"🔍 [{idx}/{total}] Fetching message...")
        
        # Fetch message using user client
        try:
            message = await user_client.get_messages(channel_id, ids=msg_id)
            print(f"[{idx}/{total}] Message fetched: {type(message)}")
        except Exception as e:
            error_msg = str(e)
            print(f"[{idx}/{total}] Fetch error: {error_msg}")
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Access error: {error_msg[:30]}")
            return {'success': False, 'error': error_msg[:50], 'file_name': f'Link {idx}', 'link': link_text}
        
        if not message or not message.media:
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] No media found")
            return {'success': False, 'error': 'No media', 'file_name': f'Link {idx}', 'link': link_text}
        
        # Get file name
        file_name = f"video_{msg_id}"
        if hasattr(message.media, 'document'):
            for attr in message.media.document.attributes:
                if hasattr(attr, 'file_name'):
                    file_name = attr.file_name
                    break
        
        print(f"[{idx}/{total}] File name: {file_name}")
        
        # DON'T update batch summary - causes too many edits
        # Just update status message
        
        # Start download
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
                    await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Download failed\n📄 `{file_name}`\nError: {str(e)[:30]}")
                    return {'success': False, 'error': str(e)[:50], 'file_name': file_name, 'link': link_text}
                await safe_edit_message(status_msg, f"⚠️ [{idx}/{total}] Retrying... ({attempt+2}/{CONNECTION_RETRIES})")
                await asyncio.sleep(2 ** attempt)
        
        if not file_path or not os.path.exists(file_path):
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Download failed\n📄 `{file_name}`")
            return {'success': False, 'error': 'Download failed', 'file_name': file_name, 'link': link_text}
        
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
        
        # Cleanup
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
            await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Upload failed\n📄 `{actual_file_name}`")
            return {'success': False, 'error': 'Upload failed', 'file_name': actual_file_name}
    
    except Exception as e:
        print(f"[{idx}/{total}] Unexpected error: {e}")
        if status_msg:
            try:
                await safe_edit_message(status_msg, f"❌ [{idx}/{total}] Error: {str(e)[:50]}")
            except:
                pass
        return {'success': False, 'error': str(e)[:50], 'file_name': file_name}

# ============ SEQUENTIAL BATCH PROCESSOR ============
async def process_sequential_batch(bot_client, user_client, chat_id, links, user_id):
    """Process multiple links sequentially"""
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
        f"🔄 Processing mode: Sequential (one by one)\n"
        f"📍 Status: Starting...\n\n"
        f"⏳ All files will be processed automatically\n"
        f"💡 Use /clear to cancel this batch"
    )
    
    results = []
    completed_count = 0
    failed_count = 0
    
    # Process each link one by one
    for idx, link in enumerate(links, 1):
        # Check if cancelled
        if user_id in user_queue and not user_queue[user_id]:
            cancelled = True
            print(f"\n❌ Batch cancelled by user at file {idx}/{total}")
            break
            
        print(f"\n--- Processing file {idx}/{total} ---")
        result = await process_single_file(bot_client, user_client, chat_id, link, idx, total, batch_msg)
        results.append(result)
        
        if result and result.get('success'):
            completed_count += 1
            print(f"✅ File {idx}/{total} completed")
        else:
            failed_count += 1
            print(f"❌ File {idx}/{total} failed: {result.get('error', 'Unknown')}")
        
        # Update batch status after each file
        try:
            elapsed = int(time.time() - start_time)
            await batch_msg.edit(
                f"📦 **PROCESSING QUEUE**\n\n"
                f"📊 Total: {total} files\n"
                f"⏱️ Elapsed: {elapsed//60}m {elapsed%60}s\n\n"
                f"Progress: {idx}/{total} ({(idx/total)*100:.1f}%)\n"
                f"✅ Completed: {completed_count}\n"
                f"❌ Failed: {failed_count}\n"
                f"⏳ Remaining: {total - idx}"
            )
        except Exception as e:
            print(f"Batch status update error: {e}")
    
    # Final summary
    total_time = int(time.time() - start_time)
    completed = [r for r in results if isinstance(r, dict) and r.get('success')]
    failed = [r for r in results if not isinstance(r, dict) or not r.get('success')]
    total_size = sum(r.get('size', 0) for r in completed)
    
    if cancelled:
        summary = f"🛑 **BATCH CANCELLED**\n\n"
        summary += f"📊 **Statistics:**\n"
        summary += f"Total files: {total}\n"
        summary += f"✅ Completed before cancel: {len(completed)}\n"
        summary += f"❌ Failed: {len(failed)}\n"
        summary += f"⏭️ Skipped: {total - len(results)}\n"
        summary += f"📦 Downloaded: {total_size:.1f} MB\n"
        summary += f"⏱️ Time: {total_time//60}m {total_time%60}s\n"
    else:
        summary = f"🎉 **ALL FILES PROCESSED!**\n\n"
        summary += f"📊 **Statistics:**\n"
        summary += f"Total files: {total}\n"
        summary += f"✅ Successful: {len(completed)}\n"
        summary += f"❌ Failed: {len(failed)}\n"
        summary += f"📦 Total size: {total_size:.1f} MB\n"
        summary += f"⏱️ Total time: {total_time//60}m {total_time%60}s\n"
        if total_size > 0 and total_time > 0:
            summary += f"⚡ Avg speed: {total_size/total_time:.2f} MB/s\n"
        summary += "\n"
    
    if completed:
        summary += f"**✅ Completed Files ({len(completed)}):**\n"
        for r in completed[:15]:
            summary += f"• `{r['file_name'][:40]}` ({r['size']:.1f} MB)\n"
        if len(completed) > 15:
            summary += f"• ... and {len(completed)-15} more\n"
    
    if failed:
        summary += f"\n**❌ Failed ({len(failed)}):**\n"
        for i, r in enumerate(failed[:10], 1):
            if isinstance(r, dict):
                summary += f"• {r.get('file_name', f'File {i}')}: {r.get('error', 'Unknown')}\n"
        if len(failed) > 10:
            summary += f"• ... and {len(failed)-10} more\n"
    
    await safe_edit_message(batch_msg, summary)
    
    # Cleanup downloads directory
    try:
        for file in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, file)
            if os.path.isfile(file_path):
                os.remove(file_path)
        print("✅ Downloads directory cleaned")
    except Exception as e:
        print(f"Cleanup error: {e}")
    
    # Remove from queue and tasks
    if user_id in user_queue:
        del user_queue[user_id]
    if user_id in user_tasks:
        del user_tasks[user_id]
    
    print(f"\n{'='*50}")
    if cancelled:
        print(f"Batch cancelled: {len(completed)} completed, {total - len(results)} skipped")
    else:
        print(f"Batch completed: {len(completed)} success, {len(failed)} failed")
    print(f"{'='*50}\n")

# ============ MESSAGE HANDLER ============
async def handle_message(event, bot_client, user_client):
    """Handle incoming messages"""
    user_id = event.sender_id
    chat_id = event.chat_id
    text = event.raw_text
    
    print(f"\nReceived message from user {user_id} in chat {chat_id}")
    
    # Check if user already has a queue
    if user_id in user_queue and user_queue[user_id]:
        await event.respond(
            "⚠️ **Already processing your files!**\n\n"
            "Your links are being processed one by one.\n"
            "Please wait for completion before sending more.\n\n"
            "💡 You can send multiple links at once - they'll be processed sequentially."
        )
        return
    
    try:
        user_queue[user_id] = True
        
        # Extract links
        links = []
        for line in text.split('\n'):
            line = line.strip()
            if 't.me/' in line or 'telegram.me/' in line:
                links.append(line)
        
        print(f"Found {len(links)} links")
        
        if not links:
            await event.respond(
                "❌ No valid Telegram links found\n\n"
                "**Supported formats:**\n"
                "• t.me/channel/123\n"
                "• https://t.me/c/123/456\n\n"
                "💡 **Tip:** Send multiple links at once (one per line)\n"
                "They'll be processed one after another automatically!"
            )
            if user_id in user_queue:
                del user_queue[user_id]
            return
        
        if len(links) == 1:
            print("Processing single link")
            result = await process_single_file(bot_client, user_client, chat_id, links[0], 1, 1, None, user_id)
            if user_id in user_queue:
                del user_queue[user_id]
        else:
            print(f"Processing batch of {len(links)} links")
            await event.respond(
                f"📝 **Received {len(links)} links**\n\n"
                f"🔄 Processing sequentially (one by one)\n"
                f"⏳ No need to wait - all will be processed automatically\n\n"
                f"You'll get individual updates for each file!\n"
                f"💡 Use /clear to cancel anytime"
            )
            # Store task for cancellation
            task = asyncio.create_task(process_sequential_batch(bot_client, user_client, chat_id, links, user_id))
            user_tasks[user_id] = task
            await task
    
    except Exception as e:
        print(f"Handler error: {e}")
        await event.respond(f"❌ Unexpected error: {str(e)}")
        if user_id in user_queue:
            del user_queue[user_id]

# ============ MAIN ============
async def main():
    print("\n" + "="*70)
    print("🚀 STARTING BOT - SEQUENTIAL MODE")
    print("="*70)
    
    if not os.path.exists(TOKEN_PICKLE):
        print("\n❌ token.pickle not found!")
        return
    
    # Initialize clients
    print("\n📱 Creating bot client...")
    bot = TelegramClient('bot', API_ID, API_HASH)
    
    print("👤 Creating user client...")
    user = TelegramClient('user', API_ID, API_HASH)
    
    try:
        print("\n🔐 Starting bot authentication...")
        await bot.start(bot_token=BOT_TOKEN)
        bot_me = await bot.get_me()
        print(f"   ✅ Bot: @{bot_me.username}")
        
        print("\n🔐 Starting user authentication...")
        
        if os.path.exists('user.session'):
            print("   📂 Session file found, connecting...")
            await user.connect()
            
            if not await user.is_user_authorized():
                print("   ⚠️  Session expired or invalid")
                return
            else:
                print("   ✅ Session valid, authorizing...")
                await user.start(phone=PHONE)
        else:
            print("   ⚠️  No session file found")
            await user.start(phone=PHONE)
        
        user_me = await user.get_me()
        print(f"   ✅ User: {user_me.first_name}")
        
    except Exception as e:
        print(f"\n❌ Authentication failed: {e}")
        return
    
    # Register handlers
    @bot.on(events.NewMessage(pattern='/start'))
    async def start_cmd(event):
        if event.is_private:
            await event.respond(
                "🚀 **SEQUENTIAL VIDEO DOWNLOADER BOT**\n\n"
                "**⚡ Features:**\n"
                "• Sequential processing (one by one)\n"
                "• No timeout limits\n"
                "• Send multiple links at once\n"
                "• Real-time progress for each file\n"
                "• Download speed & ETA tracking\n"
                "• Automatic retry on failures\n\n"
                "**📋 How to use:**\n"
                "1. Send one or more Telegram video links\n"
                "   (You can paste multiple links at once)\n"
                "2. Files will be processed one after another\n"
                "3. Each file gets its own progress tracker\n"
                "4. Get detailed completion summary\n\n"
                "**📗 Supported formats:**\n"
                "• t.me/channel/123\n"
                "• https://t.me/c/123/456\n\n"
                "**💡 Pro tip:**\n"
                "Send all your links at once! The bot will queue them\n"
                "and process each one automatically. No need to wait!\n\n"
                "**🔧 Commands:**\n"
                "/start - Show this help\n"
                "/stats - Show bot statistics\n"
                "/clear - Cancel current batch and clear queue"
            )
    
    @bot.on(events.NewMessage(pattern='/clear'))
    async def clear_cmd(event):
        if event.is_private:
            user_id = event.sender_id
            
            if user_id not in user_queue or not user_queue[user_id]:
                await event.respond("ℹ️ No active downloads to clear")
                return
            
            # Mark as cancelled
            user_queue[user_id] = False
            
            # Clean up downloads directory
            try:
                cleaned = 0
                for file in os.listdir(DOWNLOAD_DIR):
                    file_path = os.path.join(DOWNLOAD_DIR, file)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        cleaned += 1
                
                await event.respond(
                    f"🛑 **CLEARING QUEUE**\n\n"
                    f"✅ Cancellation signal sent\n"
                    f"🧹 Cleaned {cleaned} pending files\n\n"
                    f"Current download will complete, then batch will stop.\n"
                    f"You can send new links now!"
                )
                print(f"User {user_id} cleared queue - cleaned {cleaned} files")
            except Exception as e:
                await event.respond(f"⚠️ Queue cleared but cleanup error: {str(e)}")
                print(f"Clear error: {e}")
    
    @bot.on(events.NewMessage(pattern='/stats'))
    async def stats_cmd(event):
        if event.is_private:
            await event.respond(
                f"📊 **Bot Statistics**\n\n"
                f"🔄 Processing mode: Sequential (one by one)\n"
                f"📡 Connection retries: {CONNECTION_RETRIES}\n"
                f"⏱️ Progress update interval: {PROGRESS_UPDATE_INTERVAL}s\n"
                f"⏰ No timeout limits\n\n"
                f"🤖 Status: ✅ Running\n"
                f"👥 Active queues: {len([v for v in user_queue.values() if v])}"
            )
    
    @bot.on(events.NewMessage)
    async def message_cmd(event):
        if event.is_private and not event.raw_text.startswith('/'):
            await handle_message(event, bot, user)
    
    print("\n" + "="*70)
    print("✅ BOT IS RUNNING!")
    print("="*70)
    print(f"\n📱 Bot: @{bot_me.username}")
    print(f"👤 User: {user_me.first_name}")
    print(f"🔄 Mode: Sequential (one by one)")
    print(f"⏰ No timeout limits")
    print(f"\n⏳ Press Ctrl+C to stop\n")
    
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
