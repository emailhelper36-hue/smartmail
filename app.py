import os
import logging
import traceback
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from analyze import analyze_text
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)

# --- IN-MEMORY DATABASE ---
GLOBAL_STATS = []

@app.route("/")
def index():
    return "SmartMail Bot is Running."

@app.route("/dashboard")
def dashboard():
    # This looks for 'index.html' inside the 'templates' folder
    return render_template('index.html') 

@app.route("/api/stats")
def stats():
    total = len(GLOBAL_STATS)
    urgent = sum(1 for x in GLOBAL_STATS if x['urgency'] == 'High')
    angry = sum(1 for x in GLOBAL_STATS if "Angry" in x['tone'] or "Negative" in x['tone'])
    positive = sum(1 for x in GLOBAL_STATS if "Positive" in x['tone'])
    neutral = total - angry - positive

    return jsonify({
        "total": total,
        "high_urgency": urgent,
        "angry": angry,
        "positive": positive,
        "neutral": neutral,
        "recent": GLOBAL_STATS[-5:][::-1]
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        app.logger.info(f"Incoming Payload: {data}")

        # --- EXTRACT TEXT ---
        user_text = ""
        if "message" in data and isinstance(data["message"], dict):
             user_text = data["message"].get("text") or data["message"].get("content")
        if not user_text and "visitor" in data:
            if "message" in data["visitor"]: user_text = data["visitor"]["message"]
        if not user_text and "data" in data and isinstance(data["data"], dict):
            user_text = data["data"].get("text") or data["data"].get("message")

        if not user_text:
             return jsonify({"replies": [{"text": "System Error: No text found."}]})

        # --- ANALYZE ---
        result = analyze_text(user_text)

        # --- SAVE TO GLOBAL STATS ---
        GLOBAL_STATS.append({
            "tone": result['tone'],
            "urgency": result['urgency'],
            "summary": result['summary']
        })

        # --- FORMAT RESPONSE ---
        bot_message = (
            f"🔍 **Analysis Report**\n"
            f"------------------------------\n"
            f"• **Tone:** {result.get('tone', 'Neutral')}\n"
            f"• **Urgency:** {result.get('urgency', 'Low')}\n\n"
            f"📝 **Summary:**\n{result.get('summary', 'No summary')}\n\n"
            f"💡 **Suggested Reply:**\n{result.get('suggested_reply', 'No reply')}"
        )

        response = {
            "replies": [{"text": bot_message}],
            "suggestions": ["Create Ticket", "Draft Reply", "Escalate"]
        }
        return jsonify(response)

    except Exception as e:
        error_trace = traceback.format_exc()
        app.logger.error(f"CRASH: {error_trace}")
        return jsonify({"replies": [{"text": "⚠️ Processing Error."}]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
