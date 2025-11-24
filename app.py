# --- LOAD ENV FIRST ---
from dotenv import load_dotenv
load_dotenv()

import os
import logging
import traceback
from datetime import datetime, timezone

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

# --- CUSTOM MODULES ---
import zoho_service
from analyze import analyze_text # Using your high-quality analysis
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__, template_folder="templates")
CORS(app)

# Logging
logging.basicConfig(level=logging.INFO)
logger = app.logger

# --- FIREBASE ---
def init_firebase():
    if firebase_admin._apps: return firestore.client()
    if not os.environ.get("FIREBASE_PRIVATE_KEY"): return None
    try:
        cred_data = {
            "type": os.environ.get("FIREBASE_TYPE"),
            "project_id": os.environ.get("FIREBASE_PROJECT_ID"),
            "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID"),
            "private_key": os.environ.get("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
            "client_email": os.environ.get("FIREBASE_CLIENT_EMAIL"),
            "client_id": os.environ.get("FIREBASE_CLIENT_ID"),
            "auth_uri": os.environ.get("FIREBASE_AUTH_URI"),
            "token_uri": os.environ.get("FIREBASE_TOKEN_URI"),
            "auth_provider_x509_cert_url": os.environ.get("FIREBASE_AUTH_PROVIDER_CERT_URL"),
            "client_x509_cert_url": os.environ.get("FIREBASE_CLIENT_CERT_URL"),
        }
        cred = credentials.Certificate(cred_data)
        firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        logger.error(f"Firebase Init Error: {e}")
        return None

db = init_firebase()

def save_analysis_doc(payload):
    if not db: return
    try:
        doc_id = str(payload.get("messageId") or "")
        col = db.collection("email_analysis")
        doc_ref = col.document(doc_id) if doc_id else col.document()
        payload["createdAt"] = datetime.now(timezone.utc).isoformat()
        doc_ref.set(payload, merge=True)
    except Exception as e:
        logger.error(f"Save Error: {e}")

# --- WEBHOOK ---
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        
        # Extract Text
        user_text = ""
        if "message" in data and isinstance(data["message"], dict):
            user_text = data["message"].get("text", "")
        elif "visitor" in data:
             user_text = data["visitor"].get("message", "")
        
        user_text = str(user_text).strip()
        
        # 1. Dashboard
        if user_text.lower() == "dashboard":
             return jsonify({
                "replies": [{"text": f"📊 **Dashboard:**\n{request.host_url}"}],
                "suggestions": ["Hi"]
            })

        # 2. Greeting (List Emails)
        if not user_text or user_text.lower() in ["hi", "hello", "start", "menu"]:
            # Use Zoho Service to get list
            emails = zoho_service.fetch_latest_emails(limit=5)
            suggestions = [e['subject'] for e in emails]
            
            return jsonify({
                "replies": [{"text": "👋 **Hello!** Select an email to analyze:"}],
                "suggestions": suggestions
            })

        # 3. Analyze Email (Button Click)
        # Use Zoho Service to find ID (Handles Cache & Re-fetch)
        msg_id = zoho_service.find_message_id_by_subject(user_text)
        
        if msg_id:
            # Fetch Content
            email_data = zoho_service.get_full_email_content(msg_id)
            
            # Use Subject from User Text if fetch failed slightly
            final_subject = email_data['subject'] if email_data else user_text
            final_content = email_data['content'] if email_data else "Content unavailable."

            # ANALYZE using High-Quality Logic
            full_text = f"{final_subject}\n\n{final_content}"
            analysis = analyze_text(full_text)
            
            # Prepare Result
            doc = {
                "messageId": msg_id,
                "subject": final_subject,
                "summary": analysis['summary'],
                "tone": analysis['tone'],
                "urgency": analysis['urgency'],
                "suggested_reply": analysis['suggested_reply'],
                "key_points": analysis.get('key_points', []),
                "source": "zoho-mail"
            }
            save_analysis_doc(doc)

            return jsonify({
                "replies": [
                    {"text": f"✅ **{doc['subject']}**"},
                    {"text": f"📝 **Summary:** {doc['summary']}"},
                    {"text": f"🎭 **Tone:** {doc['tone']} | ⚡ **Urgency:** {doc['urgency']}"},
                    {"text": f"💬 **Draft:**\n{doc['suggested_reply']}"}
                ],
                "suggestions": ["Hi", "Dashboard"]
            })
            
        # 4. Fallback (Raw Text)
        analysis = analyze_text(user_text)
        return jsonify({
            "replies": [
                {"text": "I analyzed your text directly:"},
                {"text": f"📝 {analysis['summary']}"},
                {"text": f"Tone: {analysis['tone']}"}
            ],
            "suggestions": ["Hi"]
        })

    except Exception as e:
        logger.error(f"Webhook Error: {traceback.format_exc()}")
        return jsonify({"replies": [{"text": "⚠️ System Error."}]})

# --- DASHBOARD ROUTES ---
@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/history")
def history():
    if not db: return jsonify([])
    docs = db.collection("email_analysis").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(20).stream()
    return jsonify([d.to_dict() for d in docs])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
