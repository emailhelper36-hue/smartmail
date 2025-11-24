# --- LOAD ENV FIRST ---
from dotenv import load_dotenv
load_dotenv() 

import os
import json
import logging
import traceback
from datetime import datetime, timezone

import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from bs4 import BeautifulSoup  

from analyze import analyze_text
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__, template_folder="templates")
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = app.logger

# ------------------------------------------------------------------
# 1. Firebase Setup
# ------------------------------------------------------------------
def init_firebase():
    if firebase_admin._apps: return firestore.client()
    if not os.getenv("FIREBASE_PRIVATE_KEY"): return None
    try:
        cred_data = {
            "type": os.getenv("FIREBASE_TYPE"),
            "project_id": os.getenv("FIREBASE_PROJECT_ID"),
            "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
            "private_key": os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
            "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
            "client_id": os.getenv("FIREBASE_CLIENT_ID"),
            "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
            "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
            "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_CERT_URL"),
            "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_CERT_URL"),
        }
        cred = credentials.Certificate(cred_data)
        firebase_admin.initialize_app(cred)
        return firestore.client()
    except: return None

db = init_firebase()
GLOBAL_STATS = [] 
GLOBAL_EMAILS = []

def utc_now_iso(): return datetime.now(timezone.utc).isoformat()

def save_analysis_doc(payload):
    try:
        GLOBAL_STATS.append({"tone": payload.get("tone"), "urgency": payload.get("urgency")})
        GLOBAL_EMAILS.insert(0, payload)
        if db:
            doc_id = str(payload.get("messageId") or "")
            col = db.collection("email_analysis")
            doc_ref = col.document(doc_id) if doc_id else col.document()
            payload["createdAt"] = utc_now_iso()
            doc_ref.set(payload, merge=True)
    except Exception as e:
        logger.error(f"Firebase Error: {e}")

# ------------------------------------------------------------------
# 2. SIMPLIFIED ZOHO MAIL API (FIXED VERSION)
# ------------------------------------------------------------------
def get_zoho_token():
    try:
        url = "https://accounts.zoho.com/oauth/v2/token"
        params = {
            "refresh_token": os.environ.get("ZOHO_REFRESH_TOKEN"),
            "client_id": os.environ.get("ZOHO_CLIENT_ID"),
            "client_secret": os.environ.get("ZOHO_CLIENT_SECRET"),
            "grant_type": "refresh_token",
        }
        resp = requests.post(url, params=params, timeout=10)
        logger.info(f"🔑 Token response: {resp.status_code}")
        if resp.status_code != 200:
            logger.error(f"❌ Token error: {resp.text}")
        return resp.json().get("access_token")
    except Exception as e:
        logger.error(f"❌ Token exception: {e}")
        return None

def list_inbox_emails(limit=5):
    logger.info("📧 Attempting to fetch emails from Zoho...")
    token = get_zoho_token()
    if not token: 
        logger.error("❌ No token available")
        return {"data": []}
    
    account_id = os.environ.get('ZOHO_ACCOUNT_ID')
    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages/view"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    params = {"limit": limit}
    
    try:
        logger.info(f"🔍 API call: {url}")
        response = requests.get(url, headers=headers, params=params, timeout=15)
        logger.info(f"📧 API response: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            messages = data.get("data", [])
            logger.info(f"✅ Found {len(messages)} emails")
            for msg in messages:
                logger.info(f"   - {msg.get('subject', 'No subject')}")
            return data
        else:
            logger.error(f"❌ API error {response.status_code}: {response.text}")
            return {"data": []}
    except Exception as e:
        logger.error(f"❌ API exception: {e}")
        return {"data": []}

# ------------------------------------------------------------------
# 3. WEBHOOK (SIMPLIFIED)
# ------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        logger.info(f"INCOMING: {data}")

        user_text = ""
        if "message" in data:
            msg = data["message"]
            if isinstance(msg, dict):
                user_text = msg.get("text", "")
            else:
                user_text = str(msg)
        elif "data" in data:
            inner = data["data"]
            user_text = inner.get("message") or inner.get("text") or ""
        elif "visitor" in data:
            user_text = data["visitor"].get("message", "")

        if user_text is None:
            user_text = ""
        else:
            user_text = str(user_text).strip()

        # 1. Dashboard Shortcut
        if user_text.lower() == "dashboard":
            site_url = f"https://{request.host}/"
            return jsonify({
                "replies": [{"text": f"📊 **Command Center:**\n{site_url}"}],
                "suggestions": ["Hi"]
            })

        # 2. Greeting
        if not user_text or user_text.lower() in ["hi", "hello", "menu", "restart", "start"]:
            raw = list_inbox_emails(limit=5)
            msgs = raw.get("data") or []
            suggestions = [m.get("subject", "No Sub")[:25]+"..." for m in msgs if m.get("subject")]
            
            if not suggestions:
                return jsonify({
                    "replies": [{
                        "text": "🔧 Zoho connection issue. But you can still analyze text directly! Try typing any email content."
                    }],
                    "suggestions": ["Test: Urgent security update", "Test: Happy customer feedback"]
                })

            return jsonify({
                "replies": [{"text": "👋 **Connected to Zoho Mail!**\nTap an email below to analyze it:"}],
                "suggestions": suggestions
            })

        # 3. Direct text analysis (fallback)
        res = analyze_text(user_text)
        return jsonify({
            "replies": [
                {"text": f"📝 **Summary:** {res.get('summary')}"},
                {"text": f"❤️ **Tone:** {res.get('tone')} | 🔥 **Urgency:** {res.get('urgency')}"},
                {"text": f"💡 **Draft Reply:**\n{res.get('suggested_reply')}"}
            ],
            "suggestions": ["Hi"]
        })

    except Exception as e:
        logger.error(f"CRITICAL ERROR: {traceback.format_exc()}")
        return jsonify({"replies": [{"text": "⚠️ Error processing request. Check server logs."}]})

# --- DASHBOARD ROUTES ---
@app.route("/")
def index(): return render_template("index.html")

@app.route("/mails")
def mails_page(): return render_template("mails.html")

@app.route("/api/stats")
def stats():
    try:
        recent = [x for x in reversed(GLOBAL_EMAILS[-5:])]
        total = len(GLOBAL_EMAILS)
        urgent = sum(1 for x in GLOBAL_STATS if str(x.get('urgency')) == 'High')
        angry = sum(1 for x in GLOBAL_STATS if 'Angry' in str(x.get('tone', '')))
        positive = sum(1 for x in GLOBAL_STATS if 'Positive' in str(x.get('tone', '')))
        neutral = total - angry - positive
        
        return jsonify({
            "total": total, 
            "high_urgency": urgent, 
            "angry": angry,
            "positive": positive,
            "neutral": neutral,
            "recent": recent
        })
    except Exception as e:
        logger.error(f"Stats Error: {e}")
        return jsonify({"total": 0, "high_urgency": 0, "angry": 0, "positive": 0, "neutral": 0, "recent": []})

@app.route("/fetch_zoho_emails", methods=["POST"])
def trigger_sync():
    try:
        raw = list_inbox_emails(limit=10)
        messages = raw.get("data") or []
        count = 0
        for msg in messages:
            mid = msg.get("messageId")
            if not any(e.get('messageId') == mid for e in GLOBAL_EMAILS):
                # Simple analysis without complex Zoho content API
                subject = msg.get("subject", "No Subject")
                res = analyze_text(subject)
                doc = {
                    "messageId": mid, 
                    "subject": subject,
                    "summary": res.get("summary"),
                    "tone": res.get("tone"), 
                    "urgency": res.get("urgency"),
                    "suggested_reply": res.get("suggested_reply"), 
                    "source": "zoho-mail",
                    "analyzedAt": utc_now_iso()
                }
                save_analysis_doc(doc)
                count += 1
        return jsonify({"status": "success", "analyzed": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("🚀 Server starting with enhanced Zoho debugging...")
    app.run(host="0.0.0.0", port=port)
