import os
import requests

HF_TOKEN = os.environ.get("HF_TOKEN")
API_BASE = "https://api-inference.huggingface.co/models"

def query_hf(payload, model_url):
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        response = requests.post(model_url, headers=headers, json=payload, timeout=8)
        return response.json()
    except:
        return None

def analyze_email(text):
    """
    Performs Summary, Tone, and Urgency analysis.
    Returns a dictionary.
    """
    clean_text = text[:1500] # Truncate for API limits
    
    # 1. Detect Urgency (Rule-based is faster and more accurate for business)
    urgency_keywords = ["urgent", "asap", "emergency", "deadline", "immediately", "critical"]
    text_lower = clean_text.lower()
    
    is_urgent = any(word in text_lower for word in urgency_keywords)
    urgency_level = "High" if is_urgent else "Low"

    # 2. Summarization (BART)
    summary_model = f"{API_BASE}/facebook/bart-large-cnn"
    summary_res = query_hf({"inputs": clean_text}, summary_model)
    
    summary = "Could not generate summary."
    if summary_res and isinstance(summary_res, list) and len(summary_res) > 0:
        summary = summary_res[0].get('summary_text', summary)

    # 3. Tone (Sentiment)
    tone_model = f"{API_BASE}/cardiffnlp/twitter-roberta-base-sentiment-latest"
    tone_res = query_hf({"inputs": clean_text[:500]}, tone_model)
    
    tone = "Neutral"
    if tone_res and isinstance(tone_res, list) and isinstance(tone_res[0], list):
        # HF returns [[{'label': 'positive', 'score': 0.9}]]
        scores = tone_res[0]
        top_score = max(scores, key=lambda x: x['score'])
        tone = top_score['label'].capitalize()

    # 4. Generate Simple Reply
    reply = ""
    if urgency_level == "High":
        reply = f"Dear Sender,\n\nWe have received your urgent message regarding '{summary[:30]}...'. Our team is prioritizing this and will respond shortly.\n\nBest regards,"
    elif tone == "Negative":
        reply = f"Dear Sender,\n\nI apologize for the issues raised regarding '{summary[:30]}...'. We are looking into this immediately to resolve it.\n\nBest regards,"
    else:
        reply = f"Dear Sender,\n\nThank you for your email. We have noted the details: {summary[:50]}...\n\nWe will get back to you soon.\n\nBest regards,"

    return {
        "summary": summary,
        "urgency": urgency_level,
        "tone": tone,
        "reply": reply
    }
