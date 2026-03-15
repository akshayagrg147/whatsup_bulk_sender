import time
import random
import requests
import logging
import datetime
import base64
import os
import pytz
from config import *
from database import log_message, get_db_connection, is_opted_out

logger = logging.getLogger(__name__)

def get_ist_now():
    tz = pytz.timezone(TIMEZONE)
    return datetime.datetime.now(tz)

def can_send_now():
    now = get_ist_now()
    hour = now.hour
    if BUSINESS_START_HOUR <= hour < BUSINESS_END_HOUR:
        return True
    return False

def wait_for_business_hours():
    while not can_send_now():
        now = get_ist_now()
        logger.info(f"Outside business hours ({BUSINESS_START_HOUR}-{BUSINESS_END_HOUR}). Waiting...")
        time.sleep(600)  # Check every 10 mins

def get_daily_sent_count():
    conn = get_db_connection()
    try:
        today = get_ist_now().strftime("%Y-%m-%d")
        cur = conn.cursor()
        cur.execute("SELECT total_sent FROM daily_stats WHERE date=?", (today,))
        row = cur.fetchone()
        return row['total_sent'] if row else 0
    finally:
        conn.close()

def send_whatsapp_message(phone, text):
    """Send a plain text WhatsApp message."""
    url = f"{EVOLUTION_BASE_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": phone,
        "options": {
            "delay": random.randint(1000, 3000),
            "presence": "composing"
        },
        "textMessage": {
            "text": text
        }
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        return True, response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send message to {phone}: {e}")
        return False, str(e)


def send_whatsapp_media(phone, media_path, caption="", media_type="video"):
    """
    Send a media file (video, image, audio, document) via WhatsApp.

    Args:
        phone (str):       Recipient phone number with country code.
        media_path (str):  Absolute path to the local file OR a public URL.
        caption (str):     Optional caption text shown below the media.
        media_type (str):  One of: 'video', 'image', 'audio', 'document'

    Returns:
        (bool, dict|str)
    """
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    # Use absolute path for local files
    if not media_path.startswith("http://") and not media_path.startswith("https://"):
        media_path = os.path.abspath(media_path)

    # Determine if it's a URL or a local file — we only use local file (base64), no URL
    if media_path.startswith("http://") or media_path.startswith("https://"):
        media_url = media_path
        media_base64 = None
    else:
        if not os.path.exists(media_path):
            logger.error(f"Media file not found: {media_path}")
            return False, "File not found"
        with open(media_path, "rb") as f:
            media_base64 = base64.b64encode(f.read()).decode("utf-8")
        media_url = None

    mimetype_map = {
        "video": "video/mp4",
        "image": "image/jpeg",
        "audio": "audio/mpeg",
        "document": "application/pdf"
    }
    mimetype = mimetype_map.get(media_type, "video/mp4")
    filename = os.path.basename(media_path) if not str(media_path).startswith("http") else "video.mp4"
    if not filename.lower().endswith(('.mp4', '.mov', '.avi', '.jpg', '.jpeg', '.png', '.gif', '.mp3', '.pdf')):
        filename = f"video.mp4" if media_type == "video" else f"file.{media_type}"

    url = f"{EVOLUTION_BASE_URL}/message/sendMedia/{EVOLUTION_INSTANCE}"

    # Media: raw base64 only (no URL). Evolution accepts base64 string.
    media_value = media_base64 if media_base64 else media_url
    # Ensure phone is digits only with country code (e.g. 91917508075534)
    number_clean = str(phone).replace("+", "").replace(" ", "").replace("-", "").strip()

    delay_ms = random.randint(1000, 3000)
    # This Evolution API requires "mediaMessage" (nested format, same as sendText uses textMessage)
    payload = {
        "number": number_clean,
        "options": {"delay": delay_ms, "presence": "composing"},
        "mediaMessage": {
            "mediatype": media_type,
            "mimetype": mimetype,
            "caption": caption or "",
            "fileName": filename,
            "media": media_value,
        },
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=90)
        if response.status_code >= 400:
            logger.error(f"Evolution sendMedia {response.status_code}: {response.text[:600]}")
        response.raise_for_status()
        logger.info(f"Media ({media_type}) sent to {phone}")
        return True, response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send media to {phone}: {e}")
        return False, str(e)


def process_bulk_campaign(campaign_name, contacts, template_text, media_path=None, media_type="video"):
    """
    Run a bulk messaging campaign.

    Args:
        campaign_name (str): Name of the campaign for analytics.
        contacts (list):     List of dicts with 'Name' and 'Phone'.
        template_text (str): Message template, supports {Name} placeholder.
        media_path (str):    Optional — path or URL to a video/image file.
        media_type (str):    'video', 'image', 'audio', or 'document'.
    """
    logger.info(f"Starting Campaign [{campaign_name}] with {len(contacts)} contacts")
    if media_path:
        logger.info(f"Media attached: {media_path} (type: {media_type})")

    sent_in_batch = 0
    total_sent_this_campaign = 0

    for i, contact in enumerate(contacts):
        phone = contact.get('Phone')
        name = contact.get('Name', '')

        # Skip contacts who replied STOP / opted out
        if is_opted_out(phone):
            logger.info(f"[{i+1}/{len(contacts)}] Skipping {name} ({phone}) — opted out (STOP).")
            continue

        wait_for_business_hours()

        if get_daily_sent_count() >= DAILY_MESSAGE_LIMIT:
            logger.warning("Daily message limit reached. Campaign Paused permanently. Start a new one later.")
            break

        if sent_in_batch >= BATCH_SIZE:
            pause_time = random.randint(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX) * 60
            logger.info(f"Batch limit reached. Pausing for {pause_time/60:.1f} minutes.")
            time.sleep(pause_time)
            sent_in_batch = 0

        # Create personalized message with small random variation
        variations = ["", " ", "\n", "\r\n"]
        message = template_text.replace("{Name}", name).strip() + random.choice(variations)

        # Random delay before sending (anti-ban)
        delay = random.randint(MESSAGE_DELAY_MIN, MESSAGE_DELAY_MAX)
        logger.info(f"[{i+1}/{len(contacts)}] Waiting {delay}s before sending to {name} ({phone})...")
        time.sleep(delay)

        logger.info(f"[{i+1}/{len(contacts)}] Sending to {name}...")

        if media_path:
            # Send media with the message as caption
            success, res = send_whatsapp_media(phone, media_path, caption=message, media_type=media_type)
        else:
            # Send text only
            success, res = send_whatsapp_message(phone, message)

        status = 'sent' if success else 'failed'
        log_message(phone, name, message, 'sent', status, EVOLUTION_INSTANCE, campaign_name)

        if success:
            sent_in_batch += 1
            total_sent_this_campaign += 1

    logger.info(f"Campaign [{campaign_name}] finished. Sent {total_sent_this_campaign} messages.")


