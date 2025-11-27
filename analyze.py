import os
import requests
import json
import re

# --- CONFIGURATION ---
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_REFERER_URL = os.environ.get("OPENROUTER_REFERER_URL")

# --- MODEL PRIORITY LIST ---
# If the first one fails/is busy, it automatically tries the next one.
# All of these are free and instruction-tuned.
FREE_MODEL_LIST = [
    "google/gemma-3n-e4b-it:free",        # Primary: Fast & Current favorite
    "mistralai/mistral-7b-instruct:free", # Backup 1: Reliable standard
    "huggingfaceh4/zephyr-7b-beta:free",  # Backup 2: Good for chat
    "meta-llama/llama-3-8b-instruct:free" # Backup 3: High quality
]

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
    Queries OpenRouter with automatic failover to backup models.
    """
    if not OPENROUTER_API_KEY:
        print("⚠️ OPENROUTER_API_KEY is missing.")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERER_URL or "https://smartmail-bot.onrender.com"
    }
    
    # Combine prompts once to use for all models
    combined_prompt = f"{system_prompt}\n\n---\n\nTask Context:\n{user_prompt}"

    # Loop through our list of free models
    for model_id in FREE_MODEL_LIST:
        print(f"🤖 Trying Model: {model_id}")
        
        payload = {
            "model": model_id, 
            "messages": [{"role": "user", "content": combined_prompt}],
            "temperature": 0.3, 
            "max_tokens": 300
        }
        
        try:
            # 20s timeout is enough for these fast models
            response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=20)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('choices'):
                    content = data['choices'][0]['message']['content'].strip()
                    # Clean up markdown code blocks
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.replace("```", "").strip()
                    return content
            
            # If we get a rate limit (429) or server error (5xx), log it and loop to the next model
            print(f"⚠️ Model {model_id} Failed ({response.status_code}). Switching to backup...")
            
        except Exception as e:
            print(f"❌ Connection Error on {model_id}: {e}")
            continue # Try next model

    print("❌ All models failed.")
    return None

# --- MAIN AI LOGIC ---

def analyze_text(text):
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
    IMPORTANT: Output ONLY the raw JSON. Do not include markdown formatting."""

    user_prompt = f"Analyze this email content:\n\n{clean_text}"

    # Call AI with Failover
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
            # Clean up any thinking tags (DeepSeek artifact) just in case we fallback to a thinking model
            result_json_str = re.sub(r'<think>.*?</think>', '', result_json_str, flags=re.DOTALL).strip()
            
            # Find the JSON object if there is extra text around it
            json_match = re.search(r'\{.*\}', result_json_str, re.DOTALL)
            if json_match:
                result_json_str = json_match.group(0)

            parsed = json.loads(result_json_str)
            result["summary"] = parsed.get("summary", "No summary")
            result["tone"] = parsed.get("tone", "Neutral")
            result["urgency"] = parsed.get("urgency", "Low")
            result["suggested_reply"] = parsed.get("suggested_reply", "No draft generated.")
            result["key_points"] = parsed.get("key_points", [])
        except json.JSONDecodeError:
            print(f"⚠️ Failed to parse JSON from AI: {result_json_str}")
            result["summary"] = "AI format error"
            result["suggested_reply"] = result_json_str[:200]

    return result
