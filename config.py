import os
from dotenv import load_dotenv

load_dotenv()

EVOLUTION_BASE_URL = os.getenv("EVOLUTION_BASE_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "your_secret_key_here")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "marketing_instance")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))

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
