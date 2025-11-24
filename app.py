# --- LOAD ENV FIRST ---
from dotenv import load_dotenv
load_dotenv() 

import os
import json
import logging
import traceback
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from bs4 import BeautifulSoup  

from analyze import analyze_text
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__, template_folder="templates")
CORS(app)

# Enhanced logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = app.logger

# ------------------------------------------------------------------
# 1. Firebase Setup
# ------------------------------------------------------------------
def init_firebase():
    if firebase_admin._apps: 
        return firestore.client()
    
    if not os.getenv("FIREBASE_PRIVATE_KEY"): 
        logger.warning("Firebase credentials not found")
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
        logger.info("Firebase initialized successfully")
        return firestore.client()
    except Exception as e:
        logger.error(f"Firebase init error: {e}")
        return None

db = init_firebase()
GLOBAL_STATS = [] 
GLOBAL_EMAILS = []
EMAILS_CACHE = {"data": [], "last_fetched": None}

def utc_now_iso(): 
    return datetime.now(timezone.utc).isoformat()

def save_analysis_doc(payload):
    """Save to Firebase and local cache"""
    try:
        # Add to global stats for dashboard
        GLOBAL_STATS.append({
            "tone": payload.get("tone"), 
            "urgency": payload.get("urgency")
        })
        GLOBAL_EMAILS.insert(0, payload)
        
        # Save to Firebase if available
        if db:
            doc_id = str(payload.get("messageId") or "")
            col = db.collection("email_analysis")
            doc_ref = col.document(doc_id) if doc_id else col.document()
            payload["createdAt"] = utc_now_iso()
            doc_ref.set(payload, merge=True)
            logger.info(f"Saved analysis to Firebase: {payload.get('subject', 'No subject')}")
            
    except Exception as e:
        logger.error(f"Save analysis error: {e}")

# ------------------------------------------------------------------
# 2. EFFICIENT Zoho Mail API with Caching
# ------------------------------------------------------------------
ZOHO_TOKEN_CACHE = {
    "token": None,
    "expires_at": None
}

def get_zoho_token():
    """Get Zoho access token with caching to avoid rate limits"""
    global ZOHO_TOKEN_CACHE
    
    # Check if we have a valid cached token (valid for 55 minutes)
    if (ZOHO_TOKEN_CACHE["token"] and 
        ZOHO_TOKEN_CACHE["expires_at"] and 
        datetime.now(timezone.utc) < ZOHO_TOKEN_CACHE["expires_at"]):
        logger.info("Using cached Zoho token")
        return ZOHO_TOKEN_CACHE["token"]
    
    try:
        url = "https://accounts.zoho.com/oauth/v2/token"
        params = {
            "refresh_token": os.environ.get("ZOHO_REFRESH_TOKEN"),
            "client_id": os.environ.get("ZOHO_CLIENT_ID"),
            "client_secret": os.environ.get("ZOHO_CLIENT_SECRET"),
            "grant_type": "refresh_token",
        }
        
        logger.info("Requesting new Zoho token...")
        resp = requests.post(url, params=params, timeout=10)
        
        if resp.status_code == 200:
            token_data = resp.json()
            access_token = token_data.get("access_token")
            if access_token:
                # Cache the token for 55 minutes (Zoho tokens expire in 1 hour)
                ZOHO_TOKEN_CACHE["token"] = access_token
                ZOHO_TOKEN_CACHE["expires_at"] = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=55)
                
                logger.info("New Zoho token obtained and cached")
                return access_token
            else:
                logger.error("No access token in response")
        else:
            logger.error(f"Zoho token error {resp.status_code}: {resp.text}")
            
    except Exception as e:
        logger.error(f"Zoho token exception: {e}")
    
    return None

def list_inbox_emails(limit=5, force_refresh=False):
    """Get inbox emails with caching and rate limit protection"""
    global EMAILS_CACHE
    
    # Return cached emails if they're fresh (5 minutes) and not forcing refresh
    if (not force_refresh and 
        EMAILS_CACHE["last_fetched"] and 
        (datetime.now(timezone.utc) - EMAILS_CACHE["last_fetched"]).total_seconds() < 300):
        logger.info("Returning cached emails")
        return EMAILS_CACHE
    
    token = get_zoho_token()
    if not token: 
        logger.warning("Cannot get Zoho token")
        return {"data": []}
    
    account_id = os.environ.get('ZOHO_ACCOUNT_ID')
    if not account_id:
        logger.error("ZOHO_ACCOUNT_ID not set")
        return {"data": []}
    
    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages/view"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    params = {"limit": limit, "sortorder": "false"}
    
    try:
        logger.info(f"Fetching {limit} emails from Zoho...")
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            messages = data.get("data", [])
            logger.info(f"Successfully fetched {len(messages)} emails")
            
            # Update cache
            EMAILS_CACHE = {
                "data": messages,
                "last_fetched": datetime.now(timezone.utc)
            }
            
            return {"data": messages}
        elif response.status_code == 429:  # Too Many Requests
            logger.warning("Zoho rate limit hit - using cached data")
            return EMAILS_CACHE  # Return cached data even if stale
        else:
            logger.error(f"Zoho API error {response.status_code}: {response.text}")
            return EMAILS_CACHE  # Return cached data on error
            
    except Exception as e:
        logger.error(f"Zoho API exception: {e}")
        return EMAILS_CACHE  # Return cached data on exception

def get_email_content(message_id):
    """Get FULL email content from Zoho API"""
    token = get_zoho_token()
    if not token:
        logger.error("No token available for content fetch")
        return "Authentication failed - cannot fetch email content"
    
    account_id = os.environ.get('ZOHO_ACCOUNT_ID')
    if not account_id:
        logger.error("ZOHO_ACCOUNT_ID not set")
        return "Account ID not configured"
    
    # Try the main content endpoint
    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages/{message_id}/content"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    try:
        logger.info(f"Fetching email content for message: {message_id}")
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            email_data = data.get("data", {})
            
            # Extract subject and content
            subject = email_data.get("subject", "No Subject")
            content = email_data.get("content", "") or email_data.get("body", "")
            
            if content:
                # Clean HTML tags if present
                if "<" in content and ">" in content:
                    soup = BeautifulSoup(content, "html.parser")
                    content = soup.get_text(separator=" ", strip=True)
                
                full_content = f"Subject: {subject}\n\n{content}"
                logger.info(f"Successfully fetched email content, length: {len(full_content)}")
                return full_content
            else:
                logger.warning("Empty content in email response")
                return f"Subject: {subject}\n\nNo body content available"
                
        else:
            logger.error(f"Content API error {response.status_code}: {response.text}")
            return f"Error fetching email content: {response.status_code}"
            
    except Exception as e:
        logger.error(f"Content fetch exception: {e}")
        return f"Exception fetching email content: {str(e)}"

def find_email_by_subject(user_text):
    """Find email ID by matching subject text using cached data"""
    if not user_text:
        return None
        
    emails_data = EMAILS_CACHE if EMAILS_CACHE["data"] else list_inbox_emails(limit=10)
    messages = emails_data.get("data", [])
    user_text_clean = user_text.rstrip("...").strip().lower()
    
    for msg in messages:
        subject = msg.get("subject", "").lower()
        if user_text_clean in subject:
            return msg.get("messageId")
    
    return None

def analyze_zoho_email(message_id):
    """Analyze a Zoho email with FULL content"""
    try:
        # Get FULL email content first
        email_content = get_email_content(message_id)
        
        # Get subject for display (from cache)
        subject = "No Subject"
        for msg in EMAILS_CACHE.get("data", []):
            if msg.get("messageId") == message_id:
                subject = msg.get("subject", "No Subject")
                break
        
        # Analyze the FULL content
        analysis = analyze_text(email_content)
        
        # Create analysis document
        doc = {
            "messageId": message_id,
            "subject": subject[:100],
            "summary": analysis.get("summary"),
            "tone": analysis.get("tone"),
            "urgency": analysis.get("urgency"),
            "suggested_reply": analysis.get("suggested_reply"),
            "key_points": analysis.get("key_points", []),
            "source": "zoho-mail",
            "analyzedAt": utc_now_iso()
        }
        
        save_analysis_doc(doc)
        return doc
        
    except Exception as e:
        logger.error(f"Email analysis error: {e}")
        return None

# ------------------------------------------------------------------
# 3. Efficient Webhook
# ------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        logger.info(f"Webhook received: {data.get('handler', 'unknown')}")

        # Extract user text safely
        user_text = ""
        if "message" in data:
            msg = data["message"]
            user_text = msg.get("text", "") if isinstance(msg, dict) else str(msg)
        elif "data" in data:
            inner = data["data"]
            user_text = inner.get("message") or inner.get("text") or ""
        elif "visitor" in data:
            user_text = data["visitor"].get("message", "")

        # Ensure user_text is string
        user_text = str(user_text or "").strip()

        # 1. Dashboard command
        if user_text.lower() == "dashboard":
            site_url = f"https://{request.host}/"
            return jsonify({
                "replies": [{"text": f"📊 **SmartMail Dashboard:**\n{site_url}"}],
                "suggestions": ["Hi", "Sync Emails"]
            })

        # 2. Sync emails command
        if user_text.lower() == "sync emails":
            emails_data = list_inbox_emails(limit=10, force_refresh=True)
            messages = emails_data.get("data", [])
            count = len(messages)
            
            return jsonify({
                "replies": [{"text": f"🔄 **Sync Complete:** Found {count} emails in inbox."}],
                "suggestions": ["Hi"] + [msg.get("subject", "No Subject")[:25] + "..." 
                                       for msg in messages[:4] if msg.get("subject")]
            })

        # 3. Greeting or empty
        if not user_text or user_text.lower() in ["hi", "hello", "menu", "start"]:
            emails_data = list_inbox_emails(limit=5)  # Uses cache if available
            messages = emails_data.get("data", [])
            suggestions = [msg.get("subject", "No Subject")[:25] + "..." 
                          for msg in messages if msg.get("subject")]
            
            if not suggestions:
                return jsonify({
                    "replies": [{
                        "text": "📭 **Inbox Status:** No emails found.\n\nYou can still analyze text by typing any email content directly!"
                    }],
                    "suggestions": ["Test: Urgent security update", "Test: Customer complaint", "Test: Positive feedback", "Sync Emails"]
                })

            return jsonify({
                "replies": [{"text": "📬 **Inbox Connected!**\nTap an email below to analyze it:"}],
                "suggestions": suggestions + ["Sync Emails"]
            })
        
        # 4. Email selection or direct text analysis
        message_id = find_email_by_subject(user_text)
        
        if message_id:
            # Analyze selected email
            analysis = analyze_zoho_email(message_id)
            if analysis:
                replies = [
                    {"text": f"📋 **{analysis.get('subject', 'Email Analysis')}**"},
                    {"text": f"📝 **Summary:** {analysis.get('summary')}"},
                    {"text": f"🎭 **Tone:** {analysis.get('tone')} | ⚡ **Urgency:** {analysis.get('urgency')}"}
                ]
                
                # Add key points if available
                key_points = analysis.get('key_points', [])
                if key_points:
                    points_text = "\n".join([f"• {point}" for point in key_points])
                    replies.append({"text": f"🔑 **Key Points:**\n{points_text}"})
                
                replies.append({"text": f"💬 **Suggested Reply:**\n{analysis.get('suggested_reply')}"})
                
                return jsonify({
                    "replies": replies,
                    "suggestions": ["Hi", "Dashboard", "Sync Emails"]
                })
            else:
                return jsonify({
                    "replies": [{"text": "❌ Failed to analyze the selected email."}],
                    "suggestions": ["Hi"]
                })
        else:
            # Direct text analysis
            analysis = analyze_text(user_text)
            replies = [
                {"text": f"📝 **Analysis:** {analysis.get('summary')}"},
                {"text": f"🎭 **Tone:** {analysis.get('tone')} | ⚡ **Urgency:** {analysis.get('urgency')}"}
            ]
            
            key_points = analysis.get('key_points', [])
            if key_points:
                points_text = "\n".join([f"• {point}" for point in key_points])
                replies.append({"text": f"🔑 **Key Points:**\n{points_text}"})
            
            replies.append({"text": f"💬 **Suggested Reply:**\n{analysis.get('suggested_reply')}"})
            
            return jsonify({
                "replies": replies,
                "suggestions": ["Hi", "Dashboard"]
            })

    except Exception as e:
        logger.error(f"Webhook error: {traceback.format_exc()}")
        return jsonify({
            "replies": [{"text": "⚠️ System error. Please try again or check logs."}],
            "suggestions": ["Hi"]
        })

# ------------------------------------------------------------------
# 4. Dashboard Routes
# ------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/mails")
def mails_page():
    return render_template("mails.html")

@app.route("/api/stats")
def stats():
    try:
        recent_emails = [email for email in reversed(GLOBAL_EMAILS[-10:])]
        
        # Calculate statistics
        total_emails = len(GLOBAL_EMAILS)
        high_urgency = sum(1 for stat in GLOBAL_STATS if stat.get('urgency') == 'High')
        negative_tone = sum(1 for stat in GLOBAL_STATS if stat.get('tone') in ['Urgent', 'Negative'])
        positive_tone = sum(1 for stat in GLOBAL_STATS if stat.get('tone') == 'Positive')
        neutral_tone = total_emails - negative_tone - positive_tone
        
        return jsonify({
            "total": total_emails,
            "high_urgency": high_urgency,
            "negative_tone": negative_tone,
            "positive_tone": positive_tone,
            "neutral_tone": neutral_tone,
            "recent": recent_emails[-5:]  # Last 5 emails
        })
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({
            "total": 0, "high_urgency": 0, "negative_tone": 0, 
            "positive_tone": 0, "neutral_tone": 0, "recent": []
        })

@app.route("/fetch_zoho_emails", methods=["POST"])
def trigger_sync():
    """Manual sync endpoint for dashboard"""
    try:
        emails_data = list_inbox_emails(limit=15, force_refresh=True)
        messages = emails_data.get("data", [])
        analyzed_count = 0
        
        for message in messages:
            message_id = message.get("messageId")
            if message_id and not any(email.get('messageId') == message_id for email in GLOBAL_EMAILS):
                analyze_zoho_email(message_id)
                analyzed_count += 1
        
        return jsonify({
            "status": "success", 
            "analyzed": analyzed_count,
            "total_found": len(messages)
        })
        
    except Exception as e:
        logger.error(f"Sync error: {e}")
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 SmartMail AI starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
