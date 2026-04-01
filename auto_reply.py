import logging
import datetime
import pytz
from database import (
    log_message,
    update_message_status,
    is_first_time,
    set_opted_out,
    is_opted_out,
    get_tenant_id_for_instance,
)
from config import BUSINESS_START_HOUR, BUSINESS_END_HOUR, TIMEZONE, EVOLUTION_INSTANCE
from bulk_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

KEYWORDS = {
    ("price", "prices", "rate", "cost", "kitna", "price kya hai", "kya rate"): (
        "💰 Humare products ki price list:\n"
        "• Product A — ₹499\n"
        "• Product B — ₹999\n"
        "• Product C — ₹1499\n"
        "Bulk order ke liye alag discount milega! 😊"
    ),
    ("order", "buy", "kharidna", "purchase", "lena hai"): (
        "🛒 Order karne ke liye:\n"
        "• WhatsApp pe 'ORDER' likhen\n"
        "• Ya humari website visit karein: https://yourstore.com\n"
        "Delivery: 3-5 business days 🚚"
    ),
    ("help", "support", "problem", "issue", "complaint"): (
        "🙏 Hum aapki madad ke liye hain!\n"
        "Apni problem batayein, hum jald reply karenge.\n"
        "Ya call karein: +91-XXXXXXXXXX"
    ),
    ("hello", "hi", "hii", "hey", "helo", "namaste"): (
        "Hello! 👋 Kaise hain aap? Kya main aapki help kar sakta hoon?"
    ),
    ("thanks", "thank you", "shukriya", "dhanyawad"): (
        "😊 Aapka bahut bahut shukriya! Koi aur madad chahiye to batayein."
    ),
    ("location", "address", "shop", "store", "kahan"): (
        "📍 Hamara address:\n"
        "XYZ Market, New Delhi - 110001\n"
        "Google Maps: https://maps.google.com/..."
    )
}

# Words that mean "unsubscribe / stop promotional messages" (case-insensitive)
STOP_KEYWORDS = ("stop", "stopp", "unsubscribe", "unsub", "opt out", "optout", "remove", "cancel", "bandh karo", "ab band karo")

STOP_CONFIRM_MSG = (
    "✅ Aapko promotional messages band kar diye gaye hain.\n"
    "Aap ab is number se promos nahi paayenge.\n\n"
    "Wapas messages lene ke liye 'START' likhein."
)

START_CONFIRM_MSG = (
    "✅ Aapko wapas promotional messages ki list mein add kar diya gaya hai.\n"
    "Aap ab offers aur updates paayenge."
)

DEFAULT_REPLY = (
    "Samjha nahi 🙏 Please in mein se ek choose karein:\n\n"
    "1️⃣ Price jaanne ke liye — 'PRICE' likhein\n"
    "2️⃣ Order ke liye — 'ORDER' likhein\n"
    "3️⃣ Help ke liye — 'HELP' likhein"
)

def get_ist_now():
    tz = pytz.timezone(TIMEZONE)
    return datetime.datetime.now(tz)

def process_webhook(data, instance_name=None):
    """Process webhook payload. instance_name = which WhatsApp number received the message (for per-instance opt-out)."""
    instance = instance_name or EVOLUTION_INSTANCE
    tenant_id = get_tenant_id_for_instance(instance)
    if tenant_id is None:
        tenant_id = 1
        logger.warning(
            "Webhook for instance %r is not mapped to any user; using tenant_id=1. "
            "Set users.evolution_instances to include this instance name.",
            instance,
        )
    event = data.get('event')

    if event == 'messages.update':
        updates = data.get('data', [])
        for update in updates:
            if 'status' in update and 'key' in update:
                phone = update['key'].get('remoteJid', '').split('@')[0]
                if phone:
                    status = update['status']
                    update_message_status(phone, status.lower(), tenant_id=tenant_id)

    elif event == 'messages.upsert':
        messages = data.get('data', {}).get('messages', [])
        for msg in messages:
            if msg.get('key', {}).get('fromMe', False):
                continue

            phone = msg.get('key', {}).get('remoteJid', '').split('@')[0]
            if not phone:
                continue

            msg_obj = msg.get('message', {})
            text = (msg_obj.get('conversation') or
                    msg_obj.get('extendedTextMessage', {}).get('text') or '')

            if not text:
                continue

            remote_name = msg.get('pushName', 'Unknown')
            first = is_first_time(phone, tenant_id)

            log_message(phone, remote_name, text, 'received', 'received', instance, tenant_id=tenant_id)
            handle_incoming_text(
                phone, remote_name, text, first, instance_name=instance, tenant_id=tenant_id
            )

def handle_incoming_text(phone, name, text, first_time, instance_name=None, tenant_id=1):
    instance = instance_name or EVOLUTION_INSTANCE
    text_lower = (text or "").strip().lower()

    # Handle STOP / unsubscribe (per instance)
    if any(kw in text_lower for kw in STOP_KEYWORDS):
        set_opted_out(phone, True, instance_name=instance)
        send_whatsapp_message(phone, STOP_CONFIRM_MSG, instance_name=instance)
        log_message(phone, name, STOP_CONFIRM_MSG, 'sent', 'sent', instance, 'auto_reply', tenant_id=tenant_id)
        logger.info(f"Contact {phone} ({name}) opted out from promotional messages (instance: {instance}).")
        return
    # Handle START / re-subscribe (per instance)
    if text_lower == "start":
        set_opted_out(phone, False, instance_name=instance)
        send_whatsapp_message(phone, START_CONFIRM_MSG, instance_name=instance)
        log_message(phone, name, START_CONFIRM_MSG, 'sent', 'sent', instance, 'auto_reply', tenant_id=tenant_id)
        logger.info(f"Contact {phone} ({name}) re-subscribed (instance: {instance}).")
        return

    now = get_ist_now()
    hour = now.hour

    if hour < BUSINESS_START_HOUR or hour >= BUSINESS_END_HOUR:
        after_hours_msg = (
            "🌙 Abhi hum available nahi hain.\n"
            "Humari timing: Subah 9 AM – Raat 9 PM\n\n"
            "Hum kal subah 9 baje aapko reply karenge! 🙏\n"
            "Emergency ke liye: +91-XXXXXXXXXX"
        )
        send_whatsapp_message(phone, after_hours_msg, instance_name=instance)
        log_message(phone, name, after_hours_msg, 'sent', 'sent', instance, 'auto_reply', tenant_id=tenant_id)
        return

    if first_time:
        welcome_msg = (
            "Namaste! 🙏 Hamare business mein aapka swagat hai!\n"
            "Main aapki kaise madad kar sakta hoon?\n\n"
            "Reply karein:\n"
            "1️⃣ Price list ke liye\n"
            "2️⃣ Order karne ke liye\n"
            "3️⃣ Support ke liye"
        )
        send_whatsapp_message(phone, welcome_msg, instance_name=instance)
        log_message(phone, name, welcome_msg, 'sent', 'sent', instance, 'auto_reply', tenant_id=tenant_id)

        quick_replies = (
            "⬇️ Quick reply ke liye yeh type karein:\n"
            "🔹 ORDER KARNA HAI\n"
            "🔹 PRICE LIST CHAHIYE\n"
            "🔹 SUPPORT CHAHIYE"
        )
        send_whatsapp_message(phone, quick_replies, instance_name=instance)
        log_message(phone, name, quick_replies, 'sent', 'sent', instance, 'auto_reply', tenant_id=tenant_id)
        return

    text_lower = text.lower()
    matched = False
    for keywords, reply_text in KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            send_whatsapp_message(phone, reply_text, instance_name=instance)
            log_message(phone, name, reply_text, 'sent', 'sent', instance, 'auto_reply', tenant_id=tenant_id)
            matched = True
            break

    if not matched:
        send_whatsapp_message(phone, DEFAULT_REPLY, instance_name=instance)
        log_message(phone, name, DEFAULT_REPLY, 'sent', 'sent', instance, 'auto_reply', tenant_id=tenant_id)
