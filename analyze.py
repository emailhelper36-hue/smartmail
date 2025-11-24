import os
import requests
import re

# --- CONFIGURATION ---
HF_TOKEN = os.environ.get("HF_TOKEN")
# Faster router endpoints
API_BASE = "https://router.huggingface.co"
API_URL_SUM = f"{API_BASE}/facebook/bart-large-cnn"
API_URL_TONE = f"{API_BASE}/cardiffnlp/twitter-roberta-base-sentiment-latest"

# --- YOUR ORIGINAL KEYWORDS ---
URGENT_KEYWORDS = {
    "high": ["urgent", "emergency", "critical", "asap", "immediately", "now", "deadline", "breach"],
    "medium": ["important", "priority", "required", "must", "essential", "attention"]
}

TONE_KEYWORDS = {
    "angry": ["angry", "frustrated", "disappointed", "unacceptable", "terrible", "worst", "hate", "complaint", "fail"],
    "positive": ["thank", "appreciate", "great", "excellent", "good", "happy", "pleased", "wonderful", "love"],
    "urgent": ["urgent", "critical", "emergency", "immediately", "asap"]
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
    """Robust API call with timeout"""
    if not HF_TOKEN: return None
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=8)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

# --- CORE LOGIC ---

def smart_summarize(text):
    """Try AI summary, fallback to first 2 sentences"""
    if len(text) < 100: return text.strip()
    
    try:
        # Limit input for free tier speed
        input_text = text[:1024]
        result = query_hf_api({"inputs": input_text}, API_URL_SUM)
        
        if result and isinstance(result, list) and 'summary_text' in result[0]:
            return result[0]['summary_text'].strip()
    except: pass
    
    return first_n_sentences(text, 2)

def analyze_tone_urgency(text):
    """Hybrid: Keywords + AI validation"""
    text_lower = text.lower()
    
    # 1. Check Urgency (Keywords are best for business logic)
    urgency_score = 0
    for w in URGENT_KEYWORDS["high"]:
        if w in text_lower: urgency_score += 2
    for w in URGENT_KEYWORDS["medium"]:
        if w in text_lower: urgency_score += 1
        
    urgency = "High" if urgency_score >= 2 else ("Medium" if urgency_score == 1 else "Low")

    # 2. Check Tone (AI + Keyword Fallback)
    tone = "Neutral"
    ai_tone = None
    
    try:
        res = query_hf_api({"inputs": text[:512]}, API_URL_TONE)
        if res and isinstance(res, list) and isinstance(res[0], list):
            top = max(res[0], key=lambda x: x['score'])
            if top['score'] > 0.6: 
                label_map = {'negative': 'Negative', 'positive': 'Positive', 'neutral': 'Neutral'}
                ai_tone = label_map.get(top['label'].lower())
    except: pass

    if ai_tone:
        tone = ai_tone
    else:
        # Fallback to keywords
        neg_count = sum(1 for w in TONE_KEYWORDS["angry"] if w in text_lower)
        pos_count = sum(1 for w in TONE_KEYWORDS["positive"] if w in text_lower)
        
        if neg_count > pos_count: tone = "Negative"
        elif pos_count > neg_count: tone = "Positive"

    # Override: High Urgency often implies "Urgent" tone
    if urgency == "High" and tone == "Neutral":
        tone = "Urgent"

    return tone, urgency

def extract_key_points(text):
    """Find action items"""
    sentences = simple_sentence_split(text)
    key_points = []
    triggers = ["must", "should", "need", "please", "deadline", "?", "action", "verify"]
    
    for s in sentences[:10]: 
        if any(t in s.lower() for t in triggers):
            clean = s[:120].strip()
            if len(clean) > 10:
                prefix = "❓ " if "?" in clean else "• "
                key_points.append(f"{prefix}{clean}")
                
    return key_points[:3]

def generate_contextual_reply(tone, urgency, summary, key_points):
    """High-Quality Reply Generation"""
    s_lower = summary.lower()
    
    # 1. Security/Critical
    if "security" in s_lower or "breach" in s_lower or "hack" in s_lower:
        return f"🚨 **Security Alert:** We have received your report regarding '{summary[:50]}...'. Our security team has been notified immediately and is investigating. Expect an update within 1 hour."

    # 2. Technical Issues
    if "server" in s_lower or "down" in s_lower or "error" in s_lower or "bug" in s_lower or "crash" in s_lower:
        return f"🔧 **Support Update:** We are aware of the issue: '{summary[:50]}...'. Our engineering team is looking into it now. We will update you shortly."

    # 3. Financial/Refund
    if "refund" in s_lower or "bill" in s_lower or "charge" in s_lower or "payment" in s_lower:
        return f"💳 **Billing Support:** Thank you for contacting us about the billing matter. We are reviewing the transaction details and will get back to you within 24 hours."

    # 4. General Tones
    if tone == "Negative" or tone == "Angry":
        return f"🤝 **Apology:** I am very sorry to hear about your experience regarding '{summary[:30]}...'. I am escalating this to management to ensure it is resolved immediately."
        
    if tone == "Positive":
        return f"🌟 **Thank You:** We are thrilled to hear your feedback! '{summary[:50]}...'. We shared this with the team. Thanks for being a great customer!"

    # 5. Default
    reply = f"Thank you for your email regarding '{summary[:50]}...'. We have received it and will respond shortly."
    if key_points:
        reply += "\n\nWe noted these key points:\n" + "\n".join(key_points)
        
    return reply

def analyze_text(text):
    """Main Entry Point"""
    if not text or len(text.strip()) < 5:
        return {
            "summary": "Content unavailable for analysis.", "tone": "Neutral", "urgency": "Low", 
            "suggested_reply": "Please check the email content.", "key_points": []
        }

    # Clean text
    clean_text = " ".join(text.split())[:2000]
    
    # Run analysis components
    summary = smart_summarize(clean_text)
    tone, urgency = analyze_tone_urgency(clean_text)
    key_points = extract_key_points(clean_text)
    reply = generate_contextual_reply(tone, urgency, summary, key_points)
    
    return {
        "summary": summary,
        "tone": tone,
        "urgency": urgency,
        "suggested_reply": reply,
        "key_points": key_points
    }
