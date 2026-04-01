import logging
import datetime
import pytz
import requests
from database import (
    log_message,
    update_message_status,
    is_first_time,
    set_opted_out,
    get_tenant_id_for_instance,
    get_db_connection,
)
from config import (
    TIMEZONE,
    EVOLUTION_INSTANCE,
    AI_AUTOREPLY_ENABLED,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    AI_AUTOREPLY_MAX_CONTEXT_MESSAGES,
    AI_BUSINESS_NAME,
    AI_ASSISTANT_LANGUAGE,
    AI_SYSTEM_PROMPT,
)
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


def _normalize_jid_user(value):
    if not value:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    raw = raw.split("@")[0]
    raw = raw.split(":")[0]
    return raw.strip()


def _looks_like_phone(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return 10 <= len(digits) <= 15


def _extract_incoming_phone(msg):
    key = msg.get("key") or {}
    candidates = [
        msg.get("senderPn"),
        msg.get("sender"),
        msg.get("participantPn"),
        msg.get("participant"),
        key.get("senderPn"),
        key.get("participantPn"),
        key.get("participant"),
        key.get("remoteJid"),
        msg.get("remoteJid"),
        msg.get("chatId"),
        msg.get("jid"),
    ]

    normalized = []
    for candidate in candidates:
        value = _normalize_jid_user(candidate)
        if value and value not in normalized:
            normalized.append(value)

    for value in normalized:
        if _looks_like_phone(value):
            return value

    return normalized[0] if normalized else ""


def _log_incoming_sender_debug(msg):
    key = msg.get("key") or {}
    debug_fields = {
        "senderPn": msg.get("senderPn"),
        "sender": msg.get("sender"),
        "participantPn": msg.get("participantPn"),
        "participant": msg.get("participant"),
        "remoteJid": msg.get("remoteJid"),
        "chatId": msg.get("chatId"),
        "jid": msg.get("jid"),
        "key.senderPn": key.get("senderPn"),
        "key.participantPn": key.get("participantPn"),
        "key.participant": key.get("participant"),
        "key.remoteJid": key.get("remoteJid"),
    }
    logger.info("Inbound sender debug: %s", debug_fields)


def _send_and_log_reply(phone, name, reply_text, instance, tenant_id, campaign_name="auto_reply"):
    success, _ = send_whatsapp_message(phone, reply_text, instance_name=instance)
    status = "sent" if success else "failed"
    log_message(phone, name, reply_text, "sent", status, instance, campaign_name, tenant_id=tenant_id)
    return success

def get_ist_now():
    tz = pytz.timezone(TIMEZONE)
    return datetime.datetime.now(tz)


def _normalize_webhook_event(raw):
    """Evolution uses 'messages.update'; some stacks send MESSAGES_UPDATE / messages_update."""
    if not raw:
        return ""
    return str(raw).lower().replace("_", ".")


def _iter_messages_update_data(data_field):
    """Evolution v2 sends a single object; older payloads may send a list."""
    if data_field is None:
        return
    if isinstance(data_field, list):
        for item in data_field:
            if isinstance(item, dict):
                yield item
    elif isinstance(data_field, dict):
        yield data_field


def _messages_from_upsert_data(inner):
    """Evolution v2 sends one message as `data`; Baileys-style payloads use `data.messages` or a list."""
    if inner is None:
        return []
    if isinstance(inner, list):
        return [m for m in inner if isinstance(m, dict)]
    if isinstance(inner, dict):
        if "messages" in inner:
            raw = inner.get("messages") or []
            return raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        if inner.get("key"):
            return [inner]
    return []


def _map_evolution_ack_status(evolution_status):
    """
    Evolution forwards Baileys-style names: DELIVERY_ACK, READ, SERVER_ACK, etc.
    Returns our messages.status + daily_stats bucket name, or None to ignore.
    """
    if evolution_status is None:
        return None
    s = str(evolution_status).strip().upper()
    if s == "DELIVERY_ACK":
        return "delivered"
    if s in ("READ", "PLAYED"):
        return "read"
    if s == "ERROR":
        return "error"
    if s == "FAILED":
        return "failed"
    return None


def _recent_conversation_context(phone, tenant_id, limit=8):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT direction, message_text, timestamp
            FROM messages
            WHERE phone=? AND tenant_id=?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (phone, tenant_id, max(1, limit)),
        )
        rows = list(reversed(cur.fetchall()))
        lines = []
        for row in rows:
            speaker = "Customer" if row["direction"] == "received" else "Business"
            lines.append(f"{speaker}: {row['message_text']}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Error loading recent conversation context: {e}")
        return ""
    finally:
        conn.close()


def _extract_text_from_openai_response(payload):
    output = payload.get("output") or []
    parts = []
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    text = "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return text or (payload.get("output_text") or "").strip()


def generate_ai_reply(phone, name, text, tenant_id=1):
    if not AI_AUTOREPLY_ENABLED or not OPENAI_API_KEY:
        return None

    conversation = _recent_conversation_context(phone, tenant_id, AI_AUTOREPLY_MAX_CONTEXT_MESSAGES)
    language_hint = "Hindi" if AI_ASSISTANT_LANGUAGE.lower().startswith("hi") else "English"
    prompt = (
        f"Business name: {AI_BUSINESS_NAME}\n"
        f"Preferred reply language: {language_hint}\n"
        f"Customer name: {name or 'Customer'}\n"
        f"Recent conversation:\n{conversation or 'No previous conversation.'}\n\n"
        f"Latest customer message:\n{text}\n\n"
        "Write exactly one concise WhatsApp reply as the business. "
        "Be helpful and natural. Do not invent prices, policies, or promises."
    )

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "input": [
                    {"role": "system", "content": AI_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_output_tokens": 220,
            },
            timeout=25,
        )
        response.raise_for_status()
        reply = _extract_text_from_openai_response(response.json())
        return reply or None
    except Exception as e:
        logger.error(f"AI auto-reply failed: {e}")
        return None


def process_webhook(data, instance_name=None):
    """Process webhook payload. instance_name = which WhatsApp number received the message (for per-instance opt-out)."""
    data = data or {}
    # Evolution JSON always includes "instance"; URL may be POST /webhook without /<instance>
    instance = instance_name or data.get("instance") or EVOLUTION_INSTANCE
    tenant_id = get_tenant_id_for_instance(instance)
    if tenant_id is None:
        tenant_id = 1
        logger.warning(
            "Webhook for instance %r is not mapped to any user; using tenant_id=1. "
            "Set users.evolution_instances to include this instance name.",
            instance,
        )
    event = _normalize_webhook_event(data.get("event"))

    if event == "messages.update":
        for update in _iter_messages_update_data(data.get("data")):
            if update.get("fromMe") is not True and update.get("fromMe") != 1:
                continue
            mapped = _map_evolution_ack_status(update.get("status"))
            if not mapped:
                continue
            phone = ""
            key = update.get("key")
            if isinstance(key, dict):
                phone = (key.get("remoteJid") or "").split("@")[0]
            if not phone:
                phone = (update.get("remoteJid") or "").split("@")[0]
            if phone:
                update_message_status(phone, mapped, tenant_id=tenant_id)

    elif event == "messages.upsert":
        for msg in _messages_from_upsert_data(data.get("data")):
            if msg.get('key', {}).get('fromMe', False):
                continue

            _log_incoming_sender_debug(msg)
            phone = _extract_incoming_phone(msg)
            if not phone:
                logger.warning("Could not extract sender phone from inbound webhook: %s", msg)
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
        _send_and_log_reply(phone, name, STOP_CONFIRM_MSG, instance, tenant_id)
        logger.info(f"Contact {phone} ({name}) opted out from promotional messages (instance: {instance}).")
        return
    # Handle START / re-subscribe (per instance)
    if text_lower == "start":
        set_opted_out(phone, False, instance_name=instance)
        _send_and_log_reply(phone, name, START_CONFIRM_MSG, instance, tenant_id)
        logger.info(f"Contact {phone} ({name}) re-subscribed (instance: {instance}).")
        return

    ai_reply = generate_ai_reply(phone, name, text, tenant_id=tenant_id)
    if ai_reply:
        _send_and_log_reply(phone, name, ai_reply, instance, tenant_id, campaign_name="auto_reply_ai")
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
        _send_and_log_reply(phone, name, welcome_msg, instance, tenant_id)

        quick_replies = (
            "⬇️ Quick reply ke liye yeh type karein:\n"
            "🔹 ORDER KARNA HAI\n"
            "🔹 PRICE LIST CHAHIYE\n"
            "🔹 SUPPORT CHAHIYE"
        )
        _send_and_log_reply(phone, name, quick_replies, instance, tenant_id)
        return

    text_lower = text.lower()
    matched = False
    for keywords, reply_text in KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            _send_and_log_reply(phone, name, reply_text, instance, tenant_id)
            matched = True
            break

    if not matched:
        _send_and_log_reply(phone, name, DEFAULT_REPLY, instance, tenant_id)
