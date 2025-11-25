import os
import requests
import re

# --- CONFIGURATION ---
HF_TOKEN = os.environ.get("HF_TOKEN")
API_BASE = "https://router.huggingface.co"

# 1. Summarization
API_URL_SUM = f"{API_BASE}/facebook/bart-large-cnn"
# 2. Sentiment
API_URL_TONE = f"{API_BASE}/cardiffnlp/twitter-roberta-base-sentiment-latest"
# 3. Reply Generation (Mistral Instruct)
API_URL_GEN = f"{API_BASE}/mistralai/Mistral-7B-Instruct-v0.3"

# --- KEYWORDS ---
URGENT_KEYWORDS = [
    "urgent", "emergency", "critical", "asap", "immediately", 
    "deadline", "breach", "act now", "within 24 hours", "account locked"
]

TONE_KEYWORDS = {
    "angry": [
        "angry", "frustrated", "disappointed", "unacceptable", "terrible", 
        "worst", "hate", "complaint", "fail", "poor", "ridiculous", "scam"
    ],
    "positive": [
        "thank", "appreciate", "great", "excellent", "good", "happy", 
        "pleased", "wonderful", "love", "impressed", "outstanding", "kudos"
    ]
}

# --- HELPER FUNCTIONS ---
def simple_sentence_split(text):
    if not text: return []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]

def first_n_sentences(text, n=2):
    sentences = simple_sentence_split(text)
    return " ".join(sentences[:n])

def query_hf_api(payload, api_url):
    if not HF_TOKEN: return None
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        # Increased timeout to 20s to prevent cutoff
        response = requests.post(api_url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            return response.json()
    except: pass
    return None

# --- ANALYSIS LOGIC ---

def get_summary(text):
    if len(text) < 50: return text.strip()
    try:
        result = query_hf_api({"inputs": text[:1500]}, API_URL_SUM)
        if result and isinstance(result, list) and 'summary_text' in result[0]:
            return result[0]['summary_text'].strip()
    except: pass
    return first_n_sentences(text, 2)

def get_tone_urgency(text):
    text_lower = text.lower()
    
    # 1. URGENCY (Keywords First)
    urgency = "Low"
    for w in URGENT_KEYWORDS:
        if re.search(rf"\b{re.escape(w)}\b", text_lower):
            urgency = "High"
            break
            
    # 2. TONE (Hybrid)
    tone = "Neutral"
    
    # Keywords
    neg_count = sum(1 for w in TONE_KEYWORDS["angry"] if re.search(rf"\b{re.escape(w)}\b", text_lower))
    pos_count = sum(1 for w in TONE_KEYWORDS["positive"] if re.search(rf"\b{re.escape(w)}\b", text_lower))
    
    keyword_tone = None
    if neg_count > 0 and neg_count > pos_count: keyword_tone = "Negative"
    elif pos_count > 0 and pos_count > neg_count: keyword_tone = "Positive"

    # AI
    ai_tone = None
    try:
        result = query_hf_api({"inputs": text[:512]}, API_URL_TONE)
        if result and isinstance(result, list) and isinstance(result[0], list):
            scores = result[0]
            top = max(scores, key=lambda x: x['score'])
            label = top['label'].lower()
            if label == 'negative': ai_tone = "Negative"
            elif label == 'positive': ai_tone = "Positive"
            else: ai_tone = "Neutral"
    except: pass

    if keyword_tone: tone = keyword_tone
    elif ai_tone: tone = ai_tone
        
    if urgency == "High" and tone == "Neutral": tone = "Urgent"

    return tone, urgency

def generate_reply(text, tone, urgency, summary):
    """Generate a reply. If AI fails, use a clean, complete template."""
    
    instruction = "Write a polite and professional customer support email reply."
    if tone == "Positive":
        instruction = "Write a warm 'Thank You' email acknowledging the positive feedback."
    elif tone == "Negative":
        instruction = "Write an empathetic apology email addressing the frustration."
    elif urgency == "High":
        instruction = "Write a reassuring email acknowledging the urgency."
    
    prompt = f"""<s>[INST] You are a helpful customer support agent.
    Email Summary: "{summary}"
    Customer Sentiment: {tone}
    Priority Level: {urgency}
    
    Task: {instruction}
    Requirements:
    1. Keep it concise (under 100 words).
    2. Sign off as 'Support Team'.
    3. Ensure the response is complete sentences only.
    [/INST]"""

    try:
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 250, # Increased to ensure completion
                "return_full_text": False,
                "temperature": 0.7
            }
        }
        result = query_hf_api(payload, API_URL_GEN)
        if result and isinstance(result, list) and 'generated_text' in result[0]:
            return result[0]['generated_text'].strip()
    except: pass
    
    # --- ROBUST FALLBACKS (No more weird quotes) ---
    if tone == "Positive":
        return "Thank you so much for your kind words! We are thrilled to hear your feedback and have shared it with the entire team. Thanks for being a great customer!\n\nBest regards,\nSupport Team"
    elif tone == "Negative":
        return "We sincerely apologize for the experience you have had. This is not the standard we strive for. We are investigating this matter immediately and will get back to you shortly.\n\nBest regards,\nSupport Team"
    elif urgency == "High":
        return "We have received your urgent request. Our team has been notified and is prioritizing your case. Expect an update very soon.\n\nBest regards,\nSupport Team"
    
    return "Thank you for your email. We have received your message and will respond to your inquiry shortly.\n\nBest regards,\nSupport Team"

def extract_key_points(text):
    sentences = simple_sentence_split(text)
    key_points = []
    triggers = ["must", "should", "need", "please", "deadline", "?", "action"]
    for s in sentences[:15]: 
        if any(t in s.lower() for t in triggers):
            clean = s[:120].strip()
            if len(clean) > 10:
                prefix = "❓ " if "?" in clean else "• "
                key_points.append(f"{prefix}{clean}")
    return key_points[:3]

# --- MAIN FUNCTION ---
def analyze_text(text):
    if not text or len(text.strip()) < 5:
        return {
            "summary": "Content unavailable.", "tone": "Neutral", "urgency": "Low", 
            "suggested_reply": "Please check content.", "key_points": []
        }

    clean_text = " ".join(text.split())[:2500]
    
    summary = get_summary(clean_text)
    tone, urgency = get_tone_urgency(clean_text)
    reply = generate_reply(clean_text, tone, urgency, summary)
    key_points = extract_key_points(clean_text)
    
    return {
        "summary": summary,
        "tone": tone,
        "urgency": urgency,
        "suggested_reply": reply,
        "key_points": key_points
    }
