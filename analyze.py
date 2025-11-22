import os
import requests
from utils import simple_sentence_split, first_n_sentences

# 1. SECURE SETUP: Get Token from Render Environment
# Do NOT paste your actual token here. Set 'HF_TOKEN' in Render Dashboard.
HF_TOKEN = os.environ.get("HF_TOKEN") 

API_URL_SUM = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
API_URL_TONE = "https://api-inference.huggingface.co/models/cardiffnlp/twitter-roberta-base-sentiment"

ANGRY_KEYWORDS = ["unacceptable", "angry", "frustrat", "frustration", "angrily", "outrage", "complain", "complaint", "disappointed", "cancel"]
URGENT_KEYWORDS = ["urgent", "asap", "today", "now", "immediately", "within an hour", "by EOD", "before", "deadline"]

def query_hf_api(payload, api_url):
    """Sends text to Hugging Face. Returns JSON or None if it fails."""
    if not HF_TOKEN:
        print("⚠️ WARNING: HF_TOKEN is missing in Environment Variables.")
        return None
        
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        # Timeout set to 4s to keep the bot snappy
        response = requests.post(api_url, headers=headers, json=payload, timeout=4)
        return response.json()
    except Exception as e:
        print(f"API Error: {e}")
        return None

def summarize_text(text: str) -> str:
    """Try AI Summary, Fallback to First 2 Sentences."""
    try:
        output = query_hf_api({"inputs": text}, API_URL_SUM)
        if output and isinstance(output, list) and 'summary_text' in output[0]:
            return output[0]['summary_text']
    except:
        pass
    # Fallback Logic (from utils.py)
    return first_n_sentences(text, 2)

def classify_tone_urgency(text: str):
    """Mix of Rule-Based (Speed) and AI (Accuracy)."""
    
    # 1. URGENCY (Rules are better/faster for this)
    urgency = "Low"
    text_lower = text.lower()
    for k in URGENT_KEYWORDS:
        if k in text_lower: 
            urgency = "High"
            break
    
    # 2. TONE (Try AI first)
    tone = "Neutral"
    ai_success = False
    
    try:
        output = query_hf_api({"inputs": text}, API_URL_TONE)
        if output and isinstance(output, list) and isinstance(output[0], list):
            # Output looks like [[{'label': 'LABEL_0', 'score': 0.9}, ...]]
            scores = output[0]
            top_score = max(scores, key=lambda x: x['score'])
            label = top_score['label']
            
            # Mapping roberta-base-sentiment labels
            if label == 'LABEL_0': tone = "Angry/Negative"
            elif label == 'LABEL_2': tone = "Positive"
            ai_success = True
    except:
        pass

    # Fallback if AI failed
    if not ai_success:
        for k in ANGRY_KEYWORDS:
            if k in text_lower: 
                tone = "Angry"
                break

    return tone, urgency

def extract_action_items(text: str):
    """Extracts sentences containing action verbs."""
    sents = simple_sentence_split(text)
    actions = []
    triggers = ["please", "need", "require", "send", "provide", "refund", "check", "escalate"]
    
    for s in sents:
        if any(t in s.lower() for t in triggers):
            cleaned = s.strip()
            if cleaned not in actions:
                actions.append(cleaned)
                
    # Return at least one item
    return actions if actions else ["Check conversation for details"]

def analyze_text(text: str):
    """Main function called by app.py"""
    summary = summarize_text(text)
    tone, urgency = classify_tone_urgency(text)
    actions = extract_action_items(text)
    
    # Template Reply based on analysis
    if "Angry" in tone or "Negative" in tone:
        reply = f"I sincerely apologize for the issue. {summary} I have escalated this to priority support."
    elif urgency == "High":
        reply = f"Thanks for the urgent update. {summary} We are looking into it right now."
    else:
        reply = f"Thank you for contacting us. {summary} I will get back to you shortly."
    
    return {
        "summary": summary,
        "tone": tone,
        "urgency": urgency,
        "action_items": actions,
        "suggested_reply": reply
    }