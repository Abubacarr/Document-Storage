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

In Streamlit Cloud, set the app secrets:

```toml
[google]
client_id = "your-google-client-id"
client_secret = "your-google-client-secret"
sheet_id = "your-google-sheet-id"
```

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
