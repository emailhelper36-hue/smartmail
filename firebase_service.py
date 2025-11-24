import os
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Initialize Firebase safely
def get_db():
    if not firebase_admin._apps:
        try:
            # Construct creds from env vars
            cred_dict = {
                "type": os.environ.get("FIREBASE_TYPE"),
                "project_id": os.environ.get("FIREBASE_PROJECT_ID"),
                "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID"),
                "private_key": os.environ.get("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
                "client_email": os.environ.get("FIREBASE_CLIENT_EMAIL"),
                "client_id": os.environ.get("FIREBASE_CLIENT_ID"),
                "auth_uri": os.environ.get("FIREBASE_AUTH_URI"),
                "token_uri": os.environ.get("FIREBASE_TOKEN_URI"),
                "auth_provider_x509_cert_url": os.environ.get("FIREBASE_AUTH_PROVIDER_CERT_URL"),
                "client_x509_cert_url": os.environ.get("FIREBASE_CLIENT_CERT_URL"),
            }
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"Firebase Init Error: {e}")
            return None
    
    return firestore.client()

def get_timestamp():
    return datetime.now().isoformat()

def save_analysis(data):
    db = get_db()
    if db:
        try:
            # Save using messageId as document ID for uniqueness
            db.collection("email_analysis").document(str(data['messageId'])).set(data)
            print("Saved to Firebase")
        except Exception as e:
            print(f"Save Error: {e}")

def get_all_analyses():
    db = get_db()
    results = []
    if db:
        try:
            docs = db.collection("email_analysis").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(50).stream()
            for doc in docs:
                results.append(doc.to_dict())
        except Exception as e:
            print(f"Fetch Error: {e}")
    return results
