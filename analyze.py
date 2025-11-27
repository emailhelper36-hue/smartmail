import os
import requests
import json
import re

# --- CONFIGURATION ---
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_REFERER_URL = os.environ.get("OPENROUTER_REFERER_URL")

# Set the model you requested as the default
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-3n-e4b-it:free")

def query_openrouter_json(system_prompt, user_prompt):
    """
    Queries OpenRouter and expects a JSON response.
    """
    if not OPENROUTER_API_KEY:
        print("⚠️ OPENROUTER_API_KEY is missing.")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERER_URL or "https://smartmail-bot.onrender.com"
    }
    
    payload = {
        "model": OPENROUTER_MODEL, 
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3, # Lower temperature for consistent, structured output
        "max_tokens": 300,
        "response_format": { "type": "json_object" } # Force JSON if model supports it, otherwise prompt handles it
    }
    
    try:
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=45)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('choices'):
                content = data['choices'][0]['message']['content'].strip()
                # Clean up potential markdown code blocks if the model adds them
                if content.startswith("```json"):
                    content = content.replace("```json", "").replace("```", "")
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

    clean_text = text[:2500] # Truncate to safe limit

    # The "One Prompt to Rule Them All"
    system_prompt = """You are an expert email analysis AI. 
    Analyze the incoming email and return a valid JSON object with these exact keys:
    {
        "summary": "1-2 sentence summary",
        "tone": "One of [Positive, Negative, Neutral]",
        "urgency": "One of [High, Medium, Low]",
        "key_points": ["List of 1-3 key action items or important details"],
        "suggested_reply": "A concise, professional response (under 75 words) signed off as 'Support Team'"
    }
    Do not include any extra text or markdown. Just the JSON."""

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
            # If JSON parsing fails, dump the raw text into summary so you see what happened
            result["summary"] = "AI format error"
            result["suggested_reply"] = result_json_str[:200]

    return result
