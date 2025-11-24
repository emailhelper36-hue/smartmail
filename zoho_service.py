import os
import requests
import time

# --- CACHE (The "Memory") ---
TOKEN_CACHE = {"access_token": None, "expires_at": 0}
ACCOUNT_ID_CACHE = None 
EMAIL_LIST_CACHE = [] # Stores subjects to fix the "Unknown Email" bug

# --- CONFIG ---
API_DOMAIN = os.environ.get("ZOHO_API_DOMAIN", "https://mail.zoho.com").strip()
ACCOUNTS_URL = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").strip()

def get_access_token():
    global TOKEN_CACHE
    if TOKEN_CACHE["access_token"] and time.time() < (TOKEN_CACHE["expires_at"] - 300):
        return TOKEN_CACHE["access_token"]

    try:
        url = f"{ACCOUNTS_URL}/oauth/v2/token"
        params = {
            "refresh_token": os.environ.get("ZOHO_REFRESH_TOKEN", "").strip(),
            "client_id": os.environ.get("ZOHO_CLIENT_ID", "").strip(),
            "client_secret": os.environ.get("ZOHO_CLIENT_SECRET", "").strip(),
            "grant_type": "refresh_token"
        }
        resp = requests.post(url, params=params, timeout=15)
        data = resp.json()
        if "access_token" in data:
            TOKEN_CACHE["access_token"] = data["access_token"]
            TOKEN_CACHE["expires_at"] = time.time() + data.get("expires_in", 3600)
            return data["access_token"]
    except Exception as e:
        print(f"❌ Token Error: {e}")
    return None

def get_account_id():
    """Auto-detects Account ID (Fixes 400 Errors)"""
    global ACCOUNT_ID_CACHE
    if ACCOUNT_ID_CACHE: return ACCOUNT_ID_CACHE
    
    # Try Env
    env_id = os.environ.get("ZOHO_ACCOUNT_ID", "").strip()
    if env_id:
        ACCOUNT_ID_CACHE = env_id
        return env_id
        
    # Auto-detect if Env is missing/wrong
    token = get_access_token()
    if not token: return None
    try:
        url = f"{API_DOMAIN}/api/accounts"
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                real_id = str(data["data"][0].get("accountId"))
                ACCOUNT_ID_CACHE = real_id
                return real_id
    except: pass
    return None

def fetch_latest_emails(limit=5):
    """Fetches emails and POPULATES CACHE"""
    global EMAIL_LIST_CACHE
    token = get_access_token()
    account_id = get_account_id()
    
    if not token or not account_id: return []

    url = f"{API_DOMAIN}/api/accounts/{account_id}/messages/view"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    params = {"limit": limit, "sortorder": "false"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            messages = data.get("data", [])
            clean_list = []
            for msg in messages:
                subject = msg.get("subject", "No Subject")
                clean_list.append({
                    "subject": (subject[:25] + '..') if len(subject) > 25 else subject,
                    "full_subject": subject,
                    "messageId": msg.get("messageId")
                })
            EMAIL_LIST_CACHE = clean_list
            return clean_list
    except Exception as e:
        print(f"❌ List Error: {e}")
    return []

def find_message_id_by_subject(user_text):
    """
    1. Checks Cache (Exact & Partial Match)
    2. RE-FETCHES if Cache is Empty (Fixes Render Restart Bug)
    """
    global EMAIL_LIST_CACHE
    
    # RE-FETCH LOGIC
    if not EMAIL_LIST_CACHE:
        print("⚠️ Cache empty. Re-fetching...")
        fetch_latest_emails(limit=5)

    clean_input = user_text.strip().lower().rstrip(".")

    for email in EMAIL_LIST_CACHE:
        subj = email['subject'].lower()
        full_subj = email['full_subject'].lower()

        # Match "Happy.." or "Happy" or "Happy with service"
        if clean_input == subj.rstrip(".") or clean_input in full_subj:
            return email['messageId']

    # API Fallback
    token = get_access_token()
    account_id = get_account_id()
    url = f"{API_DOMAIN}/api/accounts/{account_id}/messages/search"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    params = {"searchKey": "subject", "searchValue": user_text, "limit": 1}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        data = resp.json()
        if "data" in data and data["data"]:
            return data["data"][0].get("messageId")
    except: pass
    return None

def get_full_email_content(message_id):
    token = get_access_token()
    account_id = get_account_id()
    
    url = f"{API_DOMAIN}/api/accounts/{account_id}/messages/{message_id}/content"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            inner = data.get("data", {})
            content = inner.get("content") or inner.get("body") or "No text content."
            return {"subject": inner.get("subject", "Analyzed Email"), "content": content}
        else:
            # Clear ID cache if 400 error occurs
            global ACCOUNT_ID_CACHE
            ACCOUNT_ID_CACHE = None 
            return None
    except: return None
