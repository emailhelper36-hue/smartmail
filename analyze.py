import os
import requests
import re

# --- CONFIGURATION ---
HF_TOKEN = os.environ.get("HF_TOKEN")
API_BASE = "https://router.huggingface.co"

# 1. Summarization Model
API_URL_SUM = f"{API_BASE}/facebook/bart-large-cnn"
# 2. Sentiment Model
API_URL_TONE = f"{API_BASE}/cardiffnlp/twitter-roberta-base-sentiment-latest"
# 3. Zero-Shot Classification (For Urgency)
API_URL_CLASS = f"{API_BASE}/facebook/bart-large-mnli"
# 4. Text Generation (For Replies) - Using Mistral for high-quality instruction following
API_URL_GEN = f"{API_BASE}/mistralai/Mistral-7B-Instruct-v0.3"

# --- HELPER FUNCTIONS ---
def simple_sentence_split(text):
    if not text: return []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]

def first_n_sentences(text, n=2):
    sentences = simple_sentence_split(text)
    return " ".join(sentences[:n])

def query_hf_api(payload, api_url):
    """Generic API Query function"""
    if not HF_TOKEN: return None
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        # Increased timeout for generation models
        response = requests.post(api_url, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            return response.json()
    except: pass
    return None

# --- AI CORE LOGIC ---

def get_ai_summary(text):
    """Generate Summary using BART"""
    if len(text) < 50: return text.strip()
    try:
        result = query_hf_api({"inputs": text[:1500]}, API_URL_SUM)
        if result and isinstance(result, list) and 'summary_text' in result[0]:
            return result[0]['summary_text'].strip()
    except: pass
    # Fallback if AI fails
    return first_n_sentences(text, 2)

def get_ai_tone(text):
    """Detect Tone using Roberta"""
    tone = "Neutral"
    try:
        result = query_hf_api({"inputs": text[:512]}, API_URL_TONE)
        if result and isinstance(result, list) and isinstance(result[0], list):
            # Get label with highest score
            scores = result[0]
            top = max(scores, key=lambda x: x['score'])
            
            label = top['label'].lower()
            if label == 'negative': tone = "Negative"
            elif label == 'positive': tone = "Positive"
            else: tone = "Neutral"
    except: pass
    return tone

def get_ai_urgency(text):
    """Detect Urgency using Zero-Shot Classification (No Keywords!)"""
    urgency = "Low"
    try:
        # We ask the AI to classify the text into one of these labels
        labels = ["Urgent", "Normal", "Low Priority"]
        payload = {
            "inputs": text[:1000],
            "parameters": {"candidate_labels": labels}
        }
        result = query_hf_api(payload, API_URL_CLASS)
        
        if result and 'labels' in result and 'scores' in result:
            top_label = result['labels'][0]
            top_score = result['scores'][0]
            
            # If confidence is decent, use the label
            if top_score > 0.4:
                if "Urgent" in top_label: urgency = "High"
                elif "Normal" in top_label: urgency = "Medium"
                else: urgency = "Low"
    except: pass
    return urgency

def generate_ai_reply(text, tone, urgency, summary):
    """Generate a Reply using Mistral LLM (No Templates!)"""
    
    # Construct a prompt for the AI
    prompt = f"""<s>[INST] You are a professional customer support agent. 
    Incoming Email Summary: "{summary}"
    Detected Tone: {tone}
    Detected Urgency: {urgency}
    
    Write a polite, professional, and concise email reply to the customer addressing their points. 
    Do not include placeholders like [Your Name]. Sign off as 'Support Team'. [/INST]"""

    try:
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 150, # Keep it concise
                "return_full_text": False,
                "temperature": 0.7
            }
        }
        result = query_hf_api(payload, API_URL_GEN)
        
        if result and isinstance(result, list) and 'generated_text' in result[0]:
            return result[0]['generated_text'].strip()
            
    except: pass
    
    # Fallback only if AI Generation completely fails
    return "Thank you for your email. We have received your message and will respond shortly. \n\nBest regards,\nSupport Team"

def extract_key_points(text):
    # We can keep this lightweight regex helper or replace it with AI too.
    # For speed, regex is often better for simple bullet points, but let's leave it simple.
    sentences = simple_sentence_split(text)
    key_points = []
    triggers = ["must", "should", "need", "please", "deadline", "?", "action"]
    for s in sentences[:12]: 
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

    # Clean text
    clean_text = " ".join(text.split())[:2000]
    
    # 1. Get Summary (AI)
    summary = get_ai_summary(clean_text)
    
    # 2. Get Tone (AI)
    tone = get_ai_tone(clean_text)
    
    # 3. Get Urgency (AI - Zero Shot)
    urgency = get_ai_urgency(clean_text)
    
    # 4. Generate Reply (AI - LLM)
    reply = generate_ai_reply(clean_text, tone, urgency, summary)
    
    # 5. Key points (Helper)
    key_points = extract_key_points(clean_text)
    
    return {
        "summary": summary,
        "tone": tone,
        "urgency": urgency,
        "suggested_reply": reply,
        "key_points": key_points
    }
