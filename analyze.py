import os
import requests
import json
import re

# --- CONFIGURATION ---
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_REFERER_URL = os.environ.get("OPENROUTER_REFERER_URL")

# Force the correct model ID as default if ENV is missing or wrong
DEFAULT_MODEL = "google/gemma-3n-e4b-it:free"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)

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

def query_openrouter_json(system_prompt, user_prompt):
    """
    Queries OpenRouter. Removed strict JSON mode enforcement to fix 400 error.
    """
    if not OPENROUTER_API_KEY:
        print("⚠️ OPENROUTER_API_KEY is missing.")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERER_URL or "https://smartmail-bot.onrender.com"
    }
    
    print(f"🤖 Using Model: {OPENROUTER_MODEL}") 

    # Combined prompt for models that don't support 'system' role well
    combined_prompt = f"{system_prompt}\n\n---\n\nTask Context:\n{user_prompt}"

    payload = {
        "model": OPENROUTER_MODEL, 
        "messages": [
            {"role": "user", "content": combined_prompt}
        ],
        "temperature": 0.3, 
        "max_tokens": 300,
        # REMOVED: "response_format": { "type": "json_object" } 
        # This caused the 400 error on Gemma 3n
    }
    
    try:
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=45)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('choices'):
                content = data['choices'][0]['message']['content'].strip()
                # Clean up markdown code blocks if the model adds them
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.replace("```", "").strip()
                return content
        
        print(f"❌ OpenRouter Error ({response.status_code}): {response.text}")
        return None
        
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return None

# --- MAIN AI LOGIC ---

def analyze_text(text):
    """
    Sends the text to AI to determine Summary, Tone, Urgency, and Draft in ONE go.
    """
    if not text or len(text.strip()) < 5:
        return {
            "summary": "No content.", "tone": "Neutral", "urgency": "Low", 
            "suggested_reply": "Please provide content.", "key_points": []
        }

    clean_text = text[:2500] 

    system_prompt = """You are an expert email analysis AI. 
    Analyze the incoming email and return a valid JSON object with these exact keys:
    {
        "summary": "1-2 sentence summary",
        "tone": "One of [Positive, Negative, Neutral]",
        "urgency": "One of [High, Medium, Low]",
        "key_points": ["List of 1-3 key action items or important details"],
        "suggested_reply": "A concise, professional response (under 75 words) signed off as 'Support Team'"
    }
    IMPORTANT: Output ONLY the raw JSON. Do not use Markdown formatting."""

    user_prompt = f"Analyze this email content:\n\n{clean_text}"

    # Call AI
    result_json_str = query_openrouter_json(system_prompt, user_prompt)

    # Default Safe Values
    result = {
        "summary": "Analysis failed.",
        "tone": "Neutral", 
        "urgency": "Low", 
        "suggested_reply": "Could not generate reply.",
        "key_points": []
    }

    # Parse AI Response
    if result_json_str:
        try:
            parsed = json.loads(result_json_str)
            result["summary"] = parsed.get("summary", "No summary")
            result["tone"] = parsed.get("tone", "Neutral")
            result["urgency"] = parsed.get("urgency", "Low")
            result["suggested_reply"] = parsed.get("suggested_reply", "No draft generated.")
            result["key_points"] = parsed.get("key_points", [])
        except json.JSONDecodeError:
            print(f"⚠️ Failed to parse JSON from AI: {result_json_str}")
            result["summary"] = "AI format error"
            result["suggested_reply"] = result_json_str[:200] # Show raw output for debugging

    return result
