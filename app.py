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
GLOBAL_EMAILS = []

def utc_now_iso(): return datetime.now(timezone.utc).isoformat()

def save_analysis_doc(payload):
    """Save to Firebase (Safely)"""
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
    """Improved email content extraction with better error handling"""
    token = get_zoho_token()
    if not token:
        return "", ""
    
    url = f"{ZOHO_API_DOMAIN}/api/accounts/{os.environ.get('ZOHO_ACCOUNT_ID')}/folders/{os.environ.get('ZOHO_INBOX_FOLDER_ID')}/messages/{msg_id}/content"
    try:
        resp = requests.get(url, headers={"Authorization": f"Zoho-oauthtoken {token}"}, timeout=10)
        data = resp.json()
        
        # Debug logging
        logger.info(f"Zoho API Response for {msg_id}: {data}")
        
        # Extract subject and content with multiple fallbacks
        if "data" in data:
            email_data = data["data"]
            subject = email_data.get("subject", "")
            
            # If subject is empty, try alternative field names
            if not subject:
                subject = email_data.get("subjectSummary", "") or email_data.get("displaySubject", "")
            
            content = email_data.get("content", "") or email_data.get("body", "")
            
            return subject, content
        else:
            logger.error(f"No data in Zoho response: {data}")
            return "", ""
            
    except Exception as e:
        logger.error(f"Error fetching email content: {e}")
        return "", ""

def find_id_by_text(text):
    """Matches button text to Email ID - IMPROVED MATCHING"""
    if not text: return None
    raw = list_inbox_emails(limit=10)
    msgs = raw.get("data") or []
    text_clean = text.rstrip("...").strip().lower()
    
    for m in msgs:
        subject = m.get("subject", "").lower()
        # More flexible matching - check if button text is contained in subject
        if text_clean in subject or subject in text_clean:
            return m.get("messageId")
    return None

def analyze_zoho_msg(mid):
    """Helper to analyze a specific message ID - IMPROVED SUBJECT EXTRACTION"""
    # First get subject from the message list (which we know works)
    raw = list_inbox_emails(limit=10)
    msgs = raw.get("data") or []
    subject_from_list = ""
    for m in msgs:
        if m.get("messageId") == mid:
            subject_from_list = m.get("subject", "")
            break
    
    # Then get content from content API
    subj, body = get_email_content(mid)
    
    # Use subject from list if content API returns empty
    final_subject = subj or subject_from_list or "No Subject"
    
    # Debug logging
    logger.info(f"Analyzing message {mid}: Subject='{final_subject}'")
    
    text_content = body
    try:
        if body and "<" in body:
            text_content = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
    except Exception as e:
        logger.error(f"HTML parsing error: {e}")
    
    full_text = f"{final_subject}\n\n{text_content}".strip()
    res = analyze_text(full_text)
    
    doc = {
        "messageId": mid, 
        "subject": final_subject,
        "summary": res.get("summary"),
        "tone": res.get("tone"), 
        "urgency": res.get("urgency"),
        "suggested_reply": res.get("suggested_reply"), 
        "source": "zoho-mail",
        "analyzedAt": utc_now_iso()
    }
    
    # Additional debug
    logger.info(f"Saving to Firebase: Subject='{final_subject}'")
    
    save_analysis_doc(doc)
    return doc

# ------------------------------------------------------------------
# 3. WEBHOOK (THE FIX IS HERE)
# ------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        logger.info(f"INCOMING: {data}")

        # --- FIX 1: Default to EMPTY STRING, never None ---
        user_text = ""
        
        # 1. Extract User Text Safely
        if "message" in data:
            msg = data["message"]
            if isinstance(msg, dict):
                user_text = msg.get("text", "") # Handle Dictionary
            else:
                user_text = str(msg)
        
        elif "data" in data:
            inner = data["data"]
            user_text = inner.get("message") or inner.get("text") or ""
        
        elif "visitor" in data:
            # This handles the "Trigger" event which crashed before
            user_text = data["visitor"].get("message", "")

        # --- FIX 2: Ensure it's a string before using .lower() ---
        if user_text is None:
            user_text = ""
        else:
            user_text = str(user_text).strip()

        # --- LOGIC ---

        # 1. Dashboard Shortcut
        if user_text.lower() == "dashboard":
            site_url = f"https://{request.host}/"
            return jsonify({
                "replies": [{"text": f"📊 **Command Center:**\n{site_url}"}],
                "suggestions": ["Hi"]
            })

        # 2. Greeting (Handles empty text/start of chat)
        if not user_text or user_text.lower() in ["hi", "hello", "menu", "restart", "start"]:
            raw = list_inbox_emails(limit=5)
            msgs = raw.get("data") or []
            suggestions = [m.get("subject", "No Sub")[:25]+"..." for m in msgs if m.get("subject")]
            
            if not suggestions:
                return jsonify({"replies": [{"text": "⚠️ Connected to Zoho, but Inbox is empty."}]})

            return jsonify({
                "replies": [{"text": "👋 **Connected to Zoho Mail!**\nTap an email below to analyze it:"}],
                "suggestions": suggestions
            })

        # 3. Check Button Click vs Raw Text
        msg_id = find_id_by_text(user_text)

        if msg_id:
            # Case A: Email Selected
            doc = analyze_zoho_msg(msg_id)
            subject_line = doc.get('subject') or "Analysis Complete"
            
            return jsonify({
                "replies": [
                    {"text": f"✅ **{subject_line}**"},
                    {"text": f"📝 **Summary:** {doc.get('summary')}"},
                    {"text": f"❤️ **Tone:** {doc.get('tone')} | 🔥 **Urgency:** {doc.get('urgency')}"},
                    {"text": f"💡 **Draft Reply:**\n{doc.get('suggested_reply')}"}
                ],
                "suggestions": ["Hi", "Dashboard"]
            })
        
        else:
            # Case B: Raw Text
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
    try:
        recent = [x for x in reversed(GLOBAL_EMAILS[-5:])]
        
        # Calculate stats safely
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
                analyze_zoho_msg(mid)
                count += 1
        return jsonify({"status": "success", "analyzed": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
