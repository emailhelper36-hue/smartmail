import os
import requests
import time
from datetime import datetime

# Global Cache (In-memory)
TOKEN_CACHE = {"access_token": None, "expires_at": 0}
# We keep this for speed, but we won't rely on it 100% anymore
EMAIL_LIST_CACHE = []

def get_access_token():
    """Refreshes Zoho Token if expired"""
    global TOKEN_CACHE
    
    # Return cached token if valid (with 5 min buffer)
    if TOKEN_CACHE["access_token"] and time.time() < (TOKEN_CACHE["expires_at"] - 300):
        return TOKEN_CACHE["access_token"]

    try:
        url = "https://accounts.zoho.com/oauth/v2/token"
        params = {
            "refresh_token": os.environ.get("ZOHO_REFRESH_TOKEN"),
            "client_id": os.environ.get("ZOHO_CLIENT_ID"),
            "client_secret": os.environ.get("ZOHO_CLIENT_SECRET"),
            "grant_type": "refresh_token"
        }
        # Add retry logic for token
        for _ in range(2):
            resp = requests.post(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if "access_token" in data:
                    TOKEN_CACHE["access_token"] = data["access_token"]
                    # Default to 1 hour expiry if not provided
                    expires_in = data.get("expires_in", 3600)
                    TOKEN_CACHE["expires_at"] = time.time() + expires_in
                    return data["access_token"]
            time.sleep(1)
            
        print(f"Zoho Token Failed: {resp.text}")
        return None
    except Exception as e:
        print(f"Zoho Connection Error: {e}")
        return None

def fetch_latest_emails(limit=5):
    """Fetches headers of latest emails"""
    global EMAIL_LIST_CACHE
    token = get_access_token()
    account_id = os.environ.get("ZOHO_ACCOUNT_ID")
    
    if not token or not account_id:
        return []

    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages/view"
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
                # Ensure button text isn't too long
                display_sub = (subject[:25] + '..') if len(subject) > 25 else subject
                
                clean_list.append({
                    "subject": display_sub,
                    "full_subject": subject,
                    "messageId": msg.get("messageId")
                })
            
            EMAIL_LIST_CACHE = clean_list
            return clean_list
    except Exception as e:
        print(f"Fetch Emails Error: {e}")
    
    return []

def search_email_id_by_subject(subject_text):
    """
    FALLBACK: Searches Zoho API directly if cache is empty.
    This fixes the 'Stateless' issue on Render.
    """
    token = get_access_token()
    account_id = os.environ.get("ZOHO_ACCOUNT_ID")
    
    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages/search"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    # Search for the subject in the last 10 emails
    params = {"searchKey": "subject", "searchValue": subject_text, "limit": 5}
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            messages = data.get("data", [])
            if messages:
                # Return the ID of the first match
                return messages[0].get("messageId")
    except Exception as e:
        print(f"Search Fallback Error: {e}")
    return None

def find_message_id_by_subject(user_text):
    """Matches user text to cached list OR searches live"""
    # 1. Try In-Memory Cache first (Fastest)
    global EMAIL_LIST_CACHE
    clean_text = user_text.strip().lower()
    
    if EMAIL_LIST_CACHE:
        for email in EMAIL_LIST_CACHE:
            # Check display subject (what was on the button)
            if clean_text == email['subject'].lower():
                return email['messageId']
            # Check full subject
            if clean_text == email['full_subject'].lower():
                return email['messageId']

    # 2. If not found in cache (or cache empty), try LIVE SEARCH
    # This matches the user text to a real email ID via API
    print(f"Cache miss for '{user_text}'. Searching API...")
    return search_email_id_by_subject(user_text)

def get_full_email_content(message_id):
    """Fetches body with robust fallbacks"""
    token = get_access_token()
    account_id = os.environ.get("ZOHO_ACCOUNT_ID")
    
    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages/{message_id}/content"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        
        if resp.status_code == 200:
            data = resp.json()
            inner = data.get("data", {})
            return {
                "subject": inner.get("subject", "Analyzed Email"),
                "content": inner.get("content", "") or inner.get("body", "No text content available.")
            }
        
        # 3. IF FETCH FAILS (e.g. Rate Limit 429 or 400),
        # Return a "Shell" object so the bot DOES NOT CRASH.
        # It will analyze just the subject.
        print(f"Content fetch failed ({resp.status_code}). Using fallback.")
        return {
            "subject": "Email Content Unavailable", 
            "content": f"Could not retrieve full body (Error {resp.status_code}). Analyzing based on available context."
        }

    except Exception as e:
        print(f"Get Content Exception: {e}")
        # Final Safety Net
        return {
            "subject": "Email Error",
            "content": "Server error while reading email. Please try again."
        }
