# =========================================================
# DOCUMENT STORAGE SYSTEM (FIXED VERSION)
# Fixes:
# - JSONDecodeError
# - Missing credentials.json
# - Corrupted token.json
# - Streamlit rerun issues
# - Safer Google auth
# - Better file cleanup
# =========================================================

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import streamlit as st
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


# =========================================================
# CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

SQLITE_DB_PATH = BASE_DIR / "documents.db"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
SHEET_ID_FILE = BASE_DIR / "google_sheet_id.txt"
UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

APP_FOLDER_NAME = "DocumentSystem"
DATA_SPREADSHEET_NAME = "DocumentSystemData"

DEFAULT_ADMIN_EMAIL = "admin@gmail.com"
DEFAULT_ADMIN_PASSWORD = "Admin@2026"

SHEETS = {
    "Users": ["id", "username", "email", "password", "role"],
    "Documents": ["id", "title", "category", "file_link", "drive_file_id", "created_at"],
    "Categories": ["id", "name"],
}

DEFAULT_CATEGORIES = (
    "Policies",
    "Reports",
    "Letters",
    "Invoices",
)


# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="Document Storage",
    page_icon="📂",
    layout="wide",
)


# =========================================================
# HELPERS
# =========================================================

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def clean_text(value):
    return (value or "").strip()


def is_admin():
    return st.session_state.get("role") == "admin"


def require_admin():
    if not is_admin():
        st.error("Admin only")
        st.stop()


def get_download_link(file_id):
    if not file_id:
        return None
    return f"https://drive.google.com/uc?export=download&id={file_id}"


# =========================================================
# GOOGLE AUTH
# =========================================================

def get_service_account_info():
    try:
        info = dict(st.secrets["gcp_service_account"])
        # Fix newlines in private key that get escaped in secrets.toml
        if "private_key" in info:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        return info
    except Exception:
        return None


def get_google_client_config():

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        return None

    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def safe_delete_token():
    try:
        TOKEN_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def save_token(creds):

    try:
        token_json = creds.to_json()

        # Validate before saving
        json.loads(token_json)

        time.sleep(1)

        TOKEN_FILE.write_text(
            token_json,
            encoding="utf-8"
        )

    except Exception as e:
        st.error(f"Failed saving token: {e}")


def load_token():

    if not TOKEN_FILE.exists():
        return None

    try:
        return Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES
        )

    except (json.JSONDecodeError, ValueError):

        # CORRUPTED TOKEN FIX
        safe_delete_token()

        st.warning("Corrupted token removed. Please login again.")

        return None

    except Exception:
        safe_delete_token()
        return None


def get_credentials():

    # =====================================================
    # SERVICE ACCOUNT (BEST FOR STREAMLIT CLOUD)
    # =====================================================

    service_account_info = get_service_account_info()

    if service_account_info:

        return service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=SCOPES
        )

    # =====================================================
    # USER TOKEN
    # =====================================================

    creds = load_token()

    # =====================================================
    # REFRESH TOKEN
    # =====================================================

    if creds and creds.expired and creds.refresh_token:

        try:
            creds.refresh(Request())
            save_token(creds)

        except RefreshError:

            safe_delete_token()

            st.warning("Session expired. Please login again.")

            creds = None

    # =====================================================
    # VALID CREDS
    # =====================================================

    if creds and creds.valid:
        return creds

    # =====================================================
    # GOOGLE OAUTH
    # =====================================================

    client_config = get_google_client_config()

    if CREDENTIALS_FILE.exists():

        try:

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE),
                SCOPES
            )

        except json.JSONDecodeError:

            st.error("credentials.json is corrupted.")
            st.stop()

    elif client_config:

        flow = InstalledAppFlow.from_client_config(
            client_config,
            SCOPES
        )

    else:

        st.error("Google OAuth is not configured.")

        st.info(
            "Add credentials.json beside app.py "
            "OR configure Streamlit secrets."
        )

        st.code(str(CREDENTIALS_FILE))

        st.stop()

    # =====================================================
    # LOGIN
    # =====================================================

    try:

        creds = flow.run_local_server(port=0)

    except webbrowser.Error:

        st.error(
            "Desktop OAuth cannot run on Streamlit Cloud.\n"
            "Use a Google service account instead."
        )

        st.stop()

    except Exception as e:

        st.error(f"Google login failed: {e}")

        st.stop()

    save_token(creds)

    return creds


# =========================================================
# GOOGLE SERVICES
# =========================================================

@st.cache_resource
def get_drive_service():

    creds = get_credentials()

    return build(
        "drive",
        "v3",
        credentials=creds
    )


@st.cache_resource
def get_sheets_service():

    creds = get_credentials()

    return build(
        "sheets",
        "v4",
        credentials=creds
    )


# =========================================================
# GOOGLE DRIVE
# =========================================================

def get_or_create_folder(service, name, parent_id=None):

    query = (
        f"name='{name}' and "
        "mimeType='application/vnd.google-apps.folder' and "
        "trashed=false"
    )

    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id,name)"
        )
        .execute()
    )

    folders = results.get("files", [])

    if folders:
        return folders[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }

    if parent_id:
        metadata["parents"] = [parent_id]

    folder = (
        service.files()
        .create(body=metadata, fields="id")
        .execute()
    )

    return folder["id"]


def upload_to_drive(file_path, filename, category):

    service = get_drive_service()

    root_id = get_or_create_folder(
        service,
        APP_FOLDER_NAME
    )

    category_id = get_or_create_folder(
        service,
        category,
        root_id
    )

    metadata = {
        "name": filename,
        "parents": [category_id]
    }

    media = MediaFileUpload(
        str(file_path),
        resumable=True
    )

    uploaded = (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id, webViewLink"
        )
        .execute()
    )

    service.permissions().create(
        fileId=uploaded["id"],
        body={
            "type": "anyone",
            "role": "reader"
        }
    ).execute()

    return uploaded["webViewLink"], uploaded["id"]


def delete_drive_file(file_id):

    if not file_id:
        return

    get_drive_service().files().delete(
        fileId=file_id
    ).execute()


# =========================================================
# SIMPLE SQLITE
# =========================================================

conn = sqlite3.connect(SQLITE_DB_PATH)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    email TEXT UNIQUE,
    password TEXT,
    role TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS categories(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS documents(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    category TEXT,
    file_link TEXT,
    drive_file_id TEXT,
    created_at TEXT
)
""")

conn.commit()


# =========================================================
# SEED DEFAULTS
# =========================================================

cur.execute(
    "SELECT * FROM users WHERE email=?",
    (DEFAULT_ADMIN_EMAIL,)
)

if not cur.fetchone():

    cur.execute(
        """
        INSERT INTO users(username,email,password,role)
        VALUES(?,?,?,?)
        """,
        (
            "admin",
            DEFAULT_ADMIN_EMAIL,
            hash_password(DEFAULT_ADMIN_PASSWORD),
            "admin"
        )
    )

    conn.commit()

for category in DEFAULT_CATEGORIES:

    try:

        cur.execute(
            "INSERT INTO categories(name) VALUES(?)",
            (category,)
        )

    except:
        pass

conn.commit()


# =========================================================
# SESSION
# =========================================================

if "user" not in st.session_state:
    st.session_state.user = None
    st.session_state.role = None


# =========================================================
# LOGIN
# =========================================================

def login_page():

    st.title("🔐 Login")

    tab1, tab2 = st.tabs([
        "Login",
        "Create Account"
    ])

    # =====================================================
    # LOGIN
    # =====================================================

    with tab1:

        with st.form("login_form"):

            email = st.text_input("Email")
            password = st.text_input(
                "Password",
                type="password"
            )

            submit = st.form_submit_button(
                "Login",
                use_container_width=True
            )

        if submit:

            cur.execute(
                """
                SELECT username, role
                FROM users
                WHERE email=? AND password=?
                """,
                (
                    email,
                    hash_password(password)
                )
            )

            user = cur.fetchone()

            if user:

                st.session_state.user = user[0]
                st.session_state.role = user[1]

                st.success("Logged in")

                st.rerun()

            else:
                st.error("Invalid login")

    # =====================================================
    # REGISTER
    # =====================================================

    with tab2:

        with st.form("register_form"):

            username = st.text_input("Name")
            email = st.text_input("Email")
            password = st.text_input(
                "Password",
                type="password"
            )

            submit = st.form_submit_button(
                "Create Account",
                use_container_width=True
            )

        if submit:

            try:

                cur.execute(
                    """
                    INSERT INTO users(
                        username,
                        email,
                        password,
                        role
                    )
                    VALUES(?,?,?,?)
                    """,
                    (
                        username,
                        email,
                        hash_password(password),
                        "viewer"
                    )
                )

                conn.commit()

                st.success("Account created")

            except sqlite3.IntegrityError:
                st.error("Email already exists")


if not st.session_state.user:
    login_page()
    st.stop()


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.title("📂 Document Storage")

    st.caption(
        f"{st.session_state.user} "
        f"({st.session_state.role})"
    )

    menu_items = [
        "Dashboard",
        "Upload",
        "View Documents",
    ]

    if is_admin():
        menu_items += [
            "Categories",
            "Users"
        ]

    menu = st.selectbox(
        "Menu",
        menu_items
    )

    if st.button(
        "Logout",
        use_container_width=True
    ):

        st.session_state.user = None
        st.session_state.role = None

        st.rerun()


# =========================================================
# DASHBOARD
# =========================================================

if menu == "Dashboard":

    st.title("📊 Dashboard")

    cur.execute("SELECT COUNT(*) FROM documents")
    total_docs = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM categories")
    total_categories = cur.fetchone()[0]

    col1, col2, col3 = st.columns(3)

    col1.metric("Documents", total_docs)
    col2.metric("Users", total_users)
    col3.metric("Categories", total_categories)


# =========================================================
# UPLOAD
# =========================================================

elif menu == "Upload":

    require_admin()

    st.title("📤 Upload")

    cur.execute("SELECT name FROM categories")

    categories = [
        row[0]
        for row in cur.fetchall()
    ]

    with st.form("upload_form"):

        title = st.text_input("Title")

        category = st.selectbox(
            "Category",
            categories
        )

        uploaded_file = st.file_uploader(
            "Choose File"
        )

        submit = st.form_submit_button(
            "Upload",
            use_container_width=True
        )

    if submit:

        if not uploaded_file:

            st.error("Choose a file")

        else:

            temp_path = None

            try:

                suffix = Path(
                    uploaded_file.name
                ).suffix

                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=suffix,
                    dir=UPLOAD_DIR
                ) as temp_file:

                    temp_file.write(
                        uploaded_file.getbuffer()
                    )

                    temp_path = Path(
                        temp_file.name
                    )

                with st.spinner(
                    "Uploading..."
                ):

                    link, drive_id = upload_to_drive(
                        temp_path,
                        uploaded_file.name,
                        category
                    )

                cur.execute(
                    """
                    INSERT INTO documents(
                        title,
                        category,
                        file_link,
                        drive_file_id,
                        created_at
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        title,
                        category,
                        link,
                        drive_id,
                        datetime.now().strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    )
                )

                conn.commit()

                st.success("Uploaded")

                st.markdown(
                    f"[Open File]({link})"
                )

            except HttpError as e:

                st.error(
                    f"Google API Error:\n{e}"
                )

            except Exception as e:

                st.error(
                    f"Upload failed:\n{e}"
                )

            finally:

                if temp_path and temp_path.exists():

                    temp_path.unlink(
                        missing_ok=True
                    )


# =========================================================
# VIEW DOCUMENTS
# =========================================================

elif menu == "View Documents":

    st.title("📁 Documents")

    search = st.text_input("Search")

    cur.execute("""
    SELECT
        id,
        title,
        category,
        file_link,
        drive_file_id,
        created_at
    FROM documents
    ORDER BY id DESC
    """)

    documents = cur.fetchall()

    for doc in documents:

        if search.lower() not in str(doc[1]).lower():
            continue

        with st.container(border=True):

            st.subheader(doc[1])

            st.write(
                f"Category: {doc[2]}"
            )

            st.caption(doc[5])

            st.markdown(
                f"[📥 Open File]({doc[3]})"
            )

            download_link = get_download_link(
                doc[4]
            )

            if download_link:

                st.link_button(
                    "Download",
                    download_link
                )

            if is_admin():

                if st.button(
                    "Delete",
                    key=f"delete_{doc[0]}"
                ):

                    try:

                        delete_drive_file(doc[4])

                        cur.execute(
                            "DELETE FROM documents WHERE id=?",
                            (doc[0],)
                        )

                        conn.commit()

                        st.success("Deleted")

                        st.rerun()

                    except Exception as e:

                        st.error(
                            f"Delete failed:\n{e}"
                        )


# =========================================================
# CATEGORIES
# =========================================================

elif menu == "Categories":

    require_admin()

    st.title("📂 Categories")

    with st.form("category_form"):

        new_category = st.text_input(
            "New Category"
        )

        submit = st.form_submit_button(
            "Add"
        )

    if submit:

        try:

            cur.execute(
                "INSERT INTO categories(name) VALUES(?)",
                (new_category,)
            )

            conn.commit()

            st.success("Added")

            st.rerun()

        except sqlite3.IntegrityError:

            st.error(
                "Category already exists"
            )

    cur.execute(
        "SELECT id,name FROM categories"
    )

    categories = cur.fetchall()

    for cat in categories:

        st.write(cat[1])


# =========================================================
# USERS
# =========================================================

elif menu == "Users":

    require_admin()

    st.title("👥 Users")

    cur.execute("""
    SELECT
        username,
        email,
        role
    FROM users
    ORDER BY username
    """)

    users = cur.fetchall()

    for user in users:

        st.write(
            f"**{user[0]}** | "
            f"{user[1]} | "
            f"{user[2]}"
        )
