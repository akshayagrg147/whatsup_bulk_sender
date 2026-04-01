import os
import threading
import requests
import logging
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, abort
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from functools import wraps
from datetime import datetime

from config import *
from excel_parser import parse_contacts
from analytics import get_overview_stats, get_chart_data, get_all_contacts, get_campaigns
from auto_reply import process_webhook
from bulk_sender import process_bulk_campaign, AUTO_INSTANCE
from scheduler import start_scheduler
from database import (
    init_db,
    get_user,
    get_user_by_username,
    get_ist_now,
    parse_evolution_instances,
    get_all_instance_names_from_db,
    set_user_evolution_instances,
    user_instance_display_pool,
    append_evolution_instance_name,
    create_user,
)

# Flask App
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = './uploads'
app.secret_key = 'super_secret_key'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Authentication Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, user_row):
        self.id = user_row['id']
        self.username = user_row['username']
        self.is_admin = bool(user_row['is_admin'])
        self.subscription_expiry = user_row['subscription_expiry']
        try:
            raw = user_row['evolution_instances'] or ''
        except (KeyError, IndexError):
            raw = ''
        self.evolution_instances = parse_evolution_instances(raw)
        try:
            self.public_id = (user_row['public_id'] or '').strip()
        except (KeyError, IndexError):
            self.public_id = ''

    def is_subscription_active(self):
        if not self.subscription_expiry:
            return False
        expiry = datetime.strptime(self.subscription_expiry, "%Y-%m-%d %H:%M:%S")
        return expiry > datetime.now()

@login_manager.user_loader
def load_user(user_id):
    u = get_user(user_id)
    return User(u) if u else None

def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_subscription_active():
            if request.is_json:
                return jsonify({"error": "Subscription expired. Please renew to continue using the service."}), 403
            flash("Your subscription has expired. Please renew to access this feature.")
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = get_user_by_username(username)
        
        if user and check_password_hash(user['password_hash'], password):
            user_obj = User(user)
            login_user(user_obj)
            return redirect(url_for('dashboard'))
        
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/setup/whatsapp')
@login_required
def setup_whatsapp():
    base = WEBHOOK_BASE_URL.rstrip("/")
    names = user_instance_display_pool(current_user.public_id, current_user.evolution_instances)
    if not names:
        names = list(EVOLUTION_INSTANCES)
    urls = [f"{base}/webhook/{name}" for name in names]
    return render_template(
        "setup_whatsapp.html",
        user=current_user,
        evolution_manager_hint=EVOLUTION_BASE_URL.rstrip("/") + "/manager",
        instance_names=names,
        webhook_urls=urls,
    )


@app.route('/admin/users', methods=['POST'])
@login_required
def admin_create_user():
    """Create a user (admin only). JSON: username, password, optional days, is_admin, assign_default_evolution_instance."""
    if not current_user.is_admin:
        abort(403)
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400
    try:
        days = int(body.get("days", 365))
    except (TypeError, ValueError):
        days = 365
    is_admin = 1 if body.get("is_admin") in (True, 1, "1", "true", "True", "yes") else 0
    raw_assign = body.get("assign_default_evolution_instance", True)
    assign_default = not (
        raw_assign is False
        or (isinstance(raw_assign, str) and raw_assign.strip().lower() in ("0", "false", "no"))
    )
    ok = create_user(
        username,
        password,
        days=days,
        is_admin=is_admin,
        assign_default_evolution_instance=assign_default,
    )
    if not ok:
        return jsonify({"error": "Could not create user (username may already exist)."}), 400
    u = get_user_by_username(username)
    if assign_default:
        threading.Thread(target=register_webhook).start()
    return jsonify(
        {
            "success": True,
            "id": u["id"],
            "username": u["username"],
            "public_id": u["public_id"],
            "evolution_instances": u["evolution_instances"] or "",
        }
    )


@app.route('/admin/users/<int:user_id>/evolution-instances', methods=['POST'])
@login_required
def admin_set_evolution_instances(user_id):
    if not current_user.is_admin:
        abort(403)
    body = request.get_json(silent=True) or {}
    csv_val = (body.get("instances") or request.form.get("instances") or "").strip()
    if not csv_val:
        return jsonify({"error": "instances (comma-separated) is required"}), 400
    ok, err_msg = set_user_evolution_instances(user_id, csv_val)
    if not ok:
        return jsonify({"error": err_msg or "Update failed"}), 400
    threading.Thread(target=register_webhook).start()
    return jsonify({"success": True, "instances": parse_evolution_instances(csv_val)})


@app.route('/admin/users/<int:user_id>/evolution-instances/append', methods=['POST'])
@login_required
def admin_append_evolution_instance(user_id):
    """Add a single extra instance name (does not remove existing CSV entries)."""
    if not current_user.is_admin:
        abort(403)
    body = request.get_json(silent=True) or {}
    name = (body.get("instance") or body.get("name") or request.form.get("instance") or "").strip()
    if not name:
        return jsonify({"error": "instance or name (string) is required"}), 400
    ok, err_msg = append_evolution_instance_name(user_id, name)
    if not ok:
        return jsonify({"error": err_msg or "Append failed"}), 400
    threading.Thread(target=register_webhook).start()
    u = get_user(user_id)
    pool = user_instance_display_pool(u["public_id"], parse_evolution_instances(u["evolution_instances"] or ""))
    return jsonify({"success": True, "instances": pool})


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    tid = current_user.id
    stats = get_overview_stats(tid)
    charts = get_chart_data(tid)
    contacts = get_all_contacts(tid)
    campaigns = get_campaigns(tid)
    
    # Calculate days left for subscription
    expiry = datetime.strptime(current_user.subscription_expiry, "%Y-%m-%d %H:%M:%S")
    days_left = (expiry - datetime.now()).days
    
    pool = user_instance_display_pool(current_user.public_id, current_user.evolution_instances)
    if not pool:
        pool = list(EVOLUTION_INSTANCES)
    return render_template(
        "dashboard.html",
        stats=stats,
        charts=charts,
        contacts=contacts,
        campaigns=campaigns,
        instances=pool,
        instance_auto_available=len(pool) > 1,
        user=current_user,
        days_left=max(0, days_left),
    )

@app.route('/upload', methods=['POST'])
@login_required
@subscription_required
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    campaign_name = request.form.get('campaign_name', 'Campaign_1')
    message_template = request.form.get('message_template', '')
    instance_name = request.form.get('instance_name', '').strip() or None  # __auto__ = rotate when 200/day

    if not message_template:
        return jsonify({"error": "Message template is required"}), 400

    pool = user_instance_display_pool(current_user.public_id, current_user.evolution_instances)
    if not pool:
        pool = list(EVOLUTION_INSTANCES)
    if not pool:
        return jsonify({"error": "No WhatsApp instances assigned. Ask admin to set your Evolution instance names."}), 400

    if len(pool) <= 1:
        instance_name = pool[0]
    elif instance_name in (None, "", AUTO_INSTANCE):
        instance_name = AUTO_INSTANCE
    elif instance_name not in pool:
        return jsonify({"error": "Invalid WhatsApp instance for your account."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    contacts, err = parse_contacts(filepath)
    if err:
        return jsonify({"error": err}), 400
        
    # Handle optional media file (video/image/audio/document)
    media_path = None
    media_type = "video"
    if 'media_file' in request.files and request.files['media_file'].filename != '':
        media_file = request.files['media_file']
        media_filename = secure_filename(media_file.filename)
        media_path = os.path.join(app.config['UPLOAD_FOLDER'], media_filename)
        media_file.save(media_path)
        # Auto-detect media type from extension
        ext = media_filename.rsplit('.', 1)[-1].lower()
        type_map = {
            'mp4': 'video', 'mov': 'video', 'avi': 'video',
            'jpg': 'image', 'jpeg': 'image', 'png': 'image', 'gif': 'image',
            'mp3': 'audio', 'ogg': 'audio', 'wav': 'audio',
            'pdf': 'document', 'docx': 'document', 'xlsx': 'document'
        }
        media_type = type_map.get(ext, 'video')
        logger.info(f"Media file received: {media_filename} ({media_type})")

    t = threading.Thread(
        target=process_bulk_campaign,
        args=(campaign_name, contacts, message_template, media_path, media_type),
        kwargs={
            "instance_name": instance_name,
            "tenant_id": current_user.id,
            "evolution_instance_pool": pool,
        },
    )
    t.start()
    
    media_info = f" + {media_type} media" if media_path else ""
    return jsonify({"success": f"Campaign started with {len(contacts)} contacts{media_info}."})

@app.route('/webhook', methods=['POST'])
@app.route('/webhook/<instance_name>', methods=['POST'])
def webhook(instance_name=None):
    data = request.json or {}
    try:
        process_webhook(data, instance_name=instance_name)
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
    return jsonify({"status": "ok"}), 200

def register_webhook():
    import time
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    base = WEBHOOK_BASE_URL.rstrip("/")
    db_instances = get_all_instance_names_from_db()
    all_instances = sorted(set(EVOLUTION_INSTANCES) | db_instances)
    for instance in all_instances:
        try:
            r = requests.get(f"{EVOLUTION_BASE_URL}/instance/connectionState/{instance}", headers={"apikey": EVOLUTION_API_KEY}, timeout=5)
            if r.status_code == 404:
                logger.info(f"Instance '{instance}' not found. Creating...")
                create_res = requests.post(f"{EVOLUTION_BASE_URL}/instance/create", headers=headers, json={
                    "instanceName": instance,
                    "integration": "WHATSAPP-BAILEYS",
                    "qrcode": True
                }, timeout=10)
                logger.info(f"Instance create {instance}: {create_res.status_code} {create_res.text[:200]}")
                time.sleep(3)
            webhook_payload = {
                "enabled": True,
                "url": f"{base}/webhook/{instance}",
                "webhook_by_events": False,
                "webhook_base64": False,
                "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE"]
            }
            res = requests.post(f"{EVOLUTION_BASE_URL}/webhook/set/{instance}", json=webhook_payload, headers=headers, timeout=10)
            if res.status_code in [200, 201]:
                logger.info(f"✅ Webhook registered for instance: {instance}")
            else:
                logger.warning(f"Webhook {instance}: {res.status_code} {res.text[:200]}")
        except Exception as e:
            logger.error(f"Could not register webhook for {instance}: {e}")


if __name__ == '__main__':
    print("Initializing Database...")
    init_db()
    
    print("Starting Scheduler...")
    start_scheduler()
    
    print("Registering Webhook...")
    threading.Thread(target=register_webhook).start()
    
    print(f"✅ Evolution API: {EVOLUTION_BASE_URL}")
    print(f"✅ Dashboard: http://localhost:{FLASK_PORT}/dashboard")
    print(f"✅ Webhook registered internally on /webhook")
    print(f"✅ Scheduler running")
    print("🚀 WhatsApp Marketing Suite is LIVE!")
    
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False)
