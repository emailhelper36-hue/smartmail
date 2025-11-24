import os
import requests
import time

# --- CACHE ---
TOKEN_CACHE = {"access_token": None, "expires_at": 0}
ACCOUNT_ID_CACHE = None 

# --- CONFIG ---
# Get the domain from your env or default to .com
API_DOMAIN = os.environ.get("ZOHO_API_DOMAIN", "https://mail.zoho.com")

def get_access_token():
    """Refreshes the Zoho Token"""
    global TOKEN_CACHE
    
    # Return cached token if it's still valid (with 5 min buffer)
    if TOKEN_CACHE["access_token"] and time.time() < (TOKEN_CACHE["expires_at"] - 300):
        return TOKEN_CACHE["access_token"]

    try:
        # Zoho Token URL is usually accounts.zoho.com (or .in/.eu based on region)
        # We try standard .com first, or use the one from your Env if you set ZOHO_ACCOUNTS_URL
        acc_url = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com")
        url = f"{acc_url}/oauth/v2/token"
        
        params = {
            "refresh_token": os.environ.get("ZOHO_REFRESH_TOKEN"),
            "client_id": os.environ.get("ZOHO_CLIENT_ID"),
            "client_secret": os.environ.get("ZOHO_CLIENT_SECRET"),
            "grant_type": "refresh_token"
        }
        
        resp = requests.post(url, params=params, timeout=15)
        data = resp.json()
        
        if "access_token" in data:
            TOKEN_CACHE["access_token"] = data["access_token"]
            TOKEN_CACHE["expires_at"] = time.time() + data.get("expires_in", 3600)
            return data["access_token"]
        else:
            print(f"❌ Token Refresh Failed: {data}")
            
    except Exception as e:
        print(f"❌ Connection Error: {e}")
    return None

def get_account_id():
    """
    AUTO-DISCOVERY: Ignores the .env ID if it doesn't work 
    and asks Zoho 'Who am I?' to get the real ID.
    """
    global ACCOUNT_ID_CACHE
    if ACCOUNT_ID_CACHE: return ACCOUNT_ID_CACHE
        
    token = get_access_token()
    if not token: return None
    
    # 1. Ask Zoho for the correct Account ID associated with this Token
    try:
        url = f"{API_DOMAIN}/api/accounts"
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                # This is the correct ID for API calls
                real_id = str(data["data"][0].get("accountId"))
                print(f"✅ Authenticated as Account ID: {real_id}")
                ACCOUNT_ID_CACHE = real_id
                return real_id
    except Exception as e:
        print(f"❌ Account ID Fetch Error: {e}")

    # 2. Fallback to .env only if API failed
    return os.environ.get("ZOHO_ACCOUNT_ID")

def fetch_latest_emails(limit=5):
    token = get_access_token()
    account_id = get_account_id()
    
    if not token or not account_id:
        return []

    url = f"{API_DOMAIN}/api/accounts/{account_id}/messages/view"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    params = {"limit": limit, "sortorder": "false"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            return [{
                "subject": (msg.get("subject", "No Subject")[:25] + ".."),
                "full_subject": msg.get("subject", "No Subject"),
                "messageId": msg.get("messageId")
            } for msg in resp.json().get("data", [])]
    except Exception as e:
        print(f"❌ List Error: {e}")
    return []

def find_message_id_by_subject(user_text):
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
    except:
        pass
    return None

def get_full_email_content(message_id):
    token = get_access_token()
    account_id = get_account_id()
    
    # CRITICAL: Using the API_DOMAIN from your env
    url = f"{API_DOMAIN}/api/accounts/{account_id}/messages/{message_id}/content"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        
        if resp.status_code == 200:
            data = resp.json()
            inner = data.get("data", {})
            content = inner.get("content") or inner.get("body") or "No text content."
            return {
                "subject": inner.get("subject", "Analyzed Email"),
                "content": content
            }
        else:
            print(f"❌ Content Fetch Failed {resp.status_code}: {resp.text}")
            return None # Allow app.py to handle the error
            
    except Exception as e:
        print(f"❌ Content Exception: {e}")
        return None
