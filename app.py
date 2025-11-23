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

    # We check for a few critical vars to decide if we should try init
    required_vars = ["FIREBASE_PRIVATE_KEY", "FIREBASE_CLIENT_EMAIL"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        logger.warning(f"Missing Firebase variables: {missing}. Firebase disabled (using in-memory storage).")
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

# Fallback in-memory storage
GLOBAL_STATS = []
GLOBAL_EMAILS = []

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def save_analysis_doc(payload: dict):
    # Update local memory
    minimal = {
        "tone": payload.get("tone", "Neutral"),
        "urgency": payload.get("urgency", "Low"),
        "summary": payload.get("summary", ""),
    }
    GLOBAL_STATS.append(minimal)
    GLOBAL_EMAILS.insert(0, payload)

    if not db: return

    # Update Firebase
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
# 2. Zoho Mail API Helpers (US SERVER .COM)
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
        # Only raise error if we actually try to use Zoho Mail features
        # raise RuntimeError("Zoho OAuth env vars are not fully configured.")
        logger.warning("Zoho OAuth env vars missing. Mail sync will fail.")
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
        if resp.status_code != 200:
            print(f"\n❌ ZOHO AUTH ERROR ({resp.status_code}): {resp.text}\n")
        resp.raise_for_status()
        return resp.json()["access_token"]
    except Exception as e:
        logger.error(f"Failed to get access token: {e}")
        return ""

def list_inbox_emails(limit: int = 20):
    if not (ZOHO_ACCOUNT_ID and ZOHO_INBOX_FOLDER_ID):
        logger.warning("ZOHO_ACCOUNT_ID or ZOHO_INBOX_FOLDER_ID not set.")
        return {}

    token = get_zoho_access_token()
    if not token: return {}

    url = f"{ZOHO_API_DOMAIN}/api/accounts/{ZOHO_ACCOUNT_ID}/messages/view"
    params = {
        "folderId": ZOHO_INBOX_FOLDER_ID,
        "limit": limit,
        "start": 1,
        "status": "all",
        "sortBy": "date",
        "sortorder": "false",
    }
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Error listing emails: {e}")
        return {}

def get_email_content(message_id: str):
    token = get_zoho_access_token()
    if not token: return "", "", ""
    
    url = (
        f"{ZOHO_API_DOMAIN}/api/accounts/{ZOHO_ACCOUNT_ID}/"
        f"folders/{ZOHO_INBOX_FOLDER_ID}/messages/{message_id}/content"
    )
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        
        json_response = resp.json()
        # Handle 'data' wrapper if present
        data = json_response.get("data", json_response)

        subject = data.get("subject", "")
        from_address = data.get("fromAddress", "")
        # Check multiple fields for body content
        body = data.get("content") or data.get("body") or data.get("contentText") or ""
        
        return subject, from_address, body
    except Exception as e:
        logger.error(f"Error getting content for {message_id}: {e}")
        return "Error", "Error", ""

def analyze_zoho_message(message_id: str):
    subject, from_addr, body = get_email_content(message_id)

    # Clean HTML tags
    text_content = body
    try:
        if body and ("<html" in body or "<div" in body or "<p>" in body):
            soup = BeautifulSoup(body, "html.parser")
            text_content = soup.get_text(separator=" ", strip=True)
    except:
        pass

    full_text = f"{subject}\n\n{text_content}".strip()
    analysis = analyze_text(full_text)

    doc = {
        "messageId": message_id,
        "subject": subject,
        "from": from_addr,
        "body": body,  # Store original HTML for display
        "summary": analysis.get("summary", ""),
        "tone": analysis.get("tone", "Neutral"),
        "urgency": analysis.get("urgency", "Low"),
        "action_items": analysis.get("action_items", []),
        "suggested_reply": analysis.get("suggested_reply", ""),
        "source": "zoho-mail",
        "createdAt": utc_now_iso(),
    }
    save_analysis_doc(doc)
    return doc

# ------------------------------------------------------------------
# Routes
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
                item = d.to_dict() or {}
                total += 1
                tone = item.get("tone", "")
                urgency = item.get("urgency", "Low")
                if urgency == "High": high_urgency += 1
                if "Angry" in tone or "Negative" in tone: angry += 1
                if "Positive" in tone: positive += 1
                recent.append({"id": d.id, "tone": tone, "urgency": urgency, "summary": item.get("summary", "")})
            recent = recent[:5]
        else:
            total = len(GLOBAL_STATS)
            high_urgency = sum(1 for x in GLOBAL_STATS if x["urgency"] == "High")
            angry = sum(1 for x in GLOBAL_STATS if "Angry" in x["tone"] or "Negative" in x["tone"])
            positive = sum(1 for x in GLOBAL_STATS if "Positive" in x["tone"])
            recent = GLOBAL_STATS[-5:][::-1]

        return jsonify({"total": total, "high_urgency": high_urgency, "angry": angry, "positive": positive, "neutral": total-angry-positive, "recent": recent})
    except:
        return jsonify({"total": 0, "high_urgency": 0, "angry": 0, "positive": 0, "neutral": 0, "recent": []})

@app.route("/api/emails")
def api_emails():
    try:
        emails = []
        if db:
            docs = db.collection("email_analysis").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(200).stream()
            for d in docs:
                item = d.to_dict(); item["id"] = d.id; emails.append(item)
        else:
            emails = GLOBAL_EMAILS[:200]
        return jsonify(emails)
    except:
        return jsonify([])

@app.route("/api/email/<email_id>")
def api_email_detail(email_id):
    try:
        if db:
            doc = db.collection("email_analysis").document(email_id).get()
            if doc.exists: return jsonify(doc.to_dict())
            docs = db.collection("email_analysis").where("messageId", "==", email_id).limit(1).stream()
            for d in docs: return jsonify(d.to_dict())
            return jsonify({}), 404
        else:
            for e in GLOBAL_EMAILS:
                if e.get("messageId") == email_id or e.get("id") == email_id: return jsonify(e)
            return jsonify({}), 404
    except:
        return jsonify({}), 500

# --- SALESIQ WEBHOOK (Fixed for SalesIQ format) ---
@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Dedicated Endpoint for Zoho SalesIQ (Zobot).
    """
    try:
        # 1. Parse Incoming Data safely
        data = request.get_json(force=True)
        print(f"DEBUG - Received from SalesIQ: {data}")

        user_text = ""
        
        # Strategy: Try every possible field SalesIQ might use
        if isinstance(data, dict):
            # Case A: Direct message field
            if "message" in data:
                user_text = data["message"]
            # Case B: Inside 'data' wrapper
            elif "data" in data and isinstance(data["data"], dict):
                user_text = data["data"].get("message") or data["data"].get("text")
            # Case C: Inside 'visitor' wrapper
            elif "visitor" in data and isinstance(data["visitor"], dict):
                user_text = data["visitor"].get("message")
        
        # 2. Handle "Empty" or "Greeting" inputs
        if not user_text or user_text.strip() == "":
            return jsonify({
                "replies": [
                    {"text": "👋 Hi! I am SmartMail AI."}, 
                    {"text": "Paste an email text here, and I will analyze its sentiment, urgency, and draft a reply for you."}
                ]
            })

        # 3. Run Your AI Analysis
        result = analyze_text(user_text)
        
        # 4. Save to Firebase
        try:
            doc = {
                "source": "salesiq-bot",
                "original_text": user_text,
                "summary": result.get("summary", ""),
                "tone": result.get("tone", "Neutral"),
                "urgency": result.get("urgency", "Low"),
                "suggested_reply": result.get("suggested_reply", ""),
                "createdAt": utc_now_iso(),
            }
            save_analysis_doc(doc)
        except Exception as e:
            logger.error(f"Firebase Save Error: {e}")

        # 5. Send Beautiful Response to SalesIQ
        return jsonify({
            "replies": [
                {
                    "text": f"📊 **Analysis Report**\n\n**Summary:** {result.get('summary')}"
                },
                {
                    "text": f"❤️ **Tone:** {result.get('tone')}  |  🔥 **Urgency:** {result.get('urgency')}"
                },
                {
                    "text": f"💡 **Suggested Reply:**\n\n{result.get('suggested_reply')}"
                }
            ]
        })

    except Exception as e:
        logger.error(f"SalesIQ Webhook Critical Fail: {e}")
        return jsonify({
            "replies": [{"text": "⚠️ My AI brain is sleeping. Please try sending that again in 10 seconds!"}]
        })

@app.route("/fetch_zoho_emails", methods=["POST"])
def fetch_zoho_emails():
    """
    OPTIMIZED: Checks DB first. If email ID exists, SKIPS analysis to save API credits.
    """
    try:
        limit = int(request.args.get("limit", "20"))
        raw = list_inbox_emails(limit=limit)
        
        messages = []
        if isinstance(raw, dict):
            messages = raw.get("data") or raw.get("messages") or []
        elif isinstance(raw, list):
            messages = raw

        processed = 0
        skipped = 0
        
        for msg in messages:
            if not isinstance(msg, dict): continue
            
            message_id = str(msg.get("messageId") or msg.get("messageIdString") or "")
            if not message_id: continue
            
            # --- CREDIT SAVER: Check if exists ---
            if db:
                doc = db.collection("email_analysis").document(message_id).get()
                if doc.exists:
                    skipped += 1
                    continue
            else:
                exists = any(e.get("messageId") == message_id for e in GLOBAL_EMAILS)
                if exists:
                    skipped += 1
                    continue
            
            # If not found, analyze it
            try:
                analyze_zoho_message(message_id)
                processed += 1
            except Exception as e:
                logger.error(f"Message {message_id} failed: {e}")

        logger.info(f"Sync Result: {processed} analyzed, {skipped} skipped.")
        return jsonify({"status": "ok", "processed": processed, "skipped": skipped})
        
    except Exception:
        error_trace = traceback.format_exc()
        logger.error(f"Error in /fetch_zoho_emails:\n{error_trace}")
        return jsonify({"status": "error", "error": str(error_trace)}), 500

# ------------------------------------------------------------------
# 6. ZOHO CLIQ BOT ENDPOINT (The Conversation Logic)
# ------------------------------------------------------------------

def get_recent_emails_for_bot():
    """Fetches top 5 emails and formats them as Zoho Buttons"""
    try:
        # Reuse your existing function
        raw = list_inbox_emails(limit=5)
        messages = []
        if isinstance(raw, dict):
            messages = raw.get("data") or raw.get("messages") or []
        
        buttons = []
        for msg in messages:
            subject = msg.get("subject", "No Subject")[:40] + "..." # Truncate long subjects
            msg_id = msg.get("messageId")
            
            # Create a button for each email
            buttons.append({
                "label": subject,
                "type": "+", # Action button
                "action": {
                    "type": "invoke.function",
                    "data": {"action_type": "analyze_specific", "msg_id": msg_id}
                }
            })
            
        return buttons
    except Exception as e:
        logger.error(f"Bot Fetch Error: {e}")
        return []

@app.route("/zoho-bot", methods=["POST"])
def zoho_bot_handler():
    try:
        data = request.get_json(force=True)
        
        # 1. Identify Event Type (Message vs Button Click)
        # Zoho sends different structures. We look for 'type' inside 'action' data if it exists.
        event_data = {}
        if "action" in data and "data" in data["action"]:
            event_data = data["action"]["data"]
        
        action_type = event_data.get("action_type")

        # --- SCENARIO A: USER SAYS "HI" (OR ANY TEXT) ---
        # If no specific action_type, it's a greeting/new message
        if not action_type:
            buttons = get_recent_emails_for_bot()
            
            if not buttons:
                return jsonify({"text": "⚠️ I couldn't fetch emails. Check your Zoho connection."})

            return jsonify({
                "text": "👋 **Hello! I am SmartMail.**",
                "card": {
                    "title": "Inbox Triage",
                    "theme": "modern-inline",
                    "thumbnail": "https://cdn-icons-png.flaticon.com/512/646/646094.png",
                    "rows": [
                        {"label": "Status", "value": "Ready to Analyze"},
                        {"label": "Instruction", "value": "Select an email below to process:"}
                    ]
                },
                "buttons": buttons
            })

        # --- SCENARIO B: USER SELECTED AN EMAIL ---
        if action_type == "analyze_specific":
            msg_id = event_data.get("msg_id")
            
            # 1. Run the Analysis (This saves to Firebase automatically via your existing code)
            # We wrap this in try/except to catch analysis errors
            try:
                doc = analyze_zoho_message(msg_id)
                
                # 3. Return the Result Card + Loop Button
                return jsonify({
                    "text": "✅ **Analysis Complete**",
                    "card": {
                        "title": doc.get("subject", "Email Analysis"),
                        "theme": "modern-inline",
                        "rows": [
                            {"label": "📝 Summary", "value": doc.get("summary", "No Summary")},
                            {"label": "❤️ Tone", "value": doc.get("tone", "Neutral")},
                            {"label": "🔥 Urgency", "value": doc.get("urgency", "Low")},
                            {"label": "💡 Draft", "value": (doc.get("suggested_reply", "")[:100] + "...")}
                        ]
                    },
                    "buttons": [
                        {
                            "label": "🔄 Analyze Another Email?",
                            "type": "+",
                            "action": {
                                "type": "invoke.function",
                                "data": {"action_type": "load_list"} # Loop back
                            }
                        },
                        {
                            "label": "Open in Dashboard",
                            "type": "open.url",
                            "action": {
                                "web": f"https://{request.host}/mails" 
                            }
                        }
                    ]
                })

            except Exception as e:
                return jsonify({"text": f"❌ Analysis Failed: {str(e)}"})

        # --- SCENARIO C: LOOP (USER CLICKED "ANALYZE ANOTHER") ---
        if action_type == "load_list":
            buttons = get_recent_emails_for_bot()
            return jsonify({
                "text": "🔄 **Fetching latest emails...**",
                "buttons": buttons
            })

    except Exception as e:
        logger.error(f"Bot Error: {e}")
        return jsonify({"text": "🤖 System Error. Please try again."})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
