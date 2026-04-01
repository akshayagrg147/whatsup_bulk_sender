import os
from dotenv import load_dotenv

load_dotenv()

EVOLUTION_BASE_URL = os.getenv("EVOLUTION_BASE_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "your_secret_key_here")
# Single instance (default) for backward compatibility
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "marketing_instance")
# Multiple instances: comma-separated, e.g. EVOLUTION_INSTANCES=number1,number2,support
# If set, these are used for webhook registration and instance dropdown in dashboard.
_instances_raw = os.getenv("EVOLUTION_INSTANCES", "").strip()
EVOLUTION_INSTANCES = [s.strip() for s in _instances_raw.split(",") if s.strip()] if _instances_raw else [EVOLUTION_INSTANCE]
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))
# When running app inside Docker with Evolution API, set e.g. WEBHOOK_BASE_URL=http://whatsapp-marketing:5001
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", f"http://host.docker.internal:{FLASK_PORT}")

DAILY_MESSAGE_LIMIT = int(os.getenv("DAILY_MESSAGE_LIMIT", 200))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 30))
BATCH_PAUSE_MIN = int(os.getenv("BATCH_PAUSE_MIN", 5))
BATCH_PAUSE_MAX = int(os.getenv("BATCH_PAUSE_MAX", 10))
MESSAGE_DELAY_MIN = int(os.getenv("MESSAGE_DELAY_MIN", 4))
MESSAGE_DELAY_MAX = int(os.getenv("MESSAGE_DELAY_MAX", 9))
BUSINESS_START_HOUR = int(os.getenv("BUSINESS_START_HOUR", 9))
BUSINESS_END_HOUR = int(os.getenv("BUSINESS_END_HOUR", 21))

TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
DB_PATH = os.getenv("DB_PATH", "./data/marketing.db")
YOUR_STORE_URL = os.getenv("YOUR_STORE_URL", "https://yourstore.com")
YOUR_PHONE = os.getenv("YOUR_PHONE", "+91-XXXXXXXXXX")

AI_AUTOREPLY_ENABLED = os.getenv("AI_AUTOREPLY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
AI_AUTOREPLY_MAX_CONTEXT_MESSAGES = int(os.getenv("AI_AUTOREPLY_MAX_CONTEXT_MESSAGES", 8))
AI_BUSINESS_NAME = os.getenv("AI_BUSINESS_NAME", "our business").strip() or "our business"
AI_ASSISTANT_LANGUAGE = os.getenv("AI_ASSISTANT_LANGUAGE", "en").strip() or "en"
AI_SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT",
    (
        "You are a professional WhatsApp business assistant. Reply briefly, clearly, and helpfully. "
        "Keep replies suitable for WhatsApp, avoid long paragraphs, do not invent pricing or policies, "
        "and ask a short follow-up question when needed."
    ),
).strip()
