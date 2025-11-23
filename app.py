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

# Ensure analyze.py and utils.py are in your folder!
from analyze import analyze_text
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__, template_folder="templates")
CORS(app)

# Enable Debug Logging
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

def utc_now_iso(): return datetime.now(timezone.utc).isoformat()

def save_analysis_doc(payload):
    """Save to Firebase (Safely)"""
    try:
        GLOBAL_STATS.append({"tone": payload.get("tone"), "urgency": payload.get("urgency")})
        if db:
            doc_id = str(payload.get("messageId") or "")
            col = db.collection("email_analysis")
            doc_ref = col.document(doc_id) if doc_id else col.document()
            payload["createdAt"] = utc_now_iso()
            doc_ref.set(payload, merge=True)
    except Exception as e:
        logger.error(f"Firebase Error: {e}")

# ------------------------------------------------------------------
# 2. Zoho Mail API
# ------------------------------------------------------------------
ZOHO_ACCOUNTS_URL = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")
ZOHO_API_DOMAIN = os.environ.get("ZOHO_API_DOMAIN", "https://mail.zoho.com")

def get_zoho_token():
    try:
        url = f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token"
        params = {
            "refresh_token": os.environ.get("ZOHO_REFRESH_TOKEN"),
            "client_id": os.environ.get("ZOHO_CLIENT_ID"),
            "client_secret": os.environ.get("ZOHO_CLIENT_SECRET"),
            "grant_type": "refresh_token",
        }
        resp = requests.post(url, params=params, timeout=5)
        return resp.json().get("access_token")
    except: return None

def list_inbox_emails(limit=5):
    token = get_zoho_token()
    if not token: return {}
    url = f"{ZOHO_API_DOMAIN}/api/accounts/{os.environ.get('ZOHO_ACCOUNT_ID')}/messages/view"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    params = {"folderId": os.environ.get("ZOHO_INBOX_FOLDER_ID"), "limit": limit, "sortorder": "false"}
    try:
        return requests.get(url, headers=headers, params=params, timeout=10).json()
    except: return {}

def get_email_content(msg_id):
    token = get_zoho_token()
    url = f"{ZOHO_API_DOMAIN}/api/accounts/{os.environ.get('ZOHO_ACCOUNT_ID')}/folders/{os.environ.get('ZOHO_INBOX_FOLDER_ID')}/messages/{msg_id}/content"
    try:
        resp = requests.get(url, headers={"Authorization": f"Zoho-oauthtoken {token}"}, timeout=10)
        d = resp.json().get("data", {})
        return d.get("subject", ""), d.get("content", "")
    except: return "", ""

# --- FIXED FUNCTION NAME HERE ---
def find_id_by_text(text):
    """Matches button text to Email ID"""
    raw = list_inbox_emails(limit=8)
    msgs = raw.get("data") or []
    for m in msgs:
        subject = m.get("subject", "")
        # Check exact match or if subject starts with the button text (handling "..." truncation)
        if text == subject or subject.startswith(text.rstrip("...")):
            return m.get("messageId")
    return None

def analyze_zoho_msg(mid):
    """Helper to analyze a specific message ID"""
    subj, body = get_email_content(mid)
    text_content = body
    try:
        if body and "<" in body:
            text_content = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
    except: pass
    
    full_text = f"{subj}\n\n{text_content}".strip()
    res = analyze_text(full_text)
    
    doc = {
        "messageId": mid, "subject": subj, "summary": res.get("summary"),
        "tone": res.get("tone"), "urgency": res.get("urgency"),
        "suggested_reply": res.get("suggested_reply"), "source": "zoho-mail"
    }
    save_analysis_doc(doc)
    return doc

# ------------------------------------------------------------------
# 3. WEBHOOK (The Logic)
# ------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        logger.info(f"INCOMING: {data}")

        # 1. Extract User Text
        user_text = ""
        if isinstance(data, dict):
            if "message" in data: user_text = data["message"]
            elif "data" in data: user_text = data["data"].get("message") or data["data"].get("text")
            elif "visitor" in data: user_text = data["visitor"].get("message")

        # 2. GREETING or RESET
        if not user_text or user_text.lower().strip() in ["hi", "hello", "menu", "restart"]:
            raw = list_inbox_emails(limit=5)
            msgs = raw.get("data") or []
            suggestions = [m.get("subject")[:30]+"..." for m in msgs if m.get("subject")]
            
            if not suggestions:
                return jsonify({"replies": [{"text": "⚠️ No emails found in Zoho Inbox."}]})

            return jsonify({
                "replies": [{"text": "👋 **Connected!** Tap an email below to analyze:"}],
                "suggestions": suggestions
            })

        # 3. CHECK IF USER CLICKED AN EMAIL SUBJECT
        # (This uses the FIXED function name)
        msg_id = find_id_by_text(user_text)

        if msg_id:
            # Case A: It is an email subject -> Analyze that Email
            doc = analyze_zoho_msg(msg_id)
            return jsonify({
                "replies": [
                    {"text": f"✅ **Analysis: {doc.get('subject')}**"},
                    {"text": f"📝 **Summary:** {doc.get('summary')}"},
                    {"text": f"❤️ **Tone:** {doc.get('tone')} | 🔥 **Urgency:** {doc.get('urgency')}"},
                    {"text": f"💡 **Draft Reply:**\n{doc.get('suggested_reply')}"}
                ],
                "suggestions": ["Hi", "Dashboard"]
            })
        
        else:
            # Case B: It is raw text -> Analyze the text directly
            res = analyze_text(user_text)
            return jsonify({
                "replies": [
                    {"text": f"📝 **Summary:** {res.get('summary')}"},
                    {"text": f"💡 **Draft:** {res.get('suggested_reply')}"}
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
    # Simplified stats for dashboard
    try:
        recent = [x for x in reversed(GLOBAL_EMAILS[-5:])]
        return jsonify({
            "total": len(GLOBAL_EMAILS), 
            "high_urgency": sum(1 for x in GLOBAL_STATS if x['urgency']=='High'),
            "angry": sum(1 for x in GLOBAL_STATS if 'Angry' in x['tone']),
            "recent": recent
        })
    except: return jsonify({})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
