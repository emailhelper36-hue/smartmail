import os
import requests
import time

# --- CACHE ---
TOKEN_CACHE = {"access_token": None, "expires_at": 0}
ACCOUNT_ID_CACHE = None 
EMAIL_LIST_CACHE = [] 

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
    global ACCOUNT_ID_CACHE
    if ACCOUNT_ID_CACHE: return ACCOUNT_ID_CACHE
    
    env_id = os.environ.get("ZOHO_ACCOUNT_ID", "").strip()
    if env_id:
        ACCOUNT_ID_CACHE = env_id
        return env_id
        
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
                    "full_subject": subject, # We need this!
                    "messageId": msg.get("messageId")
                })
            EMAIL_LIST_CACHE = clean_list
            return clean_list
    except Exception as e:
        print(f"❌ List Error: {e}")
    return []

def find_message_data_by_subject(user_text):
    """
    Returns (messageId, full_subject)
    """
    global EMAIL_LIST_CACHE
    
    # Re-fetch if empty
    if not EMAIL_LIST_CACHE:
        fetch_latest_emails(limit=5)

    clean_input = user_text.strip().lower().rstrip(".")

    for email in EMAIL_LIST_CACHE:
        subj = email['subject'].lower()
        full_subj = email['full_subject'].lower()

        if clean_input == subj.rstrip(".") or clean_input in full_subj:
            return email['messageId'], email['full_subject']

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
            item = data["data"][0]
            return item.get("messageId"), item.get("subject")
    except: pass
    
    return None, None

def get_full_email_content(message_id):
    token = get_access_token()
    account_id = get_account_id()
    
    url = f"{API_DOMAIN}/api/accounts/{account_id}/messages/{message_id}/content"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    try:
        print(f"📥 Fetching content for ID: {message_id}")
        resp = requests.get(url, headers=headers, timeout=12)
        
        if resp.status_code == 200:
            data = resp.json()
            inner = data.get("data", {})
            
            # TRY ALL POSSIBLE CONTENT FIELDS
            content = inner.get("content") or inner.get("body") or inner.get("summary") or ""
            
            # If still empty, Zoho might have returned a 200 OK but with no content field
            if not content:
                print(f"⚠️ Empty body received. Full response: {data}")
                content = "No text content found in email body."
                
            return {"subject": inner.get("subject", ""), "content": content}
        else:
            print(f"❌ Content API Error {resp.status_code}: {resp.text}")
            global ACCOUNT_ID_CACHE
            ACCOUNT_ID_CACHE = None 
            return None
    except Exception as e:
        print(f"❌ Content Fetch Exception: {e}")
        return None
