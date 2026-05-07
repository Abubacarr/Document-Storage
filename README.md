# Document Storage

A Streamlit document management system with user accounts, admin controls, Google Drive file storage, and Google Sheets data storage. Admins can upload, categorize, delete, and manage documents, while viewers can search, open, and download shared files.

## Features

- User login and self-service account creation
- Admin and viewer roles
- Google Drive document uploads
- Google Sheets storage for users, categories, and document records
- Category management
- Document search and category filtering
- Viewer download buttons
- Admin-only document and category deletion

## Requirements

- Python 3.10+
- Google Cloud project with these APIs enabled:
  - Google Drive API
  - Google Sheets API
- OAuth client ID and client secret
- A Google Sheet ID for shared app data

## Local Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Create `.streamlit/secrets.toml`:

```toml
[google]
client_id = "your-google-client-id"
client_secret = "your-google-client-secret"
sheet_id = "your-google-sheet-id"
```

Run the app:

```bash
streamlit run app.py
```

On first run, Google may ask you to authorize access.

## Streamlit Cloud Deployment

For Streamlit Cloud, use a Google service account because desktop OAuth cannot open a local browser on the cloud server.

Create a Google Cloud service account, download its JSON key, and paste the full JSON into Streamlit secrets as a multi-line string:

```toml
GOOGLE_SERVICE_ACCOUNT_JSON = '''
{
  "type": "service_account",
  "project_id": "...",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "...",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "...",
  "universe_domain": "googleapis.com"
}
'''

[google]
sheet_id = "your-google-sheet-id"
```

You can also paste the key fields individually:

```toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "your-private-key-id"
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "your-service-account@your-project.iam.gserviceaccount.com"
client_id = "your-service-account-client-id"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "your-cert-url"
universe_domain = "googleapis.com"

[google]
sheet_id = "your-google-sheet-id"
```

Share your Google Sheet with the service account `client_email` as **Editor**.

Deploy from:

- Repository: `Abubacarr/Document-Storage`
- Branch: `main`
- Main file: `app.py`

## Private Files

Do not commit these files:

- `.streamlit/secrets.toml`
- `credentials.json`
- `token.json`
- `google_sheet_id.txt`
- `documents.db`
- `uploads/`
