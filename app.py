import os
import logging
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv

# Import Custom Services
import firebase_service
import zoho_service
import ai_service

# Load Environment
load_dotenv()

app = Flask(__name__)
CORS(app)

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = app.logger

# --- WEBHOOK ROUTE (The Bot) ---
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        
        # 1. Safe Extraction of User Message
        user_text = ""
        if "message" in data:
            msg = data["message"]
            user_text = msg.get("text", "") if isinstance(msg, dict) else str(msg)
        elif "visitor" in data:
            user_text = data["visitor"].get("message", "")
        
        user_text = str(user_text).strip()
        logger.info(f"User said: {user_text}")

        # --- LOGIC FLOW ---

        # SCENARIO A: Greeting / Start
        if not user_text or user_text.lower() in ["hi", "hello", "start", "menu", "restart"]:
            
            # Fetch last 5 emails
            emails = zoho_service.fetch_latest_emails(limit=5)
            
            if not emails:
                return jsonify({
                    "replies": [{"text": "👋 Hi! I tried to fetch your emails, but found none or had a connection error."}],
                    "suggestions": ["Try Again"]
                })

            # Create suggestions based on subjects
            suggestions = [email['subject'] for email in emails]
            # Store ID mapping in a simple global cache or use Zoho search in next step
            # For simplicity in this bot, we assume the subject is unique enough to find
            
            return jsonify({
                "replies": [
                    {"text": "👋 **Hello!** I am your SmartMail Insight Bot."},
                    {"text": "Here are your last 5 emails. Select one to analyze:"}
                ],
                "suggestions": suggestions
            })

        # SCENARIO B: User Selected an Email (Subject Analysis)
        # We try to find this subject in the recent cache or search Zoho
        found_message_id = zoho_service.find_message_id_by_subject(user_text)

        if found_message_id:
            # 1. Fetch Full Content
            email_data = zoho_service.get_full_email_content(found_message_id)
            
            if not email_data:
                return jsonify({"replies": [{"text": "❌ Error fetching email content."}]})

            full_text = f"Subject: {email_data['subject']}\n\n{email_data['content']}"

            # 2. Analyze
            analysis = ai_service.analyze_email(full_text)

            # 3. Save to Firebase
            save_data = {
                "messageId": found_message_id,
                "subject": email_data['subject'],
                "summary": analysis['summary'],
                "tone": analysis['tone'],
                "urgency": analysis['urgency'],
                "reply": analysis['reply'],
                "timestamp": firebase_service.get_timestamp()
            }
            firebase_service.save_analysis(save_data)

            # 4. Respond to User
            return jsonify({
                "replies": [
                    {"text": f"✅ **Analyzed:** {email_data['subject']}"},
                    {"text": f"📊 **Summary:** {analysis['summary']}"},
                    {"text": f"🎭 **Tone:** {analysis['tone']} | ⚡ **Urgency:** {analysis['urgency']}"},
                    {"text": f"💬 **Suggested Reply:**\n{analysis['reply']}"}
                ],
                "suggestions": ["Hi", "View Dashboard"]
            })

        # SCENARIO C: Dashboard Link
        if "dashboard" in user_text.lower():
            site_url = request.host_url
            return jsonify({
                "replies": [{"text": f"👀 View all stored analysis here:\n{site_url}"}],
                "suggestions": ["Hi"]
            })

        # Fallback
        return jsonify({
            "replies": [{"text": "I didn't recognize that email subject. Please type 'Hi' to see your list again."}],
            "suggestions": ["Hi"]
        })

    except Exception as e:
        logger.error(f"Webhook Crash: {e}")
        return jsonify({
            "replies": [{"text": "⚠️ System Error. Please check logs."}],
            "suggestions": ["Hi"]
        })

# --- DASHBOARD ROUTES ---
@app.route("/")
def dashboard():
    return render_template("dashboard.html")

@app.route("/api/history")
def get_history():
    data = firebase_service.get_all_analyses()
    return jsonify(data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
