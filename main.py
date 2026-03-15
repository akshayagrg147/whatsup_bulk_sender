import os
import threading
import requests
import logging
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename

from config import *
from database import init_db
from excel_parser import parse_contacts
from analytics import get_overview_stats, get_chart_data, get_all_contacts, get_campaigns
from auto_reply import process_webhook
from bulk_sender import process_bulk_campaign
from scheduler import start_scheduler

# Flask App
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = './uploads'
app.secret_key = 'super_secret_key'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@app.route('/dashboard', methods=['GET'])
def dashboard():
    stats = get_overview_stats()
    charts = get_chart_data()
    contacts = get_all_contacts()
    campaigns = get_campaigns()
    return render_template('dashboard.html', stats=stats, charts=charts, contacts=contacts, campaigns=campaigns)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    campaign_name = request.form.get('campaign_name', 'Campaign_1')
    message_template = request.form.get('message_template', '')
    
    if not message_template:
        return jsonify({"error": "Message template is required"}), 400
        
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

    t = threading.Thread(target=process_bulk_campaign, args=(campaign_name, contacts, message_template, media_path, media_type))
    t.start()
    
    media_info = f" + {media_type} media" if media_path else ""
    return jsonify({"success": f"Campaign started with {len(contacts)} contacts{media_info}."})

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    try:
        process_webhook(data)
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
    return jsonify({"status": "ok"}), 200

def register_webhook():
    import time
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    webhook_payload = {
        "webhook": {
            "enabled": True,
            "url": f"http://host.docker.internal:{FLASK_PORT}/webhook",
            "byEvents": False,
            "base64": False,
            "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE"]
        }
    }
    try:
        r = requests.get(f"{EVOLUTION_BASE_URL}/instance/connectionState/{EVOLUTION_INSTANCE}", headers={"apikey": EVOLUTION_API_KEY}, timeout=5)
        if r.status_code == 404:
            logger.info("Instance not found. Creating Evolution instance...")
            create_res = requests.post(f"{EVOLUTION_BASE_URL}/instance/create", headers=headers, json={
                "instanceName": EVOLUTION_INSTANCE,
                "integration": "WHATSAPP-BAILEYS",
                "qrcode": True
            }, timeout=10)
            logger.info(f"Instance create response: {create_res.status_code} {create_res.text[:200]}")
            time.sleep(3)  # Wait for instance to initialize before registering webhook

        # Register webhook using Evolution API v2 format
        webhook_url = f"{EVOLUTION_BASE_URL}/webhook/set/{EVOLUTION_INSTANCE}"
        res = requests.post(webhook_url, json=webhook_payload, headers=headers, timeout=10)
        if res.status_code in [200, 201]:
            logger.info("✅ Webhook registered successfully with Evolution API.")
        else:
            logger.warning(f"Failed to register webhook. Code: {res.status_code}, Res: {res.text}")
    except Exception as e:
        logger.error(f"Could not connect to Evolution API to register webhook: {e}")


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
