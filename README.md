# Document Storage

A Streamlit document management system with user accounts, admin controls, and Supabase file storage. Admins can upload, rename, categorize, and delete documents, while viewers can search, open, and download shared files.

## Features 

- User login and self-service account creation
- Admin and viewer roles
- Supabase file storage with public download links
- SQLite database for users, categories, and document records
- Category management
- Document search
- Rename documents (admin only)
- Viewer download buttons
- Admin-only document and category deletion

## Requirements

- Python 3.10+
- Supabase account (free tier works)
- Supabase project with a public storage bucket named `documents`

## Local Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Create `.streamlit/secrets.toml`:

```toml
[supabase]
url = "https://your-project.supabase.co"
key = "your-anon-public-key"
```

Run the app:

```bash
streamlit run app.py
```

## Supabase Setup

1. Go to [supabase.com](https://supabase.com) and create a free account
2. Create a new project
3. Go to **Storage** → **New bucket**
4. Name it `documents` and enable **Public bucket**
5. Go to **Settings** → **API**
6. Copy the **Project URL** and **anon/public key**
7. Paste them into your secrets as shown above

## Streamlit Cloud Deployment

Add the following to your app secrets in Streamlit Cloud (**Manage app → Settings → Secrets**):

```toml
[supabase]
url = "https://your-project.supabase.co"
key = "your-anon-public-key"
```

Deploy from:
- Repository: `Abubacarr/Document-Storage`
- Branch: `main`
- Main file: `app.py`

## Default Admin Account

```
Email:    admin@gmail.com
Password: Admin@2026
```

