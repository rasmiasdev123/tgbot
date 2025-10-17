#!/usr/bin/env python3
"""
Optimized Telegram Video Downloader Bot - NO INPUT REQUIRED
Uses pre-generated session file
"""
import os
import asyncio
import time
from telethon import TelegramClient, events
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle

print("="*70)
print("🚀 TELEGRAM VIDEO DOWNLOADER BOT - NO INPUT MODE")
print("="*70)

# ============ CONFIGURATION ============
API_ID = '5041713'
API_HASH = '9c27474d00a8b8236307692d4b6f0434'
BOT_TOKEN = '8477203017:AAHarKtQkBdnfMR7DidTCd6tvE0ziAu0wFc'
PHONE = '+917540892472'

# Settings
DRIVE_FOLDER_ID = '1e1KS9b8iqNMMX4c3nlJvrUCaO2sO5ANO'
TOKEN_PICKLE = 'token.pickle'
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Performance settings
MAX_PARALLEL_DOWNLOADS = 5
PROGRESS_UPDATE_INTERVAL = 3
BATCH_STATUS_UPDATE_INTERVAL = 2
CONNECTION_RETRIES = 3
CHUNK_SIZE = 1024 * 1024

# Track processing
user_processing = {}

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
        status_msg = await event.respond(f"🔄 [{idx}/{total}] Initializing...")
        
        channel_id, msg_id = parse_link(link_text)
        if not channel_id:
            await status_msg.edit(f"❌ [{idx}/{total}] Invalid link format")
            return {'success': False, 'error': 'Invalid link', 'file_name': f'Link {idx}', 'link': link_text}
        
        await status_msg.edit(f"🔍 [{idx}/{total}] Fetching message...")
        try:
            message = await asyncio.wait_for(
                user_client.get_messages(channel_id, ids=msg_id),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            await status_msg.edit(f"⏱️ [{idx}/{total}] Timeout fetching message")
            return {'success': False, 'error': 'Fetch timeout', 'file_name': f'Link {idx}', 'link': link_text}
        except Exception as e:
            await status_msg.edit(f"❌ [{idx}/{total}] Access denied or invalid link")
            return {'success': False, 'error': str(e)[:50], 'file_name': f'Link {idx}', 'link': link_text}
        
        if not message or not message.media:
            await status_msg.edit(f"❌ [{idx}/{total}] No media found")
            return {'success': False, 'error': 'No media', 'file_name': f'Link {idx}', 'link': link_text}
        
        file_name = f"video_{msg_id}"
        if hasattr(message.media, 'document'):
            for attr in message.media.document.attributes:
                if hasattr(attr, 'file_name'):
                    file_name = attr.file_name
                    break
        
        progress = DownloadProgress(file_name, status_msg, idx, total)
        await status_msg.edit(f"📥 [{idx}/{total}] Starting download...\n📄 `{file_name[:40]}`")
        
        file_path = None
        for attempt in range(CONNECTION_RETRIES):
            try:
                file_path = await asyncio.wait_for(
                    user_client.download_media(
                        message.media,
                        file=DOWNLOAD_DIR,
                        progress_callback=progress.update
                    ),
                    timeout=6000.0
                )
                if file_path:
                    break
            except asyncio.TimeoutError:
                if attempt == CONNECTION_RETRIES - 1:
                    await status_msg.edit(f"⏱️ [{idx}/{total}] Download timeout\n📄 `{file_name}`")
                    return {'success': False, 'error': 'Download timeout', 'file_name': file_name, 'link': link_text}
                await status_msg.edit(f"⚠️ [{idx}/{total}] Timeout, retrying... ({attempt+2}/{CONNECTION_RETRIES})")
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                if attempt == CONNECTION_RETRIES - 1:
                    await status_msg.edit(f"❌ [{idx}/{total}] Download failed\n📄 `{file_name}`")
                    return {'success': False, 'error': str(e)[:50], 'file_name': file_name, 'link': link_text}
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

# ============ BATCH PROCESSOR ============
async def process_batch(event, user_client, links):
    user_id = event.sender_id
    total = len(links)
    start_time = time.time()
    
    batch_msg = await event.respond(
        f"📦 **BATCH PROCESSING STARTED**\n\n"
        f"📊 Total files: {total}\n"
        f"⚡ Parallel downloads: {MAX_PARALLEL_DOWNLOADS}\n"
        f"🔄 Status: Initializing...\n\n"
        f"Progress: 0/{total} (0%)\n"
        f"✅ Completed: 0\n"
        f"❌ Failed: 0\n"
        f"⏳ Processing: 0"
    )
    
    semaphore = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
    completed_count = 0
    failed_count = 0
    processing_count = 0
    
    async def process_with_semaphore(link, idx):
        nonlocal completed_count, failed_count, processing_count
        async with semaphore:
            processing_count += 1
            result = await process_single_file(event, user_client, link, idx, total, batch_msg)
            processing_count -= 1
            if result and result.get('success'):
                completed_count += 1
            else:
                failed_count += 1
            return result
    
    tasks = [process_with_semaphore(link, idx) for idx, link in enumerate(links, 1)]
    
    async def update_batch_status():
        while completed_count + failed_count < total:
            await asyncio.sleep(BATCH_STATUS_UPDATE_INTERVAL)
            progress_percent = ((completed_count + failed_count) / total) * 100
            elapsed = int(time.time() - start_time)
            try:
                await batch_msg.edit(
                    f"📦 **BATCH PROCESSING**\n\n"
                    f"📊 Total: {total} files\n"
                    f"⚡ Parallel: {MAX_PARALLEL_DOWNLOADS}\n"
                    f"⏱️ Elapsed: {elapsed//60}m {elapsed%60}s\n\n"
                    f"Progress: {completed_count + failed_count}/{total} ({progress_percent:.1f}%)\n"
                    f"✅ Completed: {completed_count}\n"
                    f"❌ Failed: {failed_count}\n"
                    f"⏳ Processing: {processing_count}"
                )
            except:
                pass
    
    status_task = asyncio.create_task(update_batch_status())
    results = await asyncio.gather(*tasks, return_exceptions=True)
    status_task.cancel()
    
    total_time = int(time.time() - start_time)
    completed = [r for r in results if isinstance(r, dict) and r.get('success')]
    failed = [r for r in results if not isinstance(r, dict) or not r.get('success')]
    total_size = sum(r.get('size', 0) for r in completed)
    
    summary = f"🎉 **BATCH COMPLETED!**\n\n"
    summary += f"📊 **Statistics:**\n"
    summary += f"Total files: {total}\n"
    summary += f"✅ Successful: {len(completed)}\n"
    summary += f"❌ Failed: {len(failed)}\n"
    summary += f"📦 Total size: {total_size:.1f} MB\n"
    summary += f"⏱️ Total time: {total_time//60}m {total_time%60}s\n"
    summary += f"⚡ Avg speed: {total_size/total_time if total_time > 0 else 0:.2f} MB/s\n\n"
    
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
    
    if user_id in user_processing:
        user_processing[user_id] = False

# ============ MESSAGE HANDLER ============
async def handle_message(event, user_client):
    user_id = event.sender_id
    text = event.raw_text
    
    if user_processing.get(user_id, False):
        await event.respond("⏳ Already processing your request. Please wait...")
        return
    
    try:
        user_processing[user_id] = True
        
        links = []
        for line in text.split('\n'):
            line = line.strip()
            if 't.me/' in line or 'telegram.me/' in line:
                links.append(line)
        
        if not links:
            await event.respond("❌ No valid Telegram links found\n\nSupported formats:\n• t.me/channel/123\n• https://t.me/c/123/456")
            return
        
        if len(links) == 1:
            await process_single_file(event, user_client, links[0], 1, 1)
        else:
            await process_batch(event, user_client, links)
    
    except Exception as e:
        await event.respond(f"❌ Unexpected error: {str(e)}")
    
    finally:
        user_processing[user_id] = False

# ============ MAIN - NO INPUT REQUIRED ============
async def main():
    print("\n" + "="*70)
    print("🚀 STARTING BOT - NO INPUT MODE")
    print("="*70)
    
    # Check for session file
    if not os.path.exists('user.session'):
        print("\n❌ ERROR: user.session file not found!")
        print("\n📝 To create session file:")
        print("1. Run the session generator script locally")
        print("2. Complete the phone verification")
        print("3. Upload the generated 'user.session' file to server")
        return
    
    if not os.path.exists(TOKEN_PICKLE):
        print("\n❌ token.pickle not found!")
        return
    
    # Create clients - will use existing session
    bot = TelegramClient('bot', API_ID, API_HASH)
    user = TelegramClient('user', API_ID, API_HASH)
    
    print("\n📱 Starting bot...")
    await bot.start(bot_token=BOT_TOKEN)
    bot_me = await bot.get_me()
    print(f"   ✅ Bot: @{bot_me.username}")
    
    print("\n👤 Starting user client with session file...")
    # This will use the session file automatically - NO INPUT NEEDED
    await user.start()
    user_me = await user.get_me()
    print(f"   ✅ User: {user_me.first_name}")
    
    # Register handlers
    @bot.on(events.NewMessage(pattern='/start'))
    async def start_cmd(event):
        if event.is_private:
            await event.respond(
                "🚀 **OPTIMIZED VIDEO DOWNLOADER BOT**\n\n"
                "**⚡ Features:**\n"
                f"• Parallel downloads: {MAX_PARALLEL_DOWNLOADS} files at once\n"
                "• Real-time progress for each file\n"
                "• Download speed & ETA tracking\n"
                "• Automatic retry on failures\n"
                "• Batch summary statistics\n\n"
                "**📋 How to use:**\n"
                "1. Send one or more Telegram video links\n"
                "2. Each file gets its own progress tracker\n"
                "3. Files download in parallel for speed\n"
                "4. Get detailed completion summary\n\n"
                "**🔗 Supported formats:**\n"
                "• t.me/channel/123\n"
                "• https://t.me/c/123/456\n\n"
                "**💡 Commands:**\n"
                "/start - Show this help\n"
                "/stats - Show bot statistics"
            )
    
    @bot.on(events.NewMessage(pattern='/stats'))
    async def stats_cmd(event):
        if event.is_private:
            await event.respond(
                f"📊 **Bot Statistics**\n\n"
                f"⚡ Max parallel downloads: {MAX_PARALLEL_DOWNLOADS}\n"
                f"🔄 Connection retries: {CONNECTION_RETRIES}\n"
                f"📡 Progress update interval: {PROGRESS_UPDATE_INTERVAL}s\n"
                f"⏱️ Batch status update: {BATCH_STATUS_UPDATE_INTERVAL}s\n\n"
                f"🤖 Status: ✅ Running\n"
                f"👥 Active users: {len([v for v in user_processing.values() if v])}"
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
    print(f"⚡ Parallel downloads: {MAX_PARALLEL_DOWNLOADS}")
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
