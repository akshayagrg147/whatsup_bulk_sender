import sqlite3
import os
import uuid
import logging
import datetime
import pytz
from werkzeug.security import generate_password_hash, check_password_hash
from config import DB_PATH, TIMEZONE

# Ensure log directory exists if logging to file
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[
    logging.StreamHandler(),
    logging.FileHandler("app.log", mode='a')
])
logger = logging.getLogger(__name__)

def parse_evolution_instances(value):
    if not value or not str(value).strip():
        return []
    return [s.strip() for s in str(value).split(",") if s.strip()]

def _uuid_compact(s):
    """Hex digits only, lowercase (for comparing hyphenated vs compact instance names)."""
    return "".join(c for c in str(s or "") if c.isalnum()).lower()


def _phone_digits(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def public_id_matches_instance_name(public_id, instance_name):
    """True if Evolution instance name is this account's public_id (with or without hyphens)."""
    if not public_id or not instance_name:
        return False
    pid = str(public_id).strip().lower()
    inst = str(instance_name).strip().lower()
    if inst == pid:
        return True
    c1, c2 = _uuid_compact(pid), _uuid_compact(inst)
    return len(c1) == 32 and c1 == c2


def default_instance_name_from_public_id(public_id):
    """
    Default Evolution instance name = same as public_id UUID (lowercase, hyphens kept).
    Matches how Evolution Manager often shows UUIDs. Compact (no-hyphen) names still map via public_id_matches_instance_name.
    """
    if not public_id:
        return ""
    return str(public_id).strip().lower()

def user_instance_display_pool(public_id, stored_instances):
    """
    Names for dashboard dropdown and upload validation: default-from-public_id first, then DB CSV list (deduped).
    Skips stored names that are only a different formatting of the same UUID as public_id.
    """
    stored = list(stored_instances or [])
    pid_name = default_instance_name_from_public_id(public_id or "")
    pid_c = _uuid_compact(pid_name) if pid_name else ""
    merged = []
    seen_display = set()
    seen_compact = set()
    if pid_name:
        merged.append(pid_name)
        seen_display.add(pid_name)
        if pid_c:
            seen_compact.add(pid_c)
    for n in stored:
        n = str(n).strip()
        if not n or n in seen_display:
            continue
        nc = _uuid_compact(n)
        if nc and nc == pid_c:
            continue
        if nc in seen_compact:
            continue
        merged.append(n)
        seen_display.add(n)
        if nc:
            seen_compact.add(nc)
    return merged

def find_evolution_instance_owner(instance_name, exclude_user_id=None):
    """Return user id that owns this Evolution instance name, or None. Names are globally unique across tenants."""
    if not instance_name or not str(instance_name).strip():
        return None
    name = str(instance_name).strip()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, evolution_instances, public_id FROM users WHERE is_active = 1"
        )
        for row in cur.fetchall():
            if row["id"] == exclude_user_id:
                continue
            if name in parse_evolution_instances(row["evolution_instances"] or ""):
                return row["id"]
            if public_id_matches_instance_name(row["public_id"] or "", name):
                return row["id"]
    finally:
        conn.close()
    return None

def get_tenant_id_for_instance(instance_name):
    """Map Evolution instance name to app user id (tenant)."""
    if not instance_name:
        return None
    name = str(instance_name).strip()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, evolution_instances, public_id FROM users WHERE is_active = 1"
        )
        for row in cur.fetchall():
            if name in parse_evolution_instances(row["evolution_instances"]):
                return row["id"]
            if public_id_matches_instance_name(row["public_id"] or "", name):
                return row["id"]
    except Exception as e:
        logger.error(f"get_tenant_id_for_instance: {e}")
    finally:
        conn.close()
    return None

def get_all_instance_names_from_db():
    """Union of every user's Evolution instance names (for webhook registration)."""
    seen = set()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT evolution_instances, public_id FROM users WHERE is_active = 1")
        for row in cur.fetchall():
            for name in parse_evolution_instances(row["evolution_instances"] or ""):
                seen.add(name)
            pid_name = default_instance_name_from_public_id(row["public_id"] or "")
            if pid_name:
                seen.add(pid_name)
            # Do not also add compact (no-hyphen) UUID here: register_webhook creates
            # missing instances, so duplicate spellings would create two Evolution shells.
            # Compact names still work if listed in evolution_instances CSV.
    finally:
        conn.close()
    return seen

def get_ist_now():
    tz = pytz.timezone(TIMEZONE)
    return datetime.datetime.now(tz)

def get_db_connection():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _user_table_columns(conn):
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA table_info(users)")
        return [r[1] for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []

def _strip_legacy_instance_names(conn, cur, drop_names):
    """Remove obsolete instance tokens from users.evolution_instances CSV (one-time cleanup per app start)."""
    drop = frozenset(drop_names)
    cur.execute(
        "SELECT id, evolution_instances FROM users WHERE evolution_instances IS NOT NULL AND trim(evolution_instances) != ''"
    )
    for row in cur.fetchall():
        names = [n for n in parse_evolution_instances(row["evolution_instances"]) if n not in drop]
        new_csv = ",".join(names)
        old = (row["evolution_instances"] or "").strip()
        if new_csv != old:
            conn.execute(
                "UPDATE users SET evolution_instances = ? WHERE id = ?",
                (new_csv, row["id"]),
            )

def _ensure_users_column(conn, col_name, alter_sql):
    """Add column if users table exists and column is missing. Each ALTER isolated so one failure does not skip the rest."""
    ucols = _user_table_columns(conn)
    if not ucols or col_name in ucols:
        return
    try:
        conn.execute(alter_sql)
    except sqlite3.OperationalError:
        pass

def _run_tenant_migrations(conn):
    """Add multi-tenant columns and rebuild tables that need composite keys."""
    cur = conn.cursor()
    _ensure_users_column(
        conn, "evolution_instances", "ALTER TABLE users ADD COLUMN evolution_instances TEXT DEFAULT ''"
    )
    # SQLite does not allow "ADD COLUMN ... UNIQUE"; add plain TEXT then unique index.
    _ensure_users_column(conn, "public_id", "ALTER TABLE users ADD COLUMN public_id TEXT")
    try:
        cur.execute("PRAGMA table_info(messages)")
        mcols = [r[1] for r in cur.fetchall()]
        if mcols and "tenant_id" not in mcols:
            conn.execute("ALTER TABLE messages ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("PRAGMA table_info(contacts)")
        ccols = [r[1] for r in cur.fetchall()]
    except sqlite3.OperationalError:
        ccols = []
    if ccols and "tenant_id" not in ccols:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS contacts_mig (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL DEFAULT 1,
                phone TEXT NOT NULL,
                name TEXT,
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                total_messages_sent INTEGER DEFAULT 0,
                total_replies INTEGER DEFAULT 0,
                is_blocked BOOLEAN DEFAULT 0,
                opted_out INTEGER DEFAULT 0,
                label TEXT,
                UNIQUE(tenant_id, phone)
            );
            INSERT INTO contacts_mig (tenant_id, phone, name, first_seen, last_seen, total_messages_sent, total_replies, is_blocked, opted_out, label)
            SELECT 1, phone, name, first_seen, last_seen, total_messages_sent, total_replies, is_blocked, COALESCE(opted_out, 0), label FROM contacts;
            DROP TABLE contacts;
            ALTER TABLE contacts_mig RENAME TO contacts;
        """)
    try:
        cur.execute("PRAGMA table_info(daily_stats)")
        dcols = [r[1] for r in cur.fetchall()]
    except sqlite3.OperationalError:
        dcols = []
    if dcols and "tenant_id" not in dcols:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_stats_mig (
                tenant_id INTEGER NOT NULL DEFAULT 1,
                date DATE NOT NULL,
                total_sent INTEGER DEFAULT 0,
                total_delivered INTEGER DEFAULT 0,
                total_read INTEGER DEFAULT 0,
                total_replied INTEGER DEFAULT 0,
                total_blocked INTEGER DEFAULT 0,
                PRIMARY KEY (tenant_id, date)
            );
            INSERT INTO daily_stats_mig (tenant_id, date, total_sent, total_delivered, total_read, total_replied, total_blocked)
            SELECT 1, date, total_sent, total_delivered, total_read, total_replied, total_blocked FROM daily_stats;
            DROP TABLE daily_stats;
            ALTER TABLE daily_stats_mig RENAME TO daily_stats;
        """)
    ucols = _user_table_columns(conn)
    if ucols:
        if "public_id" in ucols:
            cur.execute(
                "SELECT id FROM users WHERE public_id IS NULL OR TRIM(COALESCE(public_id, '')) = ''"
            )
            for row in cur.fetchall():
                conn.execute(
                    "UPDATE users SET public_id = ? WHERE id = ?",
                    (str(uuid.uuid4()), row["id"]),
                )
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_id ON users(public_id)"
                )
            except sqlite3.OperationalError:
                pass
        if "evolution_instances" in ucols and "public_id" in ucols:
            conn.execute(
                """
                UPDATE users SET evolution_instances = lower(trim(public_id))
                WHERE (evolution_instances IS NULL OR trim(evolution_instances) = '')
                  AND public_id IS NOT NULL AND trim(public_id) != ''
                """
            )
            conn.execute(
                """
                UPDATE users SET evolution_instances = lower(trim(public_id))
                WHERE public_id IS NOT NULL AND trim(public_id) != ''
                  AND evolution_instances IS NOT NULL AND trim(evolution_instances) != ''
                  AND evolution_instances NOT LIKE '%,%'
                  AND lower(replace(trim(evolution_instances), '-', ''))
                      = lower(replace(trim(public_id), '-', ''))
                """
            )
        if "evolution_instances" in ucols:
            _strip_legacy_instance_names(conn, cur, ("t1_main", "t1_extra"))

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
                    campaign_name TEXT,
                    campaign_run_id INTEGER,
                    tenant_id INTEGER NOT NULL DEFAULT 1
                )
            ''')
            try:
                conn.execute('ALTER TABLE messages ADD COLUMN campaign_run_id INTEGER')
            except sqlite3.OperationalError:
                pass
            # Table: contacts (per-tenant unique phone)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    phone TEXT NOT NULL,
                    name TEXT,
                    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    total_messages_sent INTEGER DEFAULT 0,
                    total_replies INTEGER DEFAULT 0,
                    is_blocked BOOLEAN DEFAULT 0,
                    opted_out INTEGER DEFAULT 0,
                    label TEXT,
                    UNIQUE(tenant_id, phone)
                )
            ''')
            # Add opted_out column for existing DBs (no-op if already exists)
            try:
                conn.execute('ALTER TABLE contacts ADD COLUMN opted_out INTEGER DEFAULT 0')
            except sqlite3.OperationalError:
                pass
            # Table: daily_stats (per-tenant per day)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    date DATE NOT NULL,
                    total_sent INTEGER DEFAULT 0,
                    total_delivered INTEGER DEFAULT 0,
                    total_read INTEGER DEFAULT 0,
                    total_replied INTEGER DEFAULT 0,
                    total_blocked INTEGER DEFAULT 0,
                    PRIMARY KEY (tenant_id, date)
                )
            ''')
            # Table: contact_opt_out (per-instance)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS contact_opt_out (
                    phone TEXT,
                    instance_name TEXT,
                    opted_out INTEGER DEFAULT 1,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (phone, instance_name)
                )
            ''')
            # Table: users (Login & Subscription)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    subscription_expiry DATETIME,
                    is_active INTEGER DEFAULT 1,
                    is_admin INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    evolution_instances TEXT DEFAULT '',
                    public_id TEXT UNIQUE
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS campaign_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    campaign_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    total_contacts INTEGER DEFAULT 0,
                    sent_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    instance_mode TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    stopped_at DATETIME,
                    completed_at DATETIME
                )
            ''')

            _run_tenant_migrations(conn)

            # Create a default admin user if no users exist
            cur = conn.cursor()
            cur.execute("SELECT id FROM users LIMIT 1")
            if not cur.fetchone():
                admin_pass = generate_password_hash("admin123")
                expiry = (get_ist_now() + datetime.timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
                admin_pid = str(uuid.uuid4())
                admin_inst = default_instance_name_from_public_id(admin_pid)
                conn.execute('''
                    INSERT INTO users (username, password_hash, subscription_expiry, is_admin, evolution_instances, public_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', ("admin", admin_pass, expiry, 1, admin_inst, admin_pid))
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing DB: {e}")
    finally:
        conn.close()

def log_message(phone, name, msg_text, direction, status, instance_name, campaign_name=None, tenant_id=1, campaign_run_id=None):
    conn = get_db_connection()
    now = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
    today = get_ist_now().strftime("%Y-%m-%d")

    try:
        with conn:
            conn.execute('''
                INSERT INTO messages (phone, name, message_text, direction, status, timestamp, instance_name, campaign_name, campaign_run_id, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (phone, name, msg_text, direction, status, now, instance_name, campaign_name, campaign_run_id, tenant_id))

            if direction == 'sent':
                conn.execute('''
                    INSERT INTO contacts (tenant_id, phone, name, total_messages_sent, last_seen)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(tenant_id, phone) DO UPDATE SET
                    name=excluded.name,
                    last_seen=excluded.last_seen,
                    total_messages_sent=total_messages_sent+1
                ''', (tenant_id, phone, name, now))

                conn.execute('''
                    INSERT INTO daily_stats (tenant_id, date, total_sent)
                    VALUES (?, ?, 1)
                    ON CONFLICT(tenant_id, date) DO UPDATE SET total_sent=total_sent+1
                ''', (tenant_id, today))

            elif direction == 'received':
                cur = conn.cursor()
                cur.execute(
                    "SELECT total_replies FROM contacts WHERE phone=? AND tenant_id=?",
                    (phone, tenant_id),
                )
                res = cur.fetchone()

                if res:
                    conn.execute('''
                        UPDATE contacts SET name=?, last_seen=?, total_replies=total_replies+1
                        WHERE phone=? AND tenant_id=?
                    ''', (name, now, phone, tenant_id))
                else:
                    conn.execute('''
                        INSERT INTO contacts (tenant_id, phone, name, total_replies, first_seen, last_seen)
                        VALUES (?, ?, ?, 1, ?, ?)
                    ''', (tenant_id, phone, name, now, now))

                conn.execute('''
                    INSERT INTO daily_stats (tenant_id, date, total_replied)
                    VALUES (?, ?, 1)
                    ON CONFLICT(tenant_id, date) DO UPDATE SET total_replied=total_replied+1
                ''', (tenant_id, today))
    except Exception as e:
        logger.error(f"Error logging message: {e}")
    finally:
        conn.close()

def _find_latest_sent_for_jid(cur, tenant_id, jid_user_part):
    """
    Match webhook JID user (e.g. 91876543210) to our messages.phone (Excel may add 91, spaces, +).
    Scan recent sent rows so ACKs still match after large blasts.
    """
    jid_digits = _phone_digits(jid_user_part)
    if not jid_digits:
        return None
    cur.execute(
        """
        SELECT id, status, phone FROM messages
        WHERE direction='sent' AND tenant_id=?
        ORDER BY timestamp DESC LIMIT 2500
        """,
        (tenant_id,),
    )
    rows = cur.fetchall()
    for r in rows:
        if _phone_digits(r["phone"]) == jid_digits:
            return r
    if len(jid_digits) >= 10:
        tail = jid_digits[-10:]
        for r in rows:
            pd = _phone_digits(r["phone"])
            if len(pd) >= 10 and pd[-10:] == tail:
                return r
    return None


def update_message_status(phone, status, tenant_id=1):
    conn = get_db_connection()
    today = get_ist_now().strftime("%Y-%m-%d")
    try:
        with conn:
            cur = conn.cursor()
            row = _find_latest_sent_for_jid(cur, tenant_id, phone)

            if row and row['status'] != status:
                old_status = row['status']
                msg_id = row['id']
                conn.execute('UPDATE messages SET status=? WHERE id=?', (status, msg_id))

                if status == 'delivered':
                    conn.execute(
                        '''INSERT INTO daily_stats (tenant_id, date, total_delivered) VALUES (?, ?, 1)
                           ON CONFLICT(tenant_id, date) DO UPDATE SET total_delivered=total_delivered+1''',
                        (tenant_id, today),
                    )
                elif status == 'read':
                    # Some webhooks emit READ without a prior DELIVERY_ACK; count delivery so rates stay sensible.
                    if old_status == 'sent':
                        conn.execute(
                            '''INSERT INTO daily_stats (tenant_id, date, total_delivered) VALUES (?, ?, 1)
                               ON CONFLICT(tenant_id, date) DO UPDATE SET total_delivered=total_delivered+1''',
                            (tenant_id, today),
                        )
                    conn.execute(
                        '''INSERT INTO daily_stats (tenant_id, date, total_read) VALUES (?, ?, 1)
                           ON CONFLICT(tenant_id, date) DO UPDATE SET total_read=total_read+1''',
                        (tenant_id, today),
                    )

                if status in ('error', 'failed'):
                    conn.execute(
                        'UPDATE contacts SET is_blocked=1 WHERE phone=? AND tenant_id=?',
                        (phone, tenant_id),
                    )
                    conn.execute(
                        '''INSERT INTO daily_stats (tenant_id, date, total_blocked) VALUES (?, ?, 1)
                           ON CONFLICT(tenant_id, date) DO UPDATE SET total_blocked=total_blocked+1''',
                        (tenant_id, today),
                    )
    except Exception as e:
        logger.error(f"Error updating status: {e}")
    finally:
        conn.close()

def is_first_time(phone, tenant_id=1):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM messages WHERE phone=? AND direction='received' AND tenant_id=?",
            (phone, tenant_id),
        )
        return cur.fetchone() is None
    except Exception as e:
        logger.error(f"Error checking first time: {e}")
        return False
    finally:
        conn.close()

def set_opted_out(phone, opted_out=True, instance_name=None, tenant_id=1):
    """Mark contact as opted out (unsubscribed). If instance_name given, per-instance; else global (contacts.opted_out)."""
    conn = get_db_connection()
    now = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            if instance_name:
                conn.execute('''
                    INSERT INTO contact_opt_out (phone, instance_name, opted_out, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(phone, instance_name) DO UPDATE SET opted_out=?, updated_at=?
                ''', (phone, instance_name, 1 if opted_out else 0, now, 1 if opted_out else 0, now))
            else:
                conn.execute(
                    "UPDATE contacts SET opted_out=? WHERE phone=? AND tenant_id=?",
                    (1 if opted_out else 0, phone, tenant_id),
                )
                if conn.total_changes == 0:
                    conn.execute(
                        "INSERT INTO contacts (tenant_id, phone, opted_out) VALUES (?, ?, ?)",
                        (tenant_id, phone, 1 if opted_out else 0),
                    )
    except Exception as e:
        logger.error(f"Error setting opted_out: {e}")
    finally:
        conn.close()

def is_opted_out(phone, instance_name=None, tenant_id=1):
    """Return True if contact has opted out. If instance_name given, check per-instance; else contacts.opted_out."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if instance_name:
            cur.execute(
                "SELECT opted_out FROM contact_opt_out WHERE phone=? AND instance_name=?",
                (phone, instance_name),
            )
            row = cur.fetchone()
            return bool(row and row[0])
        cur.execute(
            "SELECT opted_out FROM contacts WHERE phone=? AND tenant_id=?",
            (phone, tenant_id),
        )
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception as e:
        logger.error(f"Error checking opted_out: {e}")
        return False
    finally:
        conn.close()

def get_user(user_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()

def get_user_by_username(username):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        return cur.fetchone()
    finally:
        conn.close()

def get_user_by_public_id(public_id):
    if not public_id:
        return None
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE public_id=?", (str(public_id).strip(),))
        return cur.fetchone()
    finally:
        conn.close()

def create_user(username, password, days=365, is_admin=0, assign_default_evolution_instance=True):
    conn = get_db_connection()
    expiry = (get_ist_now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    password_hash = generate_password_hash(password)
    pid = str(uuid.uuid4())
    try:
        with conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO users (username, password_hash, subscription_expiry, is_admin, public_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (username, password_hash, expiry, is_admin, pid))
            uid = cur.lastrowid
            if assign_default_evolution_instance:
                inst = default_instance_name_from_public_id(pid)
                if inst and find_evolution_instance_owner(inst, exclude_user_id=uid) is not None:
                    inst = f"{inst}_{uuid.uuid4().hex[:8]}"
                cur.execute(
                    "UPDATE users SET evolution_instances = ? WHERE id = ?",
                    (inst or "", uid),
                )
            else:
                cur.execute(
                    "UPDATE users SET evolution_instances = ? WHERE id = ?",
                    ("", uid),
                )
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def append_evolution_instance_name(user_id, new_name):
    """
    Add one Evolution instance name to this user's stored list (comma-separated).
    Primary name from public_id is still shown automatically; this is for extra lines only.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        return False, "Instance name is required."
    row = get_user(user_id)
    if not row:
        return False, "User not found."
    existing = parse_evolution_instances(row["evolution_instances"] or "")
    if new_name in existing:
        return True, None
    if public_id_matches_instance_name(row["public_id"] or "", new_name):
        return True, None
    merged = existing + [new_name]
    return set_user_evolution_instances(user_id, ",".join(merged))

def sync_evolution_instances_from_public_id(user_id):
    """Set this user's evolution_instances to the default name derived from their public_id (single instance)."""
    row = get_user(user_id)
    if not row:
        return False, "User not found."
    inst = default_instance_name_from_public_id(row["public_id"] or "")
    if not inst:
        return False, "User has no public_id."
    return set_user_evolution_instances(user_id, inst)

def set_user_evolution_instances(user_id, instances_csv):
    """Comma-separated Evolution instance names for this tenant (e.g. uuidhex, second_line).
    Returns (True, None) on success, or (False, error_message) if a name is already used by another user."""
    uid = int(user_id)
    raw = instances_csv.strip() if instances_csv else ""
    names = parse_evolution_instances(raw)
    for n in names:
        owner = find_evolution_instance_owner(n, exclude_user_id=uid)
        if owner is not None:
            return False, f"Instance name {n!r} is already assigned to another account."
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET evolution_instances = ? WHERE id = ?",
                (raw, uid),
            )
        return True, None
    except Exception as e:
        logger.error(f"set_user_evolution_instances: {e}")
        return False, "Database update failed."
    finally:
        conn.close()

def update_subscription_expiry(username, days):
    conn = get_db_connection()
    expiry = (get_ist_now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            conn.execute("UPDATE users SET subscription_expiry=? WHERE username=?", (expiry, username))
        return True
    except Exception as e:
        logger.error(f"Error updating subscription: {e}")
        return False
    finally:
        conn.close()


def create_campaign_run(tenant_id, campaign_name, total_contacts, instance_mode):
    conn = get_db_connection()
    now = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                '''
                INSERT INTO campaign_runs (
                    tenant_id, campaign_name, status, total_contacts, instance_mode, created_at, started_at
                ) VALUES (?, ?, 'running', ?, ?, ?, ?)
                ''',
                (tenant_id, campaign_name, total_contacts, instance_mode, now, now),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_campaign_run(run_id, tenant_id=None):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        if tenant_id is None:
            cur.execute("SELECT * FROM campaign_runs WHERE id=?", (run_id,))
        else:
            cur.execute("SELECT * FROM campaign_runs WHERE id=? AND tenant_id=?", (run_id, tenant_id))
        return cur.fetchone()
    finally:
        conn.close()


def get_campaign_run_status(run_id):
    row = get_campaign_run(run_id)
    return row["status"] if row else None


def update_campaign_run_progress(run_id, sent_inc=0, failed_inc=0):
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                '''
                UPDATE campaign_runs
                SET sent_count = sent_count + ?,
                    failed_count = failed_count + ?
                WHERE id=?
                ''',
                (sent_inc, failed_inc, run_id),
            )
    finally:
        conn.close()


def request_stop_campaign(run_id, tenant_id):
    conn = get_db_connection()
    now = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                '''
                UPDATE campaign_runs
                SET status='stop_requested', stopped_at=?
                WHERE id=? AND tenant_id=? AND status='running'
                ''',
                (now, run_id, tenant_id),
            )
            if cur.rowcount:
                return True, "Stop requested."
            row = get_campaign_run(run_id, tenant_id)
            if not row:
                return False, "Campaign not found."
            if row["status"] == "stop_requested":
                return True, "Stop already requested."
            return False, f"Campaign is already {row['status']}."
    finally:
        conn.close()


def finish_campaign_run(run_id, status):
    conn = get_db_connection()
    now = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            if status == "stopped":
                conn.execute(
                    '''
                    UPDATE campaign_runs
                    SET status='stopped',
                        stopped_at=COALESCE(stopped_at, ?),
                        completed_at=?
                    WHERE id=? AND status IN ('running', 'stop_requested')
                    ''',
                    (now, now, run_id),
                )
            elif status == "completed":
                conn.execute(
                    '''
                    UPDATE campaign_runs
                    SET status='completed',
                        completed_at=?
                    WHERE id=? AND status IN ('running', 'stop_requested')
                    ''',
                    (now, run_id),
                )
    finally:
        conn.close()
