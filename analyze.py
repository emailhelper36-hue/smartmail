import os
import requests
import re
import time

# --- CONFIGURATION ---
HF_TOKEN = os.environ.get("HF_TOKEN")
# Changed to the stable Inference API base
API_BASE_INF = "https://api-inference.huggingface.co/models"

# 1. Summarization (Stable)
API_URL_SUM = f"{API_BASE_INF}/facebook/bart-large-cnn"
# 2. Sentiment (Stable)
API_URL_TONE = f"{API_BASE_INF}/cardiffnlp/twitter-roberta-base-sentiment-latest"
# 3. AI Reply Generation (Zephyr model via standard endpoint)
API_URL_GEN = f"{API_BASE_INF}/HuggingFaceH4/zephyr-7b-beta" 

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

def query_hf_api(payload, api_url, retries=1, timeout=40): # Increased base timeout to 40s
    """
    Robust API Query: Used for all HF API calls.
    """
    if not HF_TOKEN:
        print("⚠️ HF_TOKEN missing")
        return None
        
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    for attempt in range(retries + 1):
        try:
            # Use specified timeout
            response = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            
            if response.status_code == 200:
                return response.json()
            
            # 503 (Model Loading) Handling
            if response.status_code == 503:
                data = response.json()
                wait_time = data.get("estimated_time", 15)
                time.sleep(wait_time) 
                continue 
            
            # Log the fast failure status code (e.g., 404 or 401)
            print(f"❌ Fast API Failure ({response.status_code}): Check endpoint or token.")
            return None
            
        except requests.exceptions.Timeout:
            pass
        except Exception:
            pass
            
    return None

# --- ANALYSIS LOGIC ---

def get_summary(text):
    if len(text) < 50: return text.strip()
    result = query_hf_api({"inputs": text[:1500]}, API_URL_SUM, retries=1)
    
    if result and isinstance(result, list) and 'summary_text' in result[0]:
        return result[0]['summary_text'].strip()
        
    return first_n_sentences(text, 2)

def get_tone_urgency(text):
    text_lower = text.lower()
    
    # 1. URGENCY
    urgency = "Low"
    for w in URGENT_KEYWORDS:
        if re.search(rf"\b{re.escape(w)}\b", text_lower):
            urgency = "High"
            break
            
    # 2. TONE (Hybrid)
    tone = "Neutral"
    neg_count = sum(1 for w in TONE_KEYWORDS["angry"] if re.search(rf"\b{re.escape(w)}\b", text_lower))
    pos_count = sum(1 for w in TONE_KEYWORDS["positive"] if re.search(rf"\b{re.escape(w)}\b", text_lower))
    
    keyword_tone = None
    if neg_count > 0 and neg_count > pos_count: keyword_tone = "Negative"
    elif pos_count > 0 and pos_count > neg_count: keyword_tone = "Positive"

    # AI Check
    ai_tone = None
    result = query_hf_api({"inputs": text[:512]}, API_URL_TONE, retries=1)
    if result and isinstance(result, list) and isinstance(result[0], list):
        scores = result[0]
        top = max(scores, key=lambda x: x['score'])
        label = top['label'].lower()
        if label == 'negative': ai_tone = "Negative"
        elif label == 'positive': ai_tone = "Positive"
        else: ai_tone = "Neutral"

    if keyword_tone: tone = keyword_tone
    elif ai_tone: tone = ai_tone
        
    if urgency == "High" and tone == "Neutral": tone = "Urgent"

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
    Tries AI Generation first (with a much longer timeout), falls back to stable templates.
    """
    
    # --- 1. AI GENERATION ATTEMPT ---
    instruction = "Write a polite customer support email reply."
    if tone == "Positive": instruction = "Write a warm 'Thank You' email acknowledging positive feedback."
    elif tone == "Negative": instruction = "Write an empathetic apology email addressing frustration."
    elif urgency == "High" or tone == "Urgent": instruction = "Write a reassuring email regarding the urgent issue. State that it is prioritized."
    
    # Zephyr Prompt Format
    prompt = f"""<|system|>
    You are a helpful customer support agent.
    Your goal is to write a professional email reply based on the summary and sentiment provided.
    Sign off as 'Support Team'.
    </s>
    <|user|>
    Summary: "{summary}"
    Sentiment: {tone}
    Task: {instruction}
    Keep it concise (max 75 words).
    </s>
    <|assistant|>"""
    
    # Try AI with much longer timeout (90s) and 2 retries
    result = query_hf_api({
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 200, 
            "return_full_text": False, 
            "temperature": 0.7,
            "top_p": 0.9
        }
    }, API_URL_GEN, retries=2, timeout=90) 

    if result and isinstance(result, list) and 'generated_text' in result[0]:
        return result[0]['generated_text'].strip()
    
    # --- 2. TEMPLATE FALLBACK ---
    if tone == "Positive":
        return "Thank you so much for your kind words! We are thrilled to hear your feedback and have shared it with the entire team. Thanks for being a great customer!\n\nBest regards,\nSupport Team"
    elif tone == "Negative":
        return "We sincerely apologize for the experience you have had. This is not the standard we strive for. We are investigating this matter immediately.\n\nBest regards,\nSupport Team"
    elif urgency == "High" or tone == "Urgent":
        return "We have received your urgent request. Our team has been notified and is prioritizing your case. Expect an update very soon.\n\nBest regards,\nSupport Team"
    
    return f"Thank you for your email, which we summarized as: '{summary}'. We have received your message and will respond to your inquiry shortly.\n\nBest regards,\nSupport Team"


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
    reply = generate_reply(tone, urgency, summary)
    key_points = extract_key_points(clean_text)
    
    return {
        "summary": summary,
        "tone": tone,
        "urgency": urgency,
        "suggested_reply": reply,
        "key_points": key_points
    }
