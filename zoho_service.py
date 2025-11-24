import os
import requests
import time
from datetime import datetime, timedelta

# Global Cache
TOKEN_CACHE = {"access_token": None, "expires_at": 0}
EMAIL_LIST_CACHE = []  # To map subjects to IDs

def get_access_token():
    """Refreshes Zoho Token if expired"""
    global TOKEN_CACHE
    
    if TOKEN_CACHE["access_token"] and time.time() < TOKEN_CACHE["expires_at"]:
        return TOKEN_CACHE["access_token"]

    try:
        url = "https://accounts.zoho.com/oauth/v2/token"
        params = {
            "refresh_token": os.environ.get("ZOHO_REFRESH_TOKEN"),
            "client_id": os.environ.get("ZOHO_CLIENT_ID"),
            "client_secret": os.environ.get("ZOHO_CLIENT_SECRET"),
            "grant_type": "refresh_token"
        }
        resp = requests.post(url, params=params, timeout=10)
        data = resp.json()
        
        if "access_token" in data:
            TOKEN_CACHE["access_token"] = data["access_token"]
            # Set expiry to 55 minutes from now (safety buffer)
            TOKEN_CACHE["expires_at"] = time.time() + (55 * 60)
            return data["access_token"]
        else:
            print(f"Zoho Token Error: {data}")
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
    params = {"limit": limit, "sortorder": "false"} # false = descending (newest first)

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            messages = data.get("data", [])
            
            # Clean and Cache
            clean_list = []
            for msg in messages:
                subject = msg.get("subject", "No Subject")
                # Truncate subject if too long for a button
                display_sub = (subject[:30] + '..') if len(subject) > 30 else subject
                
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

def find_message_id_by_subject(user_text):
    """Matches user text to cached email list"""
    # 1. Check exact match in cache
    for email in EMAIL_LIST_CACHE:
        if user_text.strip() == email['subject'] or user_text.strip() == email['full_subject']:
            return email['messageId']
            
    # 2. Fuzzy match (if user typed part of it)
    user_text_lower = user_text.lower()
    for email in EMAIL_LIST_CACHE:
        if user_text_lower in email['full_subject'].lower():
            return email['messageId']
            
    return None

def get_full_email_content(message_id):
    """Fetches body of a specific email"""
    token = get_access_token()
    account_id = os.environ.get("ZOHO_ACCOUNT_ID")
    
    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages/{message_id}/content"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            inner = data.get("data", {})
            return {
                "subject": inner.get("subject", ""),
                "content": inner.get("content", "") or inner.get("body", "No text content found.")
            }
    except Exception as e:
        print(f"Get Content Error: {e}")
        
    return None
