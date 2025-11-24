import os
import requests
from utils import simple_sentence_split, first_n_sentences

# 1. SECURE SETUP
HF_TOKEN = os.environ.get("HF_TOKEN") 

API_URL_SUM = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
API_URL_TONE = "https://api-inference.huggingface.co/models/cardiffnlp/twitter-roberta-base-sentiment"

# --- KEYWORD LISTS ---
ANGRY_KEYWORDS = ["unacceptable", "angry", "frustrat", "disappointed", "terrible", "worst", "hate", "cancel"]
URGENT_KEYWORDS = ["urgent", "asap", "immediately", "deadline", "critical", "now"]
# NEW: Added "Positive" keywords
POSITIVE_KEYWORDS = ["happy", "great", "excellent", "love", "good", "thanks", "wonderful", "best", "thrilled", "nice"]

def query_hf_api(payload, api_url):
    if not HF_TOKEN: return None
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=4)
        return response.json()
    except: return None

def summarize_text(text):
    try:
        output = query_hf_api({"inputs": text}, API_URL_SUM)
        if output and isinstance(output, list) and 'summary_text' in output[0]:
            return output[0]['summary_text']
    except: pass
    return first_n_sentences(text, 2)

def classify_tone_urgency(text):
    text_lower = text.lower()
    
    # 1. Urgency
    urgency = "Low"
    for k in URGENT_KEYWORDS:
        if k in text_lower: 
            urgency = "High"
            break
            
    # 2. Tone (AI First)
    tone = "Neutral"
    ai_success = False
    try:
        output = query_hf_api({"inputs": text}, API_URL_TONE)
        if output and isinstance(output, list) and isinstance(output[0], list):
            scores = output[0]
            top = max(scores, key=lambda x: x['score'])
            
            if top['label'] == 'LABEL_0': tone = "Angry/Negative"
            elif top['label'] == 'LABEL_2': tone = "Positive"
            
            # LOWERED THRESHOLD: 0.4 (Makes it more sensitive)
            if top['score'] > 0.4: ai_success = True
    except: pass

    # 3. Tone Fallback (THIS IS WHAT YOU WERE MISSING)
    # If AI failed OR AI thinks it's Neutral, we check keywords manually
    if not ai_success or tone == "Neutral":
        
        # Check Angry first
        for k in ANGRY_KEYWORDS:
            if k in text_lower: 
                tone = "Angry"
                break
        
        # Check Positive next (This fixes your issue)
        if tone != "Angry":
            for k in POSITIVE_KEYWORDS:
                if k in text_lower:
                    tone = "Positive"
                    break
                    
    return tone, urgency

def extract_action_items(text):
    return ["Check email for details"]

def analyze_text(text):
    summary = summarize_text(text)
    tone, urgency = classify_tone_urgency(text)
    
    if "Angry" in tone: reply = f"I apologize for the issue. {summary} Escalating now."
    elif "Positive" in tone: reply = f"Thanks for the kind words! {summary} We appreciate it."
    else: reply = f"Received. {summary} Will update shortly."
    
    return {
        "summary": summary, "tone": tone, "urgency": urgency, 
        "suggested_reply": reply, "action_items": []
    }
