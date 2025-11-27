# Email Analysis Chatbot

This is a Flask-based web application that integrates with Zoho Mail to analyze emails via a chatbot interface. The application uses AI models through the OpenRouter API to provide summaries, determine the tone and urgency of emails, and suggest replies.

## Features

- **Zoho Mail Integration**: Fetches the latest emails from a Zoho Mail account.
- **AI-Powered Analysis**: Utilizes external AI models to analyze email content for:
  - Summary
  - Tone (e.g., Formal, Casual, Concerned)
  - Urgency Level
  - Key Points
  - Suggested Replies
- **Chatbot Ready**: Designed to be triggered by a webhook from any chatbot or conversational platform.
- **Firebase Integration**: Saves all analysis results to a Google Firestore database for logging and history.
- **Web Dashboard**: Includes a simple dashboard to view the history of analyzed emails.

## How It Works

1.  **Webhook Trigger**: A user interacts with a chatbot (e.g., by sending "hi" or an email subject). The chatbot sends a POST request to the `/webhook` endpoint.
2.  **Email Fetching**: The Flask app uses the `zoho_service.py` module to connect to the Zoho Mail API, authenticating via OAuth2, and fetching the requested email's content.
3.  **Content Cleaning**: The raw HTML content of the email is cleaned using BeautifulSoup to extract plain text.
4.  **AI Analysis**: The cleaned text is sent to the `analyze_text` function in `analyze.py`. This function sends a request to an AI model via the OpenRouter API. The system is designed with a fallback mechanism to try different models if the primary one fails.
5.  **Store Results**: The analysis results (summary, tone, etc.) are saved to a Firestore collection.
6.  **Respond to User**: The Flask app formats the analysis into a user-friendly message and sends it back to the chatbot, which then displays it to the user.

## Project Structure

```
.
├── Procfile                # Defines the command to run the app on deployment platforms
├── app.py                  # Main Flask application, handles webhooks and dashboard routes
├── analyze.py              # Contains the logic for text analysis using the OpenRouter API
├── zoho_service.py         # Manages all interactions with the Zoho Mail API
├── requirements.txt        # Lists all Python dependencies
└── templates/
    └── dashboard.html      # HTML template for the analysis history dashboard
```

## Setup and Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd <repository-name>
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

3.  **Install the dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Create a `.env` file:**
    Create a file named `.env` in the root of the project and add the following environment variables with your credentials:

    ```
    # --- Zoho Mail API ---
    ZOHO_CLIENT_ID=...
    ZOHO_CLIENT_SECRET=...
    ZOHO_REFRESH_TOKEN=...
    ZOHO_ACCOUNT_ID=... # Optional, can be auto-detected

    # --- Firebase Admin SDK ---
    # (Your Firebase service account JSON key, formatted for .env)
    FIREBASE_TYPE=service_account
    FIREBASE_PROJECT_ID=...
    FIREBASE_PRIVATE_KEY_ID=...
    FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
    FIREBASE_CLIENT_EMAIL=...
    FIREBASE_CLIENT_ID=...
    FIREBASE_AUTH_URI=...
    FIREBASE_TOKEN_URI=...
    FIREBASE_AUTH_PROVIDER_CERT_URL=...
    FIREBASE_CLIENT_CERT_URL=...

    # --- OpenRouter API ---
    OPENROUTER_API_KEY=...
    OPENROUTER_REFERER_URL=https://your-app-url.com # Optional but recommended
    ```

5.  **Run the application locally:**
    ```bash
    python app.py
    ```
    The app will be running at `http://127.0.0.1:5000`.

## Deployment

This project is configured for deployment on platforms like Heroku or Render. The `Procfile` specifies the command to run the application using the `gunicorn` WSGI server.

```
web: gunicorn app:app
```

Simply connect your Git repository to the deployment platform and follow their instructions to deploy the application.

## Key Dependencies

- **Flask**: Web framework
- **Requests**: For making HTTP requests to external APIs
- **firebase-admin**: To interact with Firestore
- **beautifulsoup4**: For HTML parsing and cleaning
- **gunicorn**: Production WSGI server
- **python-dotenv**: For managing environment variables
