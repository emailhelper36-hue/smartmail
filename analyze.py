import os
import requests
from utils import simple_sentence_split, first_n_sentences

# 1. SECURE SETUP
HF_TOKEN = os.environ.get("HF_TOKEN") 

API_URL_SUM = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
API_URL_TONE = "https://api-inference.huggingface.co/models/cardiffnlp/twitter-roberta-base-sentiment"

# --- KEYWORD LISTS ---
ANGRY_KEYWORDS = ["unacceptable", "angry", "frustrat", "disappointed", "terrible", "worst", "hate", "cancel", "critical", "emergency", "urgent", "immediately"]
URGENT_KEYWORDS = ["urgent", "asap", "immediately", "deadline", "critical", "now", "emergency", "must", "required", "essential"]
POSITIVE_KEYWORDS = ["happy", "great", "excellent", "love", "good", "thanks", "wonderful", "best", "thrilled", "nice", "appreciate"]

def query_hf_api(payload, api_url):
    if not HF_TOKEN: return None
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        print(f"HF API Error: {e}")
        return None

def summarize_text(text):
    """Better summarization that handles long texts"""
    try:
        # If text is too long, take first 1000 chars for summarization
        if len(text) > 1000:
            text_for_summary = text[:1000] + "..."
        else:
            text_for_summary = text
            
        output = query_hf_api({"inputs": text_for_summary, "max_length": 150, "min_length": 30}, API_URL_SUM)
        
        if output and isinstance(output, list) and 'summary_text' in output[0]:
            return output[0]['summary_text']
        else:
            # Fallback: first 2 sentences
            return first_n_sentences(text, 2)
    except Exception as e:
        print(f"Summarization error: {e}")
        return first_n_sentences(text, 2)

def classify_tone_urgency(text):
    text_lower = text.lower()
    
    # 1. Urgency Detection (Enhanced)
    urgency = "Low"
    urgent_count = 0
    for k in URGENT_KEYWORDS:
        if k in text_lower: 
            urgent_count += 1
            
    if urgent_count >= 3:
        urgency = "High"
    elif urgent_count >= 1:
        urgency = "Medium"
    
    # 2. Tone Detection (Improved)
    tone = "Neutral"
    
    # Check for URGENT/CRITICAL tone first (this was missing!)
    critical_words = ["critical", "emergency", "urgent", "immediately", "must", "required", "deadline"]
    critical_count = sum(1 for word in critical_words if word in text_lower)
    
    if critical_count >= 2:
        tone = "Urgent"
    else:
        # Use AI for sentiment
        try:
            output = query_hf_api({"inputs": text}, API_URL_TONE)
            if output and isinstance(output, list) and isinstance(output[0], list):
                scores = output[0]
                top = max(scores, key=lambda x: x['score'])
                
                if top['label'] == 'LABEL_0': 
                    tone = "Negative"
                elif top['label'] == 'LABEL_2': 
                    tone = "Positive"
                
                # Only trust AI if confidence is high
                if top['score'] < 0.7: 
                    tone = "Neutral"
        except:
            pass
        
        # Keyword fallback (only if AI is uncertain)
        if tone == "Neutral":
            # Check Angry/Negative first
            angry_count = sum(1 for k in ANGRY_KEYWORDS if k in text_lower)
            positive_count = sum(1 for k in POSITIVE_KEYWORDS if k in text_lower)
            
            if angry_count > positive_count:
                tone = "Negative"
            elif positive_count > angry_count:
                tone = "Positive"
    
    return tone, urgency

def extract_action_items(text):
    # Simple action item extraction
    action_items = []
    sentences = simple_sentence_split(text)
    
    for sentence in sentences:
        sentence_lower = sentence.lower()
        if any(word in sentence_lower for word in ["must", "required to", "need to", "should", "please", "action required"]):
            action_items.append(sentence[:100])  # First 100 chars
    
    return action_items[:3]  # Max 3 action items

def analyze_text(text):
    # Clean the text first
    clean_text = ' '.join(text.split()[:2000])  # Limit to 2000 words
    
    summary = summarize_text(clean_text)
    tone, urgency = classify_tone_urgency(clean_text)
    action_items = extract_action_items(clean_text)
    
    # BETTER REPLY GENERATION
    if tone == "Urgent" or tone == "Negative":
        if "security" in clean_text.lower():
            reply = "We are addressing this security emergency immediately. Our team is deploying the critical patches and will provide updates every 2 hours. All departments have been notified to comply with the security protocols."
        else:
            reply = f"We are taking immediate action on this urgent matter. {summary} Our team is prioritizing this and will provide regular updates."
    
    elif tone == "Positive":
        reply = f"Thank you for the positive feedback! {summary} We appreciate your kind words and will continue to provide excellent service."
    
    else:  # Neutral
        reply = f"Received and noted. {summary} We will review this and get back to you shortly."
    
    return {
        "summary": summary, 
        "tone": tone, 
        "urgency": urgency, 
        "suggested_reply": reply, 
        "action_items": action_items
    }
