import os
import requests
import re
from utils import simple_sentence_split, first_n_sentences

# Free Hugging Face models that work well
HF_TOKEN = os.environ.get("HF_TOKEN")

# Better models for free tier
API_URL_SUM = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
API_URL_TONE = "https://api-inference.huggingface.co/models/cardiffnlp/twitter-roberta-base-sentiment-latest"
API_URL_KEYWORDS = "https://api-inference.huggingface.co/models/yanekyuk/bert-keyword-extractor"

# Enhanced keyword lists
URGENT_KEYWORDS = {
    "high": ["urgent", "emergency", "critical", "asap", "immediately", "now", "deadline"],
    "medium": ["important", "priority", "required", "must", "essential", "deadline"]
}

TONE_KEYWORDS = {
    "angry": ["angry", "frustrated", "disappointed", "unacceptable", "terrible", "worst", "hate", "complaint"],
    "positive": ["thank", "appreciate", "great", "excellent", "good", "happy", "pleased", "wonderful"],
    "urgent": ["urgent", "critical", "emergency", "immediately", "asap"]
}

def query_hf_api(payload, api_url, max_retries=2):
    """Robust API call with retries"""
    if not HF_TOKEN: 
        return None
        
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    for attempt in range(max_retries):
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=15)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 503:
                # Model loading, wait and retry
                if attempt < max_retries - 1:
                    import time
                    time.sleep(5)
                    continue
            else:
                print(f"API Error {response.status_code}: {response.text}")
                return None
                
        except requests.exceptions.Timeout:
            print(f"Timeout on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                continue
        except Exception as e:
            print(f"API Exception: {e}")
            return None
    
    return None

def smart_summarize(text):
    """Better summarization with fallbacks"""
    if len(text) < 100:
        return text.strip()
    
    # Try BART summarization first
    try:
        # Use first 1024 chars for free tier limits
        input_text = text[:1024] if len(text) > 1024 else text
        
        result = query_hf_api(
            {"inputs": input_text, "max_length": 150, "min_length": 30, "do_sample": False},
            API_URL_SUM
        )
        
        if result and isinstance(result, list) and 'summary_text' in result[0]:
            summary = result[0]['summary_text'].strip()
            if summary and len(summary) > 20:
                return summary
    except Exception as e:
        print(f"Summarization error: {e}")
    
    # Fallback: extract key sentences
    sentences = simple_sentence_split(text)
    if len(sentences) >= 3:
        return " ".join(sentences[:2])
    elif sentences:
        return sentences[0]
    else:
        return first_n_sentences(text, 1)

def analyze_tone_urgency(text):
    """Enhanced tone and urgency detection"""
    text_lower = text.lower()
    
    # Urgency detection
    urgency_score = 0
    for word in URGENT_KEYWORDS["high"]:
        if word in text_lower:
            urgency_score += 2
    
    for word in URGENT_KEYWORDS["medium"]:
        if word in text_lower:
            urgency_score += 1
    
    if urgency_score >= 3:
        urgency = "High"
    elif urgency_score >= 1:
        urgency = "Medium"
    else:
        urgency = "Low"
    
    # Tone detection with keyword analysis first
    tone_scores = {"urgent": 0, "angry": 0, "positive": 0, "neutral": 0}
    
    for tone, keywords in TONE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text_lower:
                tone_scores[tone] += 1
    
    # Try AI sentiment analysis
    try:
        sentiment_result = query_hf_api({"inputs": text[:512]}, API_URL_TONE)
        if sentiment_result and isinstance(sentiment_result, list):
            scores = sentiment_result[0]
            top_label = max(scores, key=lambda x: x['score'])
            
            if top_label['score'] > 0.7:
                label_map = {
                    'negative': 'angry',
                    'positive': 'positive', 
                    'neutral': 'neutral'
                }
                ai_tone = label_map.get(top_label['label'].lower(), 'neutral')
                tone_scores[ai_tone] += 2
    except:
        pass
    
    # Determine final tone
    if tone_scores["urgent"] >= 2 or urgency == "High":
        tone = "Urgent"
    elif tone_scores["angry"] > tone_scores["positive"]:
        tone = "Negative"
    elif tone_scores["positive"] > tone_scores["angry"]:
        tone = "Positive"
    else:
        tone = "Neutral"
    
    return tone, urgency

def extract_key_points(text):
    """Extract key action items and important points"""
    sentences = simple_sentence_split(text)
    key_points = []
    
    action_indicators = ["must", "should", "need to", "please", "required", "action", "deadline", "urgent"]
    question_indicators = ["?", "how", "what", "when", "where", "why"]
    
    for sentence in sentences[:6]:  # Check first 6 sentences
        sentence_lower = sentence.lower()
        
        # Check for action items
        if any(indicator in sentence_lower for indicator in action_indicators):
            clean_point = sentence.strip()[:120]
            if len(clean_point) > 20:
                key_points.append(clean_point)
        
        # Check for questions
        elif any(indicator in sentence_lower for indicator in question_indicators):
            clean_point = sentence.strip()[:120]
            if len(clean_point) > 20:
                key_points.append(f"❓ {clean_point}")
    
    return key_points[:3]  # Max 3 key points

def generate_contextual_reply(tone, urgency, summary, key_points):
    """Generate much better contextual replies"""
    
    if tone == "Urgent" or urgency == "High":
        if any(word in summary.lower() for word in ["security", "breach", "hack"]):
            reply = "🚨 **Security Response:** We're addressing this critical security issue immediately. Our security team has been alerted and is implementing emergency protocols. We'll provide updates every hour."
        elif any(word in summary.lower() for word in ["server", "system", "outage", "down"]):
            reply = "🔧 **Technical Response:** Our engineering team is investigating this system issue immediately. We're working to restore services and will update you within 30 minutes."
        else:
            reply = f"⚡ **Urgent Response:** We're prioritizing this immediately. {summary} Our team is taking action now and will provide a detailed update within the hour."
    
    elif tone == "Negative":
        if any(word in summary.lower() for word in ["refund", "money", "payment"]):
            reply = "💳 **Billing Response:** I apologize for the billing issue. Our finance team will review this immediately and contact you within 24 hours to resolve it."
        elif any(word in summary.lower() for word in ["bug", "error", "not working"]):
            reply = "🐛 **Support Response:** I'm sorry you're experiencing technical issues. Our support team is investigating and will provide a solution within 2-4 hours."
        else:
            reply = f"😔 **Service Response:** I apologize for the frustration. {summary} We're addressing this and will follow up with a solution by tomorrow."
    
    elif tone == "Positive":
        if any(word in summary.lower() for word in ["thank", "appreciate"]):
            reply = "🌟 **Appreciation Response:** Thank you for your kind words! We're delighted to hear about your positive experience and will share your feedback with the team."
        else:
            reply = f"😊 **Positive Response:** We're thrilled to hear this! {summary} Your satisfaction is our priority, and we look forward to serving you again."
    
    else:  # Neutral
        if key_points and any("?" in point for point in key_points):
            reply = f"🤔 **Inquiry Response:** Thanks for your question. {summary} We're reviewing this and will get back to you with a detailed answer by EOD."
        else:
            reply = f"📝 **General Response:** Thank you for your message. {summary} We've noted your request and will respond with updates within 24 hours."
    
    # Add key points if available
    if key_points:
        reply += "\n\n**Key points we're addressing:**\n" + "\n".join(f"• {point}" for point in key_points)
    
    return reply

def analyze_text(text):
    """Main analysis function with improved output"""
    if not text or len(text.strip()) < 10:
        return {
            "summary": "No meaningful content to analyze",
            "tone": "Neutral",
            "urgency": "Low",
            "suggested_reply": "Please provide more details for analysis.",
            "key_points": []
        }
    
    # Clean and prepare text
    clean_text = ' '.join(text.split()[:1500])  # Limit length
    
    # Get all analysis components
    summary = smart_summarize(clean_text)
    tone, urgency = analyze_tone_urgency(clean_text)
    key_points = extract_key_points(clean_text)
    
    # Generate contextual reply
    suggested_reply = generate_contextual_reply(tone, urgency, summary, key_points)
    
    return {
        "summary": summary,
        "tone": tone,
        "urgency": urgency,
        "suggested_reply": suggested_reply,
        "key_points": key_points
    }
