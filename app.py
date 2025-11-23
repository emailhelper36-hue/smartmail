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

# Import your existing analysis logic
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
    if firebase_admin._apps:
        return firestore.client()

    # Check variables before crashing
    if not os.getenv("FIREBASE_PRIVATE_KEY"):
        logger.warning("⚠️ Firebase variables missing. Running in Memory Mode (Data will vanish on restart).")
        return None

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
        logger.info("✅ Firebase initialized successfully.")
        return firestore.client()
    except Exception as e:
        logger.error(f"❌ Firebase init failed: {e}")
        return None

db = init_firebase()

# Fallback in-memory storage (if Firebase fails)
GLOBAL_STATS = []
GLOBAL_EMAILS = []

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def save_analysis_doc(payload: dict):
    """Saves result to Memory AND Firebase"""
    # Update local memory
    minimal = {
        "tone": payload.get("tone", "Neutral"),
        "urgency": payload.get("urgency", "Low"),
        "summary": payload.get("summary", ""),
    }
    GLOBAL_STATS.append(minimal)
    GLOBAL_EMAILS.insert(0, payload)

    if not db: return

    try:
        message_id = str(payload.get("messageId") or "")
        if message_id:
            doc_ref = db.collection("email_analysis").document(message_id)
        else:
            doc_ref = db.collection("email_analysis").document()

        doc = payload.copy()
        if "createdAt" not in doc:
            doc["createdAt"] = utc_now_iso()
        doc_ref.set(doc, merge=True)
    except Exception as e:
        logger.error(f"Failed to save to Firebase: {e}")

# ------------------------------------------------------------------
# 2. Zoho Mail API Helpers
# ------------------------------------------------------------------

ZOHO_ACCOUNTS_URL = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")
ZOHO_API_DOMAIN = os.environ.get("ZOHO_API_DOMAIN", "https://mail.zoho.com")
ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN")
ZOHO_ACCOUNT_ID = os.environ.get("ZOHO_ACCOUNT_ID")
ZOHO_INBOX_FOLDER_ID = os.environ.get("ZOHO_INBOX_FOLDER_ID")

def get_zoho_access_token() -> str:
    if not (ZOHO_CLIENT_ID and ZOHO_REFRESH_TOKEN):
        raise RuntimeError("❌ Zoho Credentials Missing in .env")

    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }
    url = f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token"
    
    resp = requests.post(url, params=params, timeout=10)
    if resp.status_code != 200:
        logger.error(f"Zoho Token Error: {resp.text}")
        raise RuntimeError("Failed to get Zoho Token")
        
    return resp.json()["access_token"]

def list_inbox_emails(limit: int = 5):
    token = get_zoho_access_token()
    url = f"{ZOHO_API_DOMAIN}/api/accounts/{ZOHO_ACCOUNT_ID}/messages/view"
    params = {"folderId": ZOHO_INBOX_FOLDER_ID, "limit": limit, "sortorder": "false"}
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()

def analyze_zoho_message(message_id: str):
    """Fetches body, analyzes it, and saves result."""
    token = get_zoho_access_token()
    url = f"{ZOHO_API_DOMAIN}/api/accounts/{ZOHO_ACCOUNT_ID}/folders/{ZOHO_INBOX_FOLDER_ID}/messages/{message_id}/content"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    resp = requests.get(url, headers=headers, timeout=10)
    data = resp.json().get("data", {})
    
    subject = data.get("subject", "No Subject")
    body = data.get("content") or ""

    # Strip HTML
    text_content = body
    try:
        if body and "<" in body:
            text_content = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
    except: pass

    full_text = f"{subject}\n\n{text_content}".strip()
    analysis = analyze_text(full_text) # Calls your ML Logic

    doc = {
        "messageId": message_id,
        "subject": subject,
        "summary": analysis.get("summary", ""),
        "tone": analysis.get("tone", "Neutral"),
        "urgency": analysis.get("urgency", "Low"),
        "suggested_reply": analysis.get("suggested_reply", ""),
        "createdAt": utc_now_iso(),
    }
    save_analysis_doc(doc)
    return doc

# ------------------------------------------------------------------
# 3. Bot Helper Functions
# ------------------------------------------------------------------

def get_recent_emails_for_bot():
    """Fetches top 5 emails and formats them as Zoho Buttons"""
    try:
        raw = list_inbox_emails(limit=5)
        messages = raw.get("data") or []
        
        buttons = []
        for msg in messages:
            subject = msg.get("subject", "No Subject")[:35] + "..." 
            msg_id = msg.get("messageId")
            buttons.append({
                "label": subject,
                "type": "+",
                "action": {
                    "type": "invoke.function",
                    "data": {"action_type": "analyze_specific", "msg_id": msg_id}
                }
            })
        return buttons
    except Exception as e:
        logger.error(f"Bot Fetch Error: {e}")
        return []

# ------------------------------------------------------------------
# 4. Routes
# ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/stats")
def stats():
    # Logic to calculate counts for the dashboard
    try:
        recent = []
        total = 0; high_urgency = 0; angry = 0; positive = 0

        if db:
            docs = db.collection("email_analysis").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(50).stream()
            for d in docs:
                item = d.to_dict()
                total += 1
                tone = item.get("tone", "")
                if item.get("urgency") == "High": high_urgency += 1
                if "Angry" in tone: angry += 1
                if "Positive" in tone: positive += 1
                recent.append(item)
        else:
            # Use in-memory fallback
            recent = GLOBAL_EMAILS[:5]
            total = len(GLOBAL_STATS)
        
        return jsonify({
            "total": total,
            "high_urgency": high_urgency,
            "angry": angry,
            "positive": positive,
            "recent": recent[:8] # Send top 8 rows
        })
    except:
        return jsonify({"total": 0, "recent": []})

@app.route("/fetch_zoho_emails", methods=["POST"])
def trigger_sync():
    """Manual Trigger from Dashboard"""
    try:
        raw = list_inbox_emails(limit=10)
        messages = raw.get("data") or []
        count = 0
        
        for msg in messages:
            mid = msg.get("messageId")
            # Check DB to avoid re-analyzing (Credit Saver)
            exists = False
            if db:
                if db.collection("email_analysis").document(mid).get().exists: exists = True
            
            if not exists:
                analyze_zoho_message(mid)
                count += 1
        
        return jsonify({"status": "success", "analyzed": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ------------------------------------------------------------------
# 5. ZOHO BOT ENDPOINT (The Conversation Logic)
# ------------------------------------------------------------------
@app.route("/zoho-bot", methods=["POST"])
def zoho_bot_handler():
    try:
        data = request.get_json(force=True)
        event_data = {}
        if "action" in data and "data" in data["action"]:
            event_data = data["action"]["data"]
        
        action_type = event_data.get("action_type")

        # A. Greeting / Default
        if not action_type:
            buttons = get_recent_emails_for_bot()
            return jsonify({
                "text": "👋 **Hello! I am SmartMail.**",
                "card": {
                    "title": "Inbox Triage",
                    "theme": "modern-inline",
                    "thumbnail": "https://cdn-icons-png.flaticon.com/512/646/646094.png",
                    "rows": [{"label": "Status", "value": "Ready"}, {"label": "Action", "value": "Select email to analyze:"}]
                },
                "buttons": buttons
            })

        # B. Analyze Specific Email
        if action_type == "analyze_specific":
            msg_id = event_data.get("msg_id")
            doc = analyze_zoho_message(msg_id) # Run Analysis Live
            
            return jsonify({
                "text": "✅ **Analysis Complete**",
                "card": {
                    "title": doc.get("subject", "Email Analysis"),
                    "theme": "modern-inline",
                    "rows": [
                        {"label": "📝 Summary", "value": doc.get("summary")},
                        {"label": "❤️ Tone", "value": doc.get("tone")},
                        {"label": "🔥 Urgency", "value": doc.get("urgency")},
                        {"label": "💡 Draft", "value": doc.get("suggested_reply")[:100] + "..."}
                    ]
                },
                "buttons": [
                    {
                        "label": "🔄 Analyze Another?",
                        "type": "+",
                        "action": {"type": "invoke.function", "data": {"action_type": "load_list"}}
                    }
                ]
            })

        # C. Loop Back
        if action_type == "load_list":
            buttons = get_recent_emails_for_bot()
            return jsonify({"text": "🔄 **Fetching latest emails...**", "buttons": buttons})

    except Exception as e:
        logger.error(f"Bot Error: {e}")
        return jsonify({"text": "🤖 System Error. Please try again."})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
