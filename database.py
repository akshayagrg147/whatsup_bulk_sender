import sqlite3
import os
import logging
import datetime
import pytz
from config import DB_PATH, TIMEZONE

# Ensure log directory exists if logging to file
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[
    logging.StreamHandler(),
    logging.FileHandler("app.log", mode='a')
])
logger = logging.getLogger(__name__)

def get_ist_now():
    tz = pytz.timezone(TIMEZONE)
    return datetime.datetime.now(tz)

def get_db_connection():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    try:
        with conn:
            # Table: messages
            conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT,
                    name TEXT,
                    message_text TEXT,
                    direction TEXT,
                    status TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    instance_name TEXT,
                    campaign_name TEXT
                )
            ''')
            # Table: contacts
            conn.execute('''
                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT UNIQUE,
                    name TEXT,
                    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_messages_sent INTEGER DEFAULT 0,
                    total_replies INTEGER DEFAULT 0,
                    is_blocked BOOLEAN DEFAULT 0,
                    opted_out INTEGER DEFAULT 0,
                    label TEXT
                )
            ''')
            # Add opted_out column for existing DBs (no-op if already exists)
            try:
                conn.execute('ALTER TABLE contacts ADD COLUMN opted_out INTEGER DEFAULT 0')
            except sqlite3.OperationalError:
                pass
            # Table: daily_stats
            conn.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date DATE PRIMARY KEY,
                    total_sent INTEGER DEFAULT 0,
                    total_delivered INTEGER DEFAULT 0,
                    total_read INTEGER DEFAULT 0,
                    total_replied INTEGER DEFAULT 0,
                    total_blocked INTEGER DEFAULT 0
                )
            ''')
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing DB: {e}")
    finally:
        conn.close()

def log_message(phone, name, msg_text, direction, status, instance_name, campaign_name=None):
    conn = get_db_connection()
    now = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
    today = get_ist_now().strftime("%Y-%m-%d")

    try:
        with conn:
            conn.execute('''
                INSERT INTO messages (phone, name, message_text, direction, status, timestamp, instance_name, campaign_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (phone, name, msg_text, direction, status, now, instance_name, campaign_name))
            
            # Update contacts
            if direction == 'sent':
                conn.execute('''
                    INSERT INTO contacts (phone, name, total_messages_sent, last_seen) 
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(phone) DO UPDATE SET 
                    name=excluded.name,
                    last_seen=?,
                    total_messages_sent=total_messages_sent+1
                ''', (phone, name, now, now))
                
                # Update daily stats
                conn.execute('''
                    INSERT INTO daily_stats (date, total_sent)
                    VALUES (?, 1)
                    ON CONFLICT(date) DO UPDATE SET total_sent=total_sent+1
                ''', (today,))
                
            elif direction == 'received':
                # Determine if it's the first time receiving
                cur = conn.cursor()
                cur.execute("SELECT total_replies FROM contacts WHERE phone=?", (phone,))
                res = cur.fetchone()
                
                if res:
                    conn.execute('''
                        UPDATE contacts SET name=?, last_seen=?, total_replies=total_replies+1
                        WHERE phone=?
                    ''', (name, now, phone))
                else:
                    conn.execute('''
                        INSERT INTO contacts (phone, name, total_replies, first_seen, last_seen) 
                        VALUES (?, ?, 1, ?, ?)
                    ''', (phone, name, now, now))
                
                conn.execute('''
                    INSERT INTO daily_stats (date, total_replied)
                    VALUES (?, 1)
                    ON CONFLICT(date) DO UPDATE SET total_replied=total_replied+1
                ''', (today,))
    except Exception as e:
        logger.error(f"Error logging message: {e}")
    finally:
        conn.close()

def update_message_status(phone, status):
    conn = get_db_connection()
    today = get_ist_now().strftime("%Y-%m-%d")
    try:
        with conn:
            # Get current status to prevent over-counting
            cur = conn.cursor()
            cur.execute('''
                SELECT id, status FROM messages 
                WHERE phone=? AND direction='sent'
                ORDER BY timestamp DESC LIMIT 1
            ''', (phone,))
            row = cur.fetchone()
            
            if row and row['status'] != status:
                msg_id = row['id']
                conn.execute('UPDATE messages SET status=? WHERE id=?', (status, msg_id))
                
                if status == 'delivered':
                    conn.execute('INSERT INTO daily_stats (date, total_delivered) VALUES (?, 1) ON CONFLICT(date) DO UPDATE SET total_delivered=total_delivered+1', (today,))
                elif status == 'read':
                    conn.execute('INSERT INTO daily_stats (date, total_read) VALUES (?, 1) ON CONFLICT(date) DO UPDATE SET total_read=total_read+1', (today,))
                
                if status in ('error', 'failed'):
                     conn.execute('UPDATE contacts SET is_blocked=1 WHERE phone=?', (phone,))
                     conn.execute('INSERT INTO daily_stats (date, total_blocked) VALUES (?, 1) ON CONFLICT(date) DO UPDATE SET total_blocked=total_blocked+1', (today,))
    except Exception as e:
        logger.error(f"Error updating status: {e}")
    finally:
        conn.close()

def is_first_time(phone):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM messages WHERE phone=? AND direction='received'", (phone,))
        return cur.fetchone() is None
    except Exception as e:
        logger.error(f"Error checking first time: {e}")
        return False
    finally:
        conn.close()

def set_opted_out(phone, opted_out=True):
    """Mark contact as opted out (unsubscribed) from promotional messages."""
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("UPDATE contacts SET opted_out=? WHERE phone=?", (1 if opted_out else 0, phone))
            if conn.total_changes == 0:
                conn.execute(
                    "INSERT INTO contacts (phone, opted_out) VALUES (?, ?)",
                    (phone, 1 if opted_out else 0)
                )
    except Exception as e:
        logger.error(f"Error setting opted_out: {e}")
    finally:
        conn.close()

def is_opted_out(phone):
    """Return True if contact has opted out (replied STOP)."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT opted_out FROM contacts WHERE phone=?", (phone,))
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception as e:
        logger.error(f"Error checking opted_out: {e}")
        return False
    finally:
        conn.close()
