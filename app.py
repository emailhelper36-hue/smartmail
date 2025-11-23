# --- FIX 1: Load .env BEFORE importing anything else ---
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

# Now safe to import analyze because .env is loaded
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

    if not os.getenv("FIREBASE_PRIVATE_KEY"):
        logger.warning("⚠️ Firebase variables missing. Running in Memory Mode.")
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
        logger.info("Firebase initialized successfully.")
        return firestore.client()
    except Exception as e:
        logger.error(f"Firebase initialization failed: {e}")
        return None

db = init_firebase()
GLOBAL_STATS = []
GLOBAL_EMAILS = []

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def save_analysis_doc(payload: dict):
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
    if not (ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET and ZOHO_REFRESH_TOKEN):
        return ""

    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }
    url = f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token"
    
    try:
        resp = requests.post(url, params=params, timeout=15)
        if resp.status_code != 200: return ""
        return resp.json().get("access_token", "")
    except: return ""

def list_inbox_emails(limit: int = 10):
    token = get_zoho_access_token()
    if not token: return {}

    url = f"{ZOHO_API_DOMAIN}/api/accounts/{ZOHO_ACCOUNT_ID}/messages/view"
    params = {"folderId": ZOHO_INBOX_FOLDER_ID, "limit": limit, "sortorder": "false"}
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        return resp.json()
    except: return {}

def get_email_content(message_id: str):
    token = get_zoho_access_token()
    if not token: return "", "", ""
    url = f"{ZOHO_API_DOMAIN}/api/accounts/{ZOHO_ACCOUNT_ID}/folders/{ZOHO_INBOX_FOLDER_ID}/messages/{message_id}/content"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        data = resp.json().get("data", {})
        subject = data.get("subject", "")
        from_address = data.get("fromAddress", "")
        body = data.get("content") or data.get("body") or ""
        return subject, from_address, body
    except: return "Error", "Error", ""

def analyze_zoho_message(message_id: str):
    subject, from_addr, body = get_email_content(message_id)
    text_content = body
    try:
        if body and "<" in body:
            text_content = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
    except: pass

    full_text = f"{subject}\n\n{text_content}".strip()
    analysis = analyze_text(full_text)

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
# 3. Helper to Find Email ID by Subject (For SalesIQ)
# ------------------------------------------------------------------
def find_id_by_subject(subject_text):
    """Since SalesIQ only sends back the text clicked, we match it to an ID"""
    raw = list_inbox_emails(limit=10)
    messages = raw.get("data") or []
    for msg in messages:
        if msg.get("subject") == subject_text:
            return msg.get("messageId")
    return None

# ------------------------------------------------------------------
# 4. Routes
# ------------------------------------------------------------------

@app.route("/")
def index(): return render_template("index.html")

@app.route("/mails")
def mails_page(): return render_template("mails.html")

@app.route("/api/stats")
def stats():
    try:
        recent = []
        total = 0; high_urgency = 0; angry = 0; positive = 0
        if db:
            docs = db.collection("email_analysis").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(50).stream()
            for d in docs:
                item = d.to_dict()
                total += 1
                if item.get("urgency") == "High": high_urgency += 1
                if "Angry" in item.get("tone", ""): angry += 1
                recent.append(item)
        return jsonify({"total": total, "high_urgency": high_urgency, "angry": angry, "recent": recent[:5]})
    except: return jsonify({"total": 0, "recent": []})

@app.route("/fetch_zoho_emails", methods=["POST"])
def trigger_sync():
    try:
        raw = list_inbox_emails(limit=10)
        messages = raw.get("data") or []
        count = 0
        for msg in messages:
            mid = msg.get("messageId")
            analyze_zoho_message(mid)
            count += 1
        return jsonify({"status": "success", "analyzed": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# --- SALESIQ WEBHOOK (UPDATED FOR EMAIL FETCHING) ---
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        user_text = ""
        
        # Extract text
        if isinstance(data, dict):
            if "message" in data: user_text = data["message"]
            elif "data" in data: user_text = data["data"].get("message") or data["data"].get("text")
            elif "visitor" in data: user_text = data["visitor"].get("message")
        
        # --- SCENARIO 1: Greeting -> Show Email List ---
        # If user says "Hi", "Hello", "Restart", or empty
        if not user_text or user_text.lower().strip() in ["hi", "hello", "start", "menu"]:
            raw = list_inbox_emails(limit=5)
            messages = raw.get("data") or []
            
            # Create Suggestions (Clickable pills)
            suggestions = []
            for m in messages:
                subj = m.get("subject", "No Subject")
                if subj: suggestions.append(subj) # Only add valid subjects
            
            if not suggestions:
                return jsonify({"replies": [{"text": "⚠️ No emails found in Zoho Inbox."}]})

            return jsonify({
                "replies": [{"text": "👋 **Connected to Zoho Mail!**\nTap an email below to analyze it:"}],
                "suggestions": suggestions
            })

        # --- SCENARIO 2: User clicked a Subject -> Analyze IT ---
        # Check if the text matches a real email subject
        msg_id = find_id_by_subject(user_text)
        
        if msg_id:
            # User selected an email! Analyze it.
            doc = analyze_zoho_message(msg_id)
            return jsonify({
                "replies": [
                    {"text": f"✅ **Analysis: {doc.get('subject')}**"},
                    {"text": f"📝 **Summary:** {doc.get('summary')}"},
                    {"text": f"❤️ **Tone:** {doc.get('tone')} | 🔥 **Urgency:** {doc.get('urgency')}"},
                    {"text": f"💡 **Draft Reply:**\n{doc.get('suggested_reply')}"}
                ],
                "suggestions": ["Hi", "Dashboard"] # Options to go back
            })

        # --- SCENARIO 3: Fallback (Raw Text Analysis) ---
        # If it's not "Hi" and not an Email Subject, treat as raw text
        result = analyze_text(user_text)
        return jsonify({
            "replies": [
                {"text": f"📝 **Summary:** {result.get('summary')}"},
                {"text": f"💡 **Draft:** {result.get('suggested_reply')}"}
            ],
            "suggestions": ["Hi"]
        })

    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        return jsonify({"replies": [{"text": "⚠️ Error processing request."}]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
