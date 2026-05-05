import os
import io
import uuid
import threading
import requests
import logging
import pandas as pd
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, abort, send_file
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
from contact_scraper_tool import (
    DEFAULT_SCRAPER_CATEGORIES,
    DEFAULT_SCRAPER_LOCATIONS,
    SCRAPER_MAX_RESULTS,
    run_scrape as run_contact_scrape,
    record_to_dict,
)
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
    create_campaign_run,
    get_campaign_run,
    request_stop_campaign,
)

# Flask App
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = './uploads'
app.secret_key = 'super_secret_key'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
SCRAPER_JOBS = {}
SCRAPER_JOBS_LOCK = threading.Lock()

BUSINESS_TEMPLATES = {
    "fashion_boutique": {
        "label": "Fashion Boutique",
        "templates": [
            "Hi {Name}, we have launched fresh arrivals in our latest collection. If you would like to see designs, prices, or available sizes, just reply COLLECTION and we will share the details.",
            "Hello {Name}, sharing an update from our boutique. New styles are now available for this week. Reply PRICE if you want the catalogue and rate details.",
            "Hi {Name}, our new collection is now live with selected premium pieces and limited stock. If you want the latest options, reply SHOW ME and our team will assist you."
        ],
    },
    "salon_spa": {
        "label": "Salon & Spa",
        "templates": [
            "Hi {Name}, this is a quick update from our salon. We have appointments available this week for hair, skin, and beauty services. Reply BOOK to reserve your preferred slot.",
            "Hello {Name}, we are offering professional salon services with limited appointment slots this week. If you would like pricing or a booking, reply APPOINTMENT.",
            "Hi {Name}, your self-care session can be scheduled this week. Reply SERVICE to know our popular salon packages and available timings."
        ],
    },
    "real_estate": {
        "label": "Real Estate",
        "templates": [
            "Hi {Name}, we have new property options available that may match your requirement. Reply DETAILS and we will share location, pricing, and site visit information.",
            "Hello {Name}, this is a professional update regarding new residential and investment properties. If you want the brochure and price range, reply PROPERTY.",
            "Hi {Name}, we are currently showing selected properties in prime locations. Reply VISIT if you would like us to arrange details or a site visit."
        ],
    },
    "restaurant_cafe": {
        "label": "Restaurant & Cafe",
        "templates": [
            "Hi {Name}, we would love to welcome you this week. Our latest menu highlights and chef specials are now available. Reply MENU if you would like us to share them.",
            "Hello {Name}, thank you for staying connected with us. We are taking bookings for dine-in and group orders. Reply BOOK to reserve your table.",
            "Hi {Name}, we have prepared a fresh set of specials for this week. Reply OFFERS if you would like our menu and current recommendations."
        ],
    },
    "education_coaching": {
        "label": "Education & Coaching",
        "templates": [
            "Hi {Name}, admissions and new batches are now open for our upcoming program. Reply DETAILS if you would like course information, fees, and timings.",
            "Hello {Name}, we are starting a new batch soon with focused guidance and limited seats. Reply COURSE to receive the full curriculum and schedule.",
            "Hi {Name}, this is an update from our institute. If you are interested in our upcoming classes, reply ENROLL and we will share complete details professionally."
        ],
    },
    "healthcare_clinic": {
        "label": "Healthcare Clinic",
        "templates": [
            "Hi {Name}, this is a quick update from our clinic. Appointments are available for consultation this week. Reply APPOINTMENT if you would like booking assistance.",
            "Hello {Name}, we are available for consultation and follow-up support. Reply DOCTOR to receive appointment timings and service details.",
            "Hi {Name}, if you would like to schedule a consultation or know our available timings, simply reply BOOK and our team will assist you."
        ],
    },
    "electronics_mobile": {
        "label": "Electronics & Mobile",
        "templates": [
            "Hi {Name}, we have fresh stock and selected offers available on mobile and electronic products. Reply PRICE if you would like the latest list.",
            "Hello {Name}, this is a product update from our store. We can share current models, pricing, and availability. Reply DETAILS to receive the information.",
            "Hi {Name}, we are currently offering selected electronics with updated pricing and stock availability. Reply CATALOGUE if you want the latest options."
        ],
    },
    "jewellery": {
        "label": "Jewellery",
        "templates": [
            "Hi {Name}, our latest jewellery designs are now available. If you would like to see new collections, pricing, or custom options, reply COLLECTION.",
            "Hello {Name}, we are sharing a professional update about our newest jewellery pieces. Reply PRICE for design previews and rate details.",
            "Hi {Name}, we have introduced elegant new designs in our jewellery collection. Reply SHOW ME if you would like our team to share the latest pieces."
        ],
    },
    "gym_fitness": {
        "label": "Gym & Fitness",
        "templates": [
            "Hi {Name}, we are now onboarding new members for our fitness programs. Reply JOIN if you would like membership plans, timings, and coaching details.",
            "Hello {Name}, this is an update from our fitness center. We have flexible plans and guided sessions available. Reply FITNESS to know more.",
            "Hi {Name}, if you are planning to start your fitness journey, we would be happy to assist you with membership and class details. Reply PLAN for more information."
        ],
    },
    "it_services": {
        "label": "IT Services",
        "niches": {
            "restaurant_cafe": {
                "label": "Restaurants & Cafes",
                "journeys": {
                    "cold_outreach": {
                        "label": "Cold Outreach",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, we help restaurants and cafes get more orders through websites, Google visibility, online menu systems, and WhatsApp enquiry automation. If you would like a few ideas for your business, reply YES and I will share the details.",
                                    "Hello {Name}, we work with food businesses to improve online presence, direct orders, and customer follow-up using websites and WhatsApp automation. Reply YES if you would like a quick overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hum restaurants aur cafes ko website, Google visibility, online menu system, aur WhatsApp enquiry automation ke through zyada orders lane mein help karte hain. Agar aap chahein, to main aapke business ke liye kuch ideas share kar sakta hoon. Reply YES.",
                                    "Hello {Name}, hum food businesses ki online presence aur direct orders improve karne mein help karte hain website aur WhatsApp automation ke through. Agar aap short overview chahte hain to reply YES."
                                ],
                            },
                        },
                    },
                    "warm_follow_up": {
                        "label": "Warm Follow-up",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, following up on our earlier conversation. We can help your restaurant improve direct orders and customer follow-up with a better website and WhatsApp automation. If useful, reply YES and I will share a short plan.",
                                    "Hello {Name}, just checking back in. If you are still exploring ways to improve online orders and enquiry handling for your restaurant, reply YES and I will send a practical overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hamari pichhli baat ko follow up kar raha hoon. Hum aapke restaurant ko better website aur WhatsApp automation ke through direct orders badhane mein help kar sakte hain. Agar useful lage to reply YES.",
                                    "Hello {Name}, bas follow up kar raha hoon. Agar aap abhi bhi online orders aur enquiry handling improve karna chahte hain, to reply YES aur main short plan share karunga."
                                ],
                            },
                        },
                    },
                },
            },
            "salon_spa": {
                "label": "Salons & Beauty",
                "journeys": {
                    "cold_outreach": {
                        "label": "Cold Outreach",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, we work with salons and beauty businesses to improve appointments through websites, lead forms, Google Business optimization, and WhatsApp follow-up systems. Reply YES if you would like a quick overview.",
                                    "Hello {Name}, we help salons get more booking enquiries through local SEO, landing pages, and automated WhatsApp follow-ups. Reply YES if you would like a few practical ideas."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hum salons aur beauty businesses ko website, lead forms, Google Business optimization aur WhatsApp follow-up systems ke through zyada appointments lane mein help karte hain. Reply YES agar aap overview chahte hain.",
                                    "Hello {Name}, hum salons ko local SEO, landing pages, aur automated WhatsApp follow-ups ke through zyada booking enquiries dilane mein help karte hain. Agar aap ideas chahte hain to reply YES."
                                ],
                            },
                        },
                    },
                    "warm_follow_up": {
                        "label": "Warm Follow-up",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, following up from our earlier discussion. We can help your salon improve appointment bookings with better online presence and WhatsApp follow-up. Reply YES if you would like a short proposal.",
                                    "Hello {Name}, just checking back in. If you are still looking to improve bookings for your salon, reply YES and I will share a practical overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hamari pichhli discussion ko follow up kar raha hoon. Hum aapke salon ki bookings better online presence aur WhatsApp follow-up ke through improve kar sakte hain. Agar aap chahen to reply YES.",
                                    "Hello {Name}, bas follow up kar raha hoon. Agar aap salon bookings improve karne ke options dekh rahe hain, to reply YES aur main short overview share karunga."
                                ],
                            },
                        },
                    },
                },
            },
            "real_estate": {
                "label": "Real Estate",
                "journeys": {
                    "cold_outreach": {
                        "label": "Cold Outreach",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, we help real estate businesses generate more qualified enquiries through landing pages, CRM-ready lead capture, and WhatsApp automation. Reply YES if you would like to see how this can support your sales process.",
                                    "Hello {Name}, we work with property businesses to improve lead generation through project landing pages, ad-ready funnels, and WhatsApp follow-up systems. Reply YES if you want a short overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hum real estate businesses ko landing pages, CRM-ready lead capture, aur WhatsApp automation ke through zyada qualified enquiries lane mein help karte hain. Agar aap details chahte hain to reply YES.",
                                    "Hello {Name}, hum property businesses ke liye project landing pages aur WhatsApp follow-up systems bana kar lead generation improve karte hain. Agar aap short overview chahte hain to reply YES."
                                ],
                            },
                        },
                    },
                    "warm_follow_up": {
                        "label": "Warm Follow-up",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, following up on our earlier conversation. We can help your real estate business improve enquiry handling with landing pages and WhatsApp lead follow-up. Reply YES if you would like a short proposal.",
                                    "Hello {Name}, just checking back in. If you are still exploring ways to improve property lead flow, reply YES and I will send a practical overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hamari pichhli baat ko follow up kar raha hoon. Hum aapke real estate business ke liye landing pages aur WhatsApp lead follow-up system bana sakte hain. Agar aap chahein to reply YES.",
                                    "Hello {Name}, bas check kar raha hoon. Agar aap abhi bhi property lead flow improve karne ke options dekh rahe hain, to reply YES aur main short overview share karunga."
                                ],
                            },
                        },
                    },
                },
            },
            "healthcare_clinic": {
                "label": "Clinics & Healthcare",
                "journeys": {
                    "cold_outreach": {
                        "label": "Cold Outreach",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, we help clinics and healthcare businesses improve patient enquiries with professional websites, booking workflows, and WhatsApp communication systems. Reply YES if you would like more details.",
                                    "Hello {Name}, we support clinics with appointment-focused websites, local visibility improvements, and automated patient enquiry follow-up on WhatsApp. Reply YES if you would like a quick overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hum clinics aur healthcare businesses ko professional websites, booking workflows, aur WhatsApp communication systems ke through patient enquiries improve karne mein help karte hain. Reply YES agar aap details chahte hain.",
                                    "Hello {Name}, hum clinics ke liye appointment-focused websites aur automated WhatsApp patient follow-up systems bana kar enquiry handling improve karte hain. Agar aap overview chahte hain to reply YES."
                                ],
                            },
                        },
                    },
                    "warm_follow_up": {
                        "label": "Warm Follow-up",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, following up from our earlier discussion. We can help your clinic improve appointment enquiries and patient communication with a stronger website and WhatsApp workflow. Reply YES if you would like a short proposal.",
                                    "Hello {Name}, just checking back in. If you are still exploring ways to streamline clinic enquiries, reply YES and I will share a practical overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hamari pichhli discussion ko follow up kar raha hoon. Hum aapki clinic enquiries aur patient communication ko better website aur WhatsApp workflow ke through improve kar sakte hain. Agar aap chahein to reply YES.",
                                    "Hello {Name}, bas follow up kar raha hoon. Agar aap clinic enquiry system streamline karna chahte hain, to reply YES aur main short overview share karunga."
                                ],
                            },
                        },
                    },
                },
            },
            "education_coaching": {
                "label": "Coaching & Education",
                "journeys": {
                    "cold_outreach": {
                        "label": "Cold Outreach",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, we support coaching institutes and education businesses with admission-focused landing pages, enquiry systems, and WhatsApp follow-up automation. Reply YES if you would like a short overview.",
                                    "Hello {Name}, we help institutes increase admission enquiries through better websites, lead forms, and structured WhatsApp communication. Reply YES if you would like more details."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hum coaching institutes aur education businesses ko admission-focused landing pages, enquiry systems, aur WhatsApp follow-up automation ke through zyada enquiries lane mein help karte hain. Reply YES agar aap overview chahte hain.",
                                    "Hello {Name}, hum institutes ko better websites aur structured WhatsApp communication ke through admission enquiries badhane mein help karte hain. Agar aap details chahte hain to reply YES."
                                ],
                            },
                        },
                    },
                    "warm_follow_up": {
                        "label": "Warm Follow-up",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, following up from our earlier conversation. We can help your institute improve admission enquiries with landing pages and WhatsApp follow-up automation. Reply YES if you would like a short plan.",
                                    "Hello {Name}, just checking back in. If you are still exploring ways to improve student lead conversion, reply YES and I will share a practical overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hamari pichhli baat ko follow up kar raha hoon. Hum aapke institute ke admission enquiries landing pages aur WhatsApp follow-up automation ke through improve kar sakte hain. Agar useful lage to reply YES.",
                                    "Hello {Name}, bas follow up kar raha hoon. Agar aap student leads ko better convert karna chahte hain, to reply YES aur main short overview share karunga."
                                ],
                            },
                        },
                    },
                },
            },
            "retail_fashion": {
                "label": "Retail & Fashion",
                "journeys": {
                    "cold_outreach": {
                        "label": "Cold Outreach",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, we help retail and fashion businesses improve online sales through ecommerce websites, catalogue setup, and customer engagement automation on WhatsApp. Reply YES if you would like to explore this.",
                                    "Hello {Name}, we support stores and boutiques with online catalogue systems, website improvements, and WhatsApp-based customer follow-up. Reply YES if you would like a quick overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hum retail aur fashion businesses ko ecommerce websites, catalogue setup, aur WhatsApp customer engagement automation ke through online sales improve karne mein help karte hain. Reply YES agar aap details chahte hain.",
                                    "Hello {Name}, hum stores aur boutiques ko online catalogue systems aur WhatsApp-based customer follow-up ke saath support karte hain. Agar aap overview chahte hain to reply YES."
                                ],
                            },
                        },
                    },
                    "warm_follow_up": {
                        "label": "Warm Follow-up",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, following up on our earlier discussion. We can help your retail business improve catalogue visibility and customer follow-up through a stronger online setup. Reply YES if you would like a short proposal.",
                                    "Hello {Name}, just checking back in. If you are still exploring ways to improve online sales and customer engagement, reply YES and I will share a practical overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hamari pichhli discussion ko follow up kar raha hoon. Hum aapke retail business ko better catalogue visibility aur customer follow-up ke saath online setup improve karne mein help kar sakte hain. Agar aap chahein to reply YES.",
                                    "Hello {Name}, bas follow up kar raha hoon. Agar aap online sales aur customer engagement improve karna chahte hain, to reply YES aur main short overview share karunga."
                                ],
                            },
                        },
                    },
                },
            },
            "gym_fitness": {
                "label": "Gyms & Fitness",
                "journeys": {
                    "cold_outreach": {
                        "label": "Cold Outreach",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, we help gyms and fitness centers increase membership enquiries with lead-generation pages, local search visibility, and WhatsApp enquiry automation. Reply YES and I will share a few practical ideas.",
                                    "Hello {Name}, we work with gyms to improve membership lead flow through websites, local presence, and automated WhatsApp follow-up. Reply YES if you would like more details."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hum gyms aur fitness centers ko lead-generation pages, local search visibility, aur WhatsApp enquiry automation ke through membership enquiries badhane mein help karte hain. Reply YES agar aap ideas chahte hain.",
                                    "Hello {Name}, hum gyms ke liye websites aur automated WhatsApp follow-up ke saath membership lead flow improve karte hain. Agar aap details chahte hain to reply YES."
                                ],
                            },
                        },
                    },
                    "warm_follow_up": {
                        "label": "Warm Follow-up",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, following up from our earlier conversation. We can help your fitness business improve membership enquiries with a stronger website and WhatsApp follow-up system. Reply YES if you would like a short proposal.",
                                    "Hello {Name}, just checking back in. If you are still looking to improve gym lead conversion, reply YES and I will share a practical overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hamari pichhli baat ko follow up kar raha hoon. Hum aapke fitness business ke membership enquiries ko better website aur WhatsApp follow-up system ke through improve kar sakte hain. Agar useful lage to reply YES.",
                                    "Hello {Name}, bas follow up kar raha hoon. Agar aap gym lead conversion improve karna chahte hain, to reply YES aur main short overview share karunga."
                                ],
                            },
                        },
                    },
                },
            },
            "hotels_travel": {
                "label": "Hotels & Travel",
                "journeys": {
                    "cold_outreach": {
                        "label": "Cold Outreach",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, we help hotels and travel businesses improve direct bookings through better websites, enquiry capture, and WhatsApp-based customer follow-up. Reply YES if you would like more information.",
                                    "Hello {Name}, we support hospitality businesses with booking-focused websites, enquiry funnels, and WhatsApp automation to improve customer response speed. Reply YES if you would like a quick overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hum hotels aur travel businesses ko better websites, enquiry capture, aur WhatsApp-based customer follow-up ke through direct bookings improve karne mein help karte hain. Reply YES agar aap details chahte hain.",
                                    "Hello {Name}, hum hospitality businesses ke liye booking-focused websites aur WhatsApp automation ke through customer response speed improve karte hain. Agar aap overview chahte hain to reply YES."
                                ],
                            },
                        },
                    },
                    "warm_follow_up": {
                        "label": "Warm Follow-up",
                        "languages": {
                            "english": {
                                "label": "English",
                                "templates": [
                                    "Hi {Name}, following up from our earlier conversation. We can help your hotel or travel business improve direct enquiries and bookings with a stronger website and WhatsApp follow-up flow. Reply YES if you would like a short proposal.",
                                    "Hello {Name}, just checking back in. If you are still exploring ways to improve direct bookings, reply YES and I will share a practical overview."
                                ],
                            },
                            "hindi": {
                                "label": "Hindi",
                                "templates": [
                                    "Hi {Name}, hamari pichhli baat ko follow up kar raha hoon. Hum aapke hotel ya travel business ki direct enquiries aur bookings ko better website aur WhatsApp follow-up flow ke through improve kar sakte hain. Reply YES agar aap chahen.",
                                    "Hello {Name}, bas follow up kar raha hoon. Agar aap direct bookings improve karna chahte hain, to reply YES aur main short overview share karunga."
                                ],
                            },
                        },
                    },
                },
            },
        },
    },
}

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
        business_templates=BUSINESS_TEMPLATES,
        instances=pool,
        instance_auto_available=len(pool) > 1,
        user=current_user,
        days_left=max(0, days_left),
    )


@app.route("/tools/contact-scraper", methods=["GET"])
@login_required
def contact_scraper_page():
    return render_template(
        "contact_scraper.html",
        user=current_user,
        days_left=max(0, (datetime.strptime(current_user.subscription_expiry, "%Y-%m-%d %H:%M:%S") - datetime.now()).days),
        locations=DEFAULT_SCRAPER_LOCATIONS,
        categories=DEFAULT_SCRAPER_CATEGORIES,
        max_results_default=min(SCRAPER_MAX_RESULTS, 30),
    )


def _run_scraper_job(job_id, location, category, max_results, headless, only_without_website, debug_website, tenant_id):
    def log_progress(message):
        with SCRAPER_JOBS_LOCK:
            job = SCRAPER_JOBS.get(job_id)
            if not job:
                return
            job["logs"].append(message)
            job["logs"] = job["logs"][-80:]

    def collect_result(record):
        with SCRAPER_JOBS_LOCK:
            job = SCRAPER_JOBS.get(job_id)
            if not job:
                return
            job["results"].append(record_to_dict(record))

    try:
        records = run_contact_scrape(
            location=location,
            category=category,
            max_results=max_results,
            headless=headless,
            on_progress=log_progress,
            on_result=collect_result,
            only_without_website=only_without_website,
            debug_website=debug_website,
        )
        with SCRAPER_JOBS_LOCK:
            job = SCRAPER_JOBS.get(job_id)
            if job:
                job["status"] = "completed"
                job["results"] = [record_to_dict(r) for r in records]
    except Exception as e:
        with SCRAPER_JOBS_LOCK:
            job = SCRAPER_JOBS.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(e)


@app.route("/api/contact-scraper/jobs", methods=["POST"])
@login_required
def start_contact_scraper_job():
    body = request.get_json(silent=True) or {}
    location = (body.get("location") or "").strip()
    category = (body.get("category") or "").strip()
    if not location or not category:
        return jsonify({"error": "location and category are required"}), 400

    try:
        max_results = int(body.get("max_results", min(SCRAPER_MAX_RESULTS, 30)))
    except (TypeError, ValueError):
        max_results = min(SCRAPER_MAX_RESULTS, 30)
    max_results = max(5, min(SCRAPER_MAX_RESULTS, max_results))

    headless = body.get("headless", True) is not False
    only_without_website = body.get("only_without_website", False) in (True, 1, "1", "true", "True", "yes")
    debug_website = body.get("debug_website", False) in (True, 1, "1", "true", "True", "yes")

    job_id = uuid.uuid4().hex
    with SCRAPER_JOBS_LOCK:
        SCRAPER_JOBS[job_id] = {
            "id": job_id,
            "tenant_id": current_user.id,
            "status": "running",
            "location": location,
            "category": category,
            "logs": ["Scrape job created."],
            "results": [],
            "error": "",
        }

    t = threading.Thread(
        target=_run_scraper_job,
        args=(job_id, location, category, max_results, headless, only_without_website, debug_website, current_user.id),
        daemon=True,
    )
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/contact-scraper/jobs/<job_id>", methods=["GET"])
@login_required
def get_contact_scraper_job(job_id):
    with SCRAPER_JOBS_LOCK:
        job = SCRAPER_JOBS.get(job_id)
        if not job or job["tenant_id"] != current_user.id:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(
            {
                "id": job["id"],
                "status": job["status"],
                "location": job["location"],
                "category": job["category"],
                "logs": job["logs"],
                "results": job["results"],
                "error": job["error"],
            }
        )


@app.route("/api/contact-scraper/jobs/<job_id>/download", methods=["GET"])
@login_required
def download_contact_scraper_job(job_id):
    with SCRAPER_JOBS_LOCK:
        job = SCRAPER_JOBS.get(job_id)
        if not job or job["tenant_id"] != current_user.id:
            return jsonify({"error": "Job not found"}), 404
        results = list(job["results"])
        location = job["location"]
        category = job["category"]

    if not results:
        return jsonify({"error": "No results available for this job"}), 400

    df = pd.DataFrame(
        [
            {
                "#": idx + 1,
                "Business Name": row.get("name", ""),
                "Contact Number": row.get("phone", ""),
                "Rating": row.get("rating", ""),
                "Reviews": row.get("reviews", ""),
                "Category": row.get("category", ""),
                "Address": row.get("address", ""),
                "Website": row.get("website", ""),
            }
            for idx, row in enumerate(results)
        ]
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Contacts")
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"contacts_{category.replace(' ', '_')}_{location.replace(' ', '_')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/dashboard-stats", methods=["GET"])
@login_required
def dashboard_stats_api():
    """Live overview numbers for dashboard polling (same logic as page load)."""
    return jsonify(get_overview_stats(current_user.id))


@app.route('/campaigns/<int:campaign_id>/stop', methods=['POST'])
@login_required
def stop_campaign(campaign_id):
    row = get_campaign_run(campaign_id, current_user.id)
    if not row:
        return jsonify({"error": "Campaign not found"}), 404
    ok, message = request_stop_campaign(campaign_id, current_user.id)
    if not ok:
        return jsonify({"error": message}), 400
    return jsonify({"success": True, "message": message})


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
        
    # Media sending is temporarily disabled.
    # Keep these placeholders so we can re-enable video/image/audio/document sends later
    # without changing the bulk sender contract again.
    media_path = None
    media_type = "video"

    # if 'media_file' in request.files and request.files['media_file'].filename != '':
    #     media_file = request.files['media_file']
    #     media_filename = secure_filename(media_file.filename)
    #     media_path = os.path.join(app.config['UPLOAD_FOLDER'], media_filename)
    #     media_file.save(media_path)
    #     ext = media_filename.rsplit('.', 1)[-1].lower()
    #     type_map = {
    #         'mp4': 'video', 'mov': 'video', 'avi': 'video',
    #         'jpg': 'image', 'jpeg': 'image', 'png': 'image', 'gif': 'image',
    #         'mp3': 'audio', 'ogg': 'audio', 'wav': 'audio',
    #         'pdf': 'document', 'docx': 'document', 'xlsx': 'document'
    #     }
    #     media_type = type_map.get(ext, 'video')
    #     logger.info(f"Media file received: {media_filename} ({media_type})")

    campaign_run_id = create_campaign_run(
        current_user.id,
        campaign_name,
        len(contacts),
        "auto" if instance_name == AUTO_INSTANCE else instance_name,
    )

    t = threading.Thread(
        target=process_bulk_campaign,
        args=(campaign_name, contacts, message_template, media_path, media_type),
        kwargs={
            "instance_name": instance_name,
            "tenant_id": current_user.id,
            "evolution_instance_pool": pool,
            "campaign_run_id": campaign_run_id,
        },
        daemon=True,
    )
    t.start()
    
    return jsonify({
        "success": f"Campaign started with {len(contacts)} contacts.",
        "campaign_id": campaign_run_id,
    })

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
