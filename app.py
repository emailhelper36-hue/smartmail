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
# Make sure zoho_service.py and analyze.py are in the same folder!
import zoho_service
from analyze import analyze_text 
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__, template_folder="templates")
CORS(app)

# Enable Logging
logging.basicConfig(level=logging.INFO)
logger = app.logger

# ------------------------------------------------------------------
# 1. Firebase Setup
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# 2. WEBHOOK
# ------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        
        # Extract User Text Safely
        user_text = ""
        if "message" in data and isinstance(data["message"], dict):
            user_text = data["message"].get("text", "")
        elif "visitor" in data:
             user_text = data["visitor"].get("message", "")
        
        user_text = str(user_text).strip()
        
        # --- SCENARIO 1: Dashboard Link ---
        if user_text.lower() == "dashboard":
             return jsonify({
                "replies": [{"text": f"📊 **Dashboard:**\n{request.host_url}"}],
                "suggestions": ["Hi"]
            })

        # --- SCENARIO 2: Greeting (List Emails) ---
        if not user_text or user_text.lower() in ["hi", "hello", "start", "menu"]:
            emails = zoho_service.fetch_latest_emails(limit=5)
            suggestions = [e['subject'] for e in emails]
            
            return jsonify({
                "replies": [{"text": "👋 **Hello!** Select an email to analyze:"}],
                "suggestions": suggestions
            })

        # --- SCENARIO 3: Analyze Email (Button Click) ---
        # FIX: We now unpack 3 values (ID, Subject, FolderID)
        msg_id, full_subject, folder_id = zoho_service.find_message_data_by_subject(user_text)
        
        if msg_id:
            # FIX: We pass the FolderID to get content
            email_data = zoho_service.get_full_email_content(msg_id, folder_id)
            
            # Determine Final Data
            if email_data:
                final_subject = email_data['subject']
                final_content = email_data['content']
            else:
                final_subject = full_subject or user_text
                final_content = "Content unavailable."

            # ANALYZE
            full_text = f"{final_subject}\n\n{final_content}"
            analysis = analyze_text(full_text)
            
            # SAVE
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
            
        # --- SCENARIO 4: Fallback (Raw Text) ---
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

# ------------------------------------------------------------------
# 3. DASHBOARD ROUTES
# ------------------------------------------------------------------
@app.route("/")
def index():
    # Points to your templates/dashboard.html
    return render_template("dashboard.html")

@app.route("/api/history")
def history():
    if not db: return jsonify([])
    try:
        docs = db.collection("email_analysis").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(20).stream()
        return jsonify([d.to_dict() for d in docs])
    except:
        return jsonify([])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
