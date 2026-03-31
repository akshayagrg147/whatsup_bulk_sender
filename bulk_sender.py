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

# When user selects "Auto" in dashboard, use pool rotation (next number when 200/day reached)
AUTO_INSTANCE = "__auto__"

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

def get_daily_sent_count(instance_name=None):
    """Messages sent today. If instance_name given, count for that instance only (per-number limit)."""
    conn = get_db_connection()
    try:
        today = get_ist_now().strftime("%Y-%m-%d")
        cur = conn.cursor()
        if instance_name:
            cur.execute(
                "SELECT COUNT(*) as c FROM messages WHERE direction='sent' AND instance_name=? AND DATE(timestamp)=?",
                (instance_name, today)
            )
        else:
            cur.execute("SELECT total_sent FROM daily_stats WHERE date=?", (today,))
            row = cur.fetchone()
            return row['total_sent'] if row else 0
        row = cur.fetchone()
        return row['c'] if row else 0
    finally:
        conn.close()

def get_next_available_instance():
    """Return first instance from EVOLUTION_INSTANCES that has sent < DAILY_MESSAGE_LIMIT today, or None if all at limit."""
    for instance in EVOLUTION_INSTANCES:
        if get_daily_sent_count(instance) < DAILY_MESSAGE_LIMIT:
            return instance
    return None

def check_instance_connected(instance_name):
    """Return True if instance is connected (WhatsApp linked). Log clearly if not."""
    try:
        r = requests.get(
            f"{EVOLUTION_BASE_URL}/instance/connectionState/{instance_name}",
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=10
        )
        if r.status_code != 200:
            logger.warning(f"Instance '{instance_name}' connection check failed: {r.status_code} {r.text[:300]}")
            return False
        data = r.json()
        state = (data.get("instance") or data.get("state") or data).get("state") if isinstance(data, dict) else None
        if not state:
            state = str(data)
        if state not in ("open", "connected", "CONNECTED"):
            logger.error(f"Instance '{instance_name}' is NOT CONNECTED (state: {state}). Scan QR at http://localhost:8080/manager or GET /instance/connect/{instance_name}")
            return False
        return True
    except Exception as e:
        logger.error(f"Could not check instance '{instance_name}': {e}. Is Evolution API running at {EVOLUTION_BASE_URL}?")
        return False

def send_whatsapp_message(phone, text, instance_name=None):
    """Send a plain text WhatsApp message. instance_name = which WhatsApp number to use."""
    instance = instance_name or EVOLUTION_INSTANCE
    url = f"{EVOLUTION_BASE_URL}/message/sendText/{instance}"
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
        if not response.ok:
            err_detail = getattr(response, "text", str(response))[:400]
            logger.error(f"Failed to send to {phone} (instance {instance}): {response.status_code} — {err_detail}")
            return False, err_detail
        return True, response.json()
    except requests.exceptions.RequestException as e:
        err_detail = str(e)
        if hasattr(e, "response") and e.response is not None and getattr(e.response, "text", None):
            err_detail = e.response.text[:400] + " | " + err_detail
        logger.error(f"Failed to send message to {phone}: {err_detail}")
        return False, err_detail


def send_whatsapp_media(phone, media_path, caption="", media_type="video", instance_name=None):
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

    instance = instance_name or EVOLUTION_INSTANCE
    url = f"{EVOLUTION_BASE_URL}/message/sendMedia/{instance}"

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


def process_bulk_campaign(campaign_name, contacts, template_text, media_path=None, media_type="video", instance_name=None):
    """
    Run a bulk messaging campaign.

    Args:
        campaign_name (str): Name of the campaign for analytics.
        contacts (list):     List of dicts with 'Name' and 'Phone'.
        template_text (str): Message template, supports {Name} placeholder.
        media_path (str):    Optional — path or URL to a video/image file.
        media_type (str):    'video', 'image', 'audio', or 'document'.
        instance_name (str): Which WhatsApp number to use. Use AUTO_INSTANCE ("__auto__") or None to
                            auto-rotate: when one number hits 200/day, switch to next in EVOLUTION_INSTANCES.
    """
    use_auto_rotation = instance_name in (None, "", AUTO_INSTANCE)
    instance = None  # set per contact when use_auto_rotation
    logger.info(f"Starting Campaign [{campaign_name}] with {len(contacts)} contacts (mode: {'auto-rotate pool' if use_auto_rotation else instance_name or EVOLUTION_INSTANCE})")
    if media_path:
        logger.info(f"Media attached: {media_path} (type: {media_type})")

    # Check connection for the instance we'll use first (so user sees why sends fail)
    first_instance = get_next_available_instance() if use_auto_rotation else (instance_name or EVOLUTION_INSTANCE)
    if first_instance and not check_instance_connected(first_instance):
        logger.error(f"Cannot send: instance '{first_instance}' is not connected. Open http://localhost:8080/manager and scan QR for this instance.")

    sent_in_batch = 0
    total_sent_this_campaign = 0

    for i, contact in enumerate(contacts):
        phone = contact.get('Phone')
        name = contact.get('Name', '')

        if use_auto_rotation:
            instance = get_next_available_instance()
            if instance is None:
                logger.warning("All instances at daily limit (200/day). Campaign paused. Resume tomorrow or add more numbers.")
                break
        else:
            instance = instance_name or EVOLUTION_INSTANCE

        # Skip contacts who replied STOP for this instance
        if is_opted_out(phone, instance):
            logger.info(f"[{i+1}/{len(contacts)}] Skipping {name} ({phone}) — opted out (STOP) for {instance}.")
            continue

        # Business hours check removed — sending allowed 24/7

        if not use_auto_rotation and get_daily_sent_count(instance) >= DAILY_MESSAGE_LIMIT:
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

        logger.info(f"[{i+1}/{len(contacts)}] Sending to {name} via {instance}...")

        if media_path:
            success, res = send_whatsapp_media(phone, media_path, caption=message, media_type=media_type, instance_name=instance)
        else:
            success, res = send_whatsapp_message(phone, message, instance_name=instance)

        status = 'sent' if success else 'failed'
        log_message(phone, name, message, 'sent', status, instance, campaign_name)

        if success:
            sent_in_batch += 1
            total_sent_this_campaign += 1

    logger.info(f"Campaign [{campaign_name}] finished. Sent {total_sent_this_campaign} messages.")


