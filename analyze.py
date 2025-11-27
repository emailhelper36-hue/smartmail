import os
import requests
import re
import time
import json

# --- CONFIGURATION ---
# Hugging Face (Used for stable Bart Summary only)
HF_TOKEN = os.environ.get("HF_TOKEN")
API_BASE_INF = "https://api-inference.huggingface.co/models"
API_URL_SUM = f"{API_BASE_INF}/facebook/bart-large-cnn" 

# --- OPENROUTER CONFIG ---
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions" 
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
# Using the specific 20B GPT-OSS model for high quality
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-20b:free") 
OPENROUTER_REFERER_URL = os.environ.get("OPENROUTER_REFERER_URL") 

# --- LOCAL KEYWORDS & WEIGHTS ---
URGENCY_WEIGHTS = {
    "urgent": 3, "emergency": 4, "critical": 3, "asap": 2, 
    "immediately": 2, "deadline": 2, "breach": 4, "act now": 3
}

TONE_WEIGHTS = {
    "negative": {"unacceptable": 3, "frustrated": 2, "worst": 3, "fail": 2, "complaint": 1},
    "positive": {"thank": 2, "appreciate": 2, "excellent": 3, "great": 2, "outstanding": 3, "love": 1}
}

# --- HELPER FUNCTIONS ---
def simple_sentence_split(text):
    if not text: return []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]

def first_n_sentences(text, n=2):
    sentences = simple_sentence_split(text)
    return " ".join(sentences[:n])

def query_hf_api(payload, api_url, retries=1, timeout=20):
    """Robust API Query for Bart/Summary (Stable)."""
    if not HF_TOKEN: return None
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    for attempt in range(retries + 1):
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 503:
                data = response.json()
                wait_time = data.get("estimated_time", 15)
                time.sleep(wait_time) 
                continue 
            return None
        except Exception:
            pass
    return None

def query_openrouter_api(tone, urgency, summary):
    """
    Calls OpenRouter API for dynamic reply generation.
    """
    if not OPENROUTER_API_KEY or not OPENROUTER_MODEL:
        return None

    instruction = "Write a professional, concise email reply."
    if tone == "Positive": instruction = "Write a warm thank-you email acknowledging positive feedback."
    elif tone == "Negative": instruction = "Write an empathetic apology and address the frustration."
    elif urgency == "High" or tone == "Urgent": instruction = "Write a reassuring email stating the issue is prioritized immediately."
    
    # Prepare the prompt for the model
    system_prompt = "You are a highly efficient customer support agent. Your reply must be EXTREMELY BRIEF (MAX 4 SENTENCES) and sign off as 'Support Team'."
    user_prompt = f"Task: {instruction}\nSummary of Email: '{summary}'"
    
    payload = {
        "model": OPENROUTER_MODEL, 
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 80, 
        "stream": False 
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERER_URL or "https://smartmail-bot.onrender.com"
    }
    
    try:
        # Use short timeout as OpenRouter is fast
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=20) 
        
        if response.status_code == 200:
            data = response.json()
            if data.get('choices'):
                text = data['choices'][0]['message']['content']
                return text.strip()
        
        # Log error for debugging if the call fails instantly
        print(f"❌ OpenRouter API Failed ({response.status_code}): {response.text}")
        
    except Exception as e:
        print(f"❌ OpenRouter Connection Error: {e}")

    return None

# --- ANALYSIS LOGIC ---

def get_summary(text):
    if len(text) < 50: return text.strip()
    result = query_hf_api({"inputs": text[:1500]}, API_URL_SUM, retries=1)
    if result and isinstance(result, list) and 'summary_text' in result[0]:
        return result[0]['summary_text'].strip()
    return first_n_sentences(text, 2)

def get_tone_urgency_local(text):
    text_lower = text.lower()
    
    # 1. URGENCY
    urgency_score = 0
    for keyword, weight in URGENCY_WEIGHTS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", text_lower):
            urgency_score += weight
    
    urgency = "Low"
    if urgency_score >= 5: urgency = "High"
    elif urgency_score >= 2: urgency = "Medium"

    # 2. TONE (Local Scoring)
    tone_score = 0
    for keyword, weight in TONE_WEIGHTS["positive"].items():
        if re.search(rf"\b{re.escape(keyword)}\b", text_lower):
            tone_score += weight
    for keyword, weight in TONE_WEIGHTS["negative"].items():
        if re.search(rf"\b{re.escape(keyword)}\b", text_lower):
            tone_score -= weight

    tone = "Neutral"
    if tone_score >= 3: tone = "Positive"
    elif tone_score <= -3: tone = "Negative"
    
    if urgency == "High" and tone == "Neutral":
        tone = "Urgent"

    return tone, urgency

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


def generate_reply(tone, urgency, summary):
    """
    Tries OpenRouter Generation first. If it fails, returns an error message.
    """
    
    # --- 1. OPENROUTER GENERATION ATTEMPT ---
    ai_response = query_openrouter_api(tone, urgency, summary)

    if ai_response:
        return ai_response
    
    # --- 2. FAIL SAFELY (NO FALLBACK TEMPLATE) ---
    return "AI Generator Unavailable: Please check API Key or OpenRouter status."


# --- MAIN FUNCTION ---
def analyze_text(text):
    if not text or len(text.strip()) < 5:
        return {
            "summary": "Content unavailable.", "tone": "Neutral", "urgency": "Low", 
            "suggested_reply": "Please check content.", "key_points": []
        }

    clean_text = " ".join(text.split())[:2500]
    
    summary = get_summary(clean_text)
    tone, urgency = get_tone_urgency_local(clean_text)
    reply = generate_reply(tone, urgency, summary)
    key_points = extract_key_points(clean_text)
    
    return {
        "summary": summary,
        "tone": tone,
        "urgency": urgency,
        "suggested_reply": reply,
        "key_points": key_points
    }
