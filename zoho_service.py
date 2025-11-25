import os
import requests
import time

# --- CACHE ---
TOKEN_CACHE = {"access_token": None, "expires_at": 0}
ACCOUNT_ID_CACHE = None 
EMAIL_LIST_CACHE = [] 

# --- CONFIG ---
# We will try these domains in order if one fails
ZOHO_DOMAINS = ["https://mail.zoho.com", "https://mail.zoho.in", "https://mail.zoho.eu"]
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
    
    # 1. Try Env Var
    env_id = os.environ.get("ZOHO_ACCOUNT_ID", "").strip()
    if env_id:
        ACCOUNT_ID_CACHE = env_id
        return env_id
        
    # 2. Auto-detect via API (Try all regions)
    token = get_access_token()
    if not token: return None
    
    for domain in ZOHO_DOMAINS:
        try:
            url = f"{domain}/api/accounts"
            headers = {"Authorization": f"Zoho-oauthtoken {token}"}
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "data" in data and len(data["data"]) > 0:
                    real_id = str(data["data"][0].get("accountId"))
                    ACCOUNT_ID_CACHE = real_id
                    return real_id
        except: continue
        
    return None

def fetch_latest_emails(limit=5):
    """Fetches email list"""
    global EMAIL_LIST_CACHE
    token = get_access_token()
    account_id = get_account_id()
    
    if not token or not account_id: return []

    # Use the first domain that works, or default to .com
    base_domain = ZOHO_DOMAINS[0] 
    
    # Try finding the working domain for listing
    for domain in ZOHO_DOMAINS:
        url = f"{domain}/api/accounts/{account_id}/messages/view"
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        params = {"limit": limit, "sortorder": "false"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=8)
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
        except: continue
            
    return []

def find_message_id_by_subject(user_text):
    """Matches text to ID"""
    global EMAIL_LIST_CACHE
    
    # Refresh if empty
    if not EMAIL_LIST_CACHE:
        fetch_latest_emails(limit=5)

    clean_input = user_text.strip().lower().rstrip(".")

    for email in EMAIL_LIST_CACHE:
        subj = email['subject'].lower()
        full_subj = email['full_subject'].lower()
        if clean_input == subj.rstrip(".") or clean_input in full_subj:
            return email['messageId'], email['full_subject']

    # If cache miss, return None (force manual search not implemented to keep it simple)
    return None, None

def get_full_email_content(message_id):
    """
    REGION-SMART FETCH:
    Tries .com -> .in -> .eu automatically to get the REAL body.
    """
    token = get_access_token()
    account_id = get_account_id()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    print(f"📥 Fetching content for {message_id}...")

    # CYCLE THROUGH REGIONS
    for domain in ZOHO_DOMAINS:
        url = f"{domain}/api/accounts/{account_id}/messages/{message_id}/content"
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            
            if resp.status_code == 200:
                data = resp.json()
                inner = data.get("data", {})
                
                # We found it!
                real_subject = inner.get("subject", "No Subject")
                real_content = inner.get("content") or inner.get("body")
                
                if not real_content:
                    real_content = "Email has no text body (Empty)."
                    
                print(f"✅ Found content on {domain}")
                return {"subject": real_subject, "content": real_content}
                
            elif resp.status_code == 404:
                # 404 means "Not found on this server", so we try the next region (.in)
                continue
                
        except Exception as e:
            print(f"⚠️ Error checking {domain}: {e}")
            continue

    print("❌ Failed to find content on any region.")
    return None
