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
API_ID = 5041713  # Changed from string to integer
API_HASH = '9c27474d00a8b8236307692d4b6f0434'
BOT_TOKEN = '8477203017:AAHarKtQkBdnfMR7DidTCd6tvE0ziAu0wFc'
PHONE = '+917540892472'

# Settings
DRIVE_FOLDER_ID = '1e1KS9b8iqNMMX4c3nlJvrUCaO2sO5ANO'
TOKEN_PICKLE = 'token.pickle'
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Performance settings
PROGRESS_UPDATE_INTERVAL = 3
CONNECTION_RETRIES = 3

# Track processing - allow queue
user_queue = {}

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
    except:
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
        
        if (now - self.last_update >= PROGRESS_UPDATE_INTERVAL or 
            percent >= 99.9 or 
            percent - self.last_percent >= 10):
            
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
                await self.message.edit(
                    f"📥 **Downloading [{self.idx}/{self.total}]**\n\n"
                    f"📄 `{self.file_name[:40]}{'...' if len(self.file_name) > 40 else ''}`\n\n"
                    f"**Progress:** {percent:.1f}%\n"
                    f"**Downloaded:** {current/(1024*1024):.1f} MB / {total/(1024*1024):.1f} MB\n"
                    f"**Speed:** {speed:.2f} MB/s\n"
                    f"**ETA:** {eta_str}\n\n"
                )
            except:
                pass

# ============ SINGLE FILE PROCESSOR ============
async def process_single_file(event, user_client, link_text, idx, total, batch_summary_msg=None):
    file_name = "Unknown"
    status_msg = None
    
    try:
        status_msg = await event.respond(f"📍 [{idx}/{total}] Initializing...")
        
        channel_id, msg_id = parse_link(link_text)
        if not channel_id:
            await status_msg.edit(f"❌ [{idx}/{total}] Invalid link format")
            return {'success': False, 'error': 'Invalid link', 'file_name': f'Link {idx}', 'link': link_text}
        
        await status_msg.edit(f"🔍 [{idx}/{total}] Fetching message...")
        try:
            message = await user_client.get_messages(channel_id, ids=msg_id)
        except Exception as e:
            error_msg = str(e)
            await status_msg.edit(f"❌ [{idx}/{total}] Error: {error_msg[:30]}")
            return {'success': False, 'error': error_msg[:50], 'file_name': f'Link {idx}', 'link': link_text}
        
        if not message or not message.media:
            await status_msg.edit(f"❌ [{idx}/{total}] No media found")
            return {'success': False, 'error': 'No media', 'file_name': f'Link {idx}', 'link': link_text}
        
        file_name = f"video_{msg_id}"
        if hasattr(message.media, 'document'):
            for attr in message.media.document.attributes:
                if hasattr(attr, 'file_name'):
                    file_name = attr.file_name
                    break
        
        # Update batch summary if exists
        if batch_summary_msg:
            try:
                await batch_summary_msg.edit(
                    f"📦 **PROCESSING QUEUE**\n\n"
                    f"🔄 Currently downloading: [{idx}/{total}]\n"
                    f"📄 `{file_name[:40]}`\n\n"
                    f"⏳ Remaining: {total - idx} files"
                )
            except:
                pass
        
        progress = DownloadProgress(file_name, status_msg, idx, total)
        await status_msg.edit(f"📥 [{idx}/{total}] Starting download...\n📄 `{file_name[:40]}`")
        
        file_path = None
        for attempt in range(CONNECTION_RETRIES):
            try:
                file_path = await user_client.download_media(
                    message.media,
                    file=DOWNLOAD_DIR,
                    progress_callback=progress.update
                )
                if file_path:
                    break
            except Exception as e:
                if attempt == CONNECTION_RETRIES - 1:
                    await status_msg.edit(f"❌ [{idx}/{total}] Download failed\n📄 `{file_name}`\nError: {str(e)[:30]}")
                    return {'success': False, 'error': str(e)[:50], 'file_name': file_name, 'link': link_text}
                await status_msg.edit(f"⚠️ [{idx}/{total}] Retrying... ({attempt+2}/{CONNECTION_RETRIES})")
                await asyncio.sleep(2 ** attempt)
        
        if not file_path:
            await status_msg.edit(f"❌ [{idx}/{total}] Download failed\n📄 `{file_name}`")
            return {'success': False, 'error': 'Download failed', 'file_name': file_name, 'link': link_text}
        
        actual_file_name = os.path.basename(file_path)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        await status_msg.edit(
            f"☁️ [{idx}/{total}] Uploading to Drive...\n\n"
            f"📄 `{actual_file_name[:40]}`\n"
            f"📦 Size: {file_size_mb:.1f} MB"
        )
        
        upload_success = await upload_to_drive_async(file_path, actual_file_name)
        
        try:
            os.remove(file_path)
        except:
            pass
        
        if upload_success:
            await status_msg.edit(
                f"✅ [{idx}/{total}] **COMPLETED**\n\n"
                f"📄 `{actual_file_name[:40]}`\n"
                f"📦 {file_size_mb:.1f} MB\n"
                f"⏱️ {int(time.time() - progress.start_time)}s"
            )
            return {'success': True, 'file_name': actual_file_name, 'size': file_size_mb}
        else:
            await status_msg.edit(f"❌ [{idx}/{total}] Upload failed\n📄 `{actual_file_name}`")
            return {'success': False, 'error': 'Upload failed', 'file_name': actual_file_name}
    
    except Exception as e:
        if status_msg:
            try:
                await status_msg.edit(f"❌ [{idx}/{total}] Error: {str(e)[:50]}")
            except:
                pass
        return {'success': False, 'error': str(e)[:50], 'file_name': file_name}

# ============ SEQUENTIAL BATCH PROCESSOR ============
async def process_sequential_batch(event, user_client, links):
    user_id = event.sender_id
    total = len(links)
    start_time = time.time()
    
    batch_msg = await event.respond(
        f"📦 **BATCH QUEUED**\n\n"
        f"📊 Total files: {total}\n"
        f"🔄 Processing mode: Sequential (one by one)\n"
        f"📍 Status: Starting...\n\n"
        f"⏳ All files will be processed automatically"
    )
    
    results = []
    completed_count = 0
    failed_count = 0
    
    # Process each link one by one
    for idx, link in enumerate(links, 1):
        result = await process_single_file(event, user_client, link, idx, total, batch_msg)
        results.append(result)
        
        if result and result.get('success'):
            completed_count += 1
        else:
            failed_count += 1
        
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
        except:
            pass
    
    # Final summary
    total_time = int(time.time() - start_time)
    completed = [r for r in results if isinstance(r, dict) and r.get('success')]
    failed = [r for r in results if not isinstance(r, dict) or not r.get('success')]
    total_size = sum(r.get('size', 0) for r in completed)
    
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
    
    await batch_msg.edit(summary)
    
    if user_id in user_queue:
        del user_queue[user_id]

# ============ MESSAGE HANDLER ============
async def handle_message(event, user_client):
    user_id = event.sender_id
    text = event.raw_text
    
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
        
        links = []
        for line in text.split('\n'):
            line = line.strip()
            if 't.me/' in line or 'telegram.me/' in line:
                links.append(line)
        
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
            result = await process_single_file(event, user_client, links[0], 1, 1)
            if user_id in user_queue:
                del user_queue[user_id]
        else:
            await event.respond(
                f"📝 **Received {len(links)} links**\n\n"
                f"🔄 Processing sequentially (one by one)\n"
                f"⏳ No need to wait - all will be processed automatically\n\n"
                f"You'll get individual updates for each file!"
            )
            await process_sequential_batch(event, user_client, links)
    
    except Exception as e:
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
    
    # Initialize clients with proper types
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
        
        # Check if session exists
        if os.path.exists('user.session'):
            print("   📂 Session file found, attempting to connect...")
            await user.connect()
            
            if not await user.is_user_authorized():
                print("   ⚠️  Session expired or invalid")
                print("   🔄 Please delete user.session and regenerate it")
                return
            else:
                print("   ✅ Session valid, authorizing...")
                # Authorize using the session
                await user.start(phone=PHONE)
        else:
            print("   ⚠️  No session file found")
            print("   🔄 Starting interactive login...")
            await user.start(phone=PHONE)
        
        user_me = await user.get_me()
        print(f"   ✅ User: {user_me.first_name} (@{user_me.username or 'No username'})")
        
    except Exception as e:
        print(f"\n❌ Authentication failed: {e}")
        print("\n🔧 Troubleshooting steps:")
        print("1. Delete user.session file if it exists")
        print("2. Make sure API_ID and API_HASH are correct")
        print("3. Check phone number format (+917540892472)")
        print("4. Regenerate session file if needed")
        return
    
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
                "/stats - Show bot statistics"
            )
    
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
            await handle_message(event, user)
    
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
