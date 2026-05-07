import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


# ======================
# CONFIG
# ======================
BASE_DIR = Path(__file__).resolve().parent
SQLITE_DB_PATH = BASE_DIR / "documents.db"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
SHEET_ID_FILE = BASE_DIR / "google_sheet_id.txt"
UPLOAD_DIR = BASE_DIR / "uploads"

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]
APP_FOLDER_NAME = "DocumentSystem"
DATA_SPREADSHEET_NAME = "DocumentSystemData"
DEFAULT_ADMIN_EMAIL = "abubacarrjatta3@gmail.com"
DEFAULT_ADMIN_PASSWORD = "Abubacarr@2026"

SHEETS = {
    "Users": ["id", "username", "email", "password", "role"],
    "Documents": ["id", "title", "category", "file_link", "drive_file_id", "created_at"],
    "Categories": ["id", "name"],
}

DEFAULT_CATEGORIES = ("Policies", "Reports", "Letters", "Invoices")

UPLOAD_DIR.mkdir(exist_ok=True)


# ======================
# PAGE SETUP
# ======================
st.set_page_config(
    page_title="Document System",
    page_icon="📂",
    layout="wide",
)


# ======================
# HELPERS
# ======================
def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def clean_text(value):
    return (value or "").strip()


def is_admin():
    return st.session_state.get("role") == "admin"


def require_admin():
    if not is_admin():
        st.error("Admin only")
        st.stop()


def escape_drive_query_value(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


def get_download_link(file_id):
    if not file_id:
        return None
    return f"https://drive.google.com/uc?export=download&id={file_id}"


# ======================
# GOOGLE AUTH
# ======================
def get_secret_value(section, key):
    try:
        values = st.secrets.get(section, {})
        return values.get(key)
    except Exception:
        return None


def get_any_secret(*keys):
    for key in keys:
        try:
            value = st.secrets.get(key)
            if value:
                return value
        except Exception:
            pass
    return None


def get_google_client_config():
    client_id = (
        os.environ.get("GOOGLE_CLIENT_ID")
        or get_secret_value("google", "client_id")
        or get_secret_value("google", "client-id")
        or get_any_secret("GOOGLE_CLIENT_ID", "google_client_id")
    )
    client_secret = (
        os.environ.get("GOOGLE_CLIENT_SECRET")
        or get_secret_value("google", "client_secret")
        or get_secret_value("google", "client-secret")
        or get_any_secret("GOOGLE_CLIENT_SECRET", "google_client_secret")
    )

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


def has_required_scopes(creds):
    if hasattr(creds, "has_scopes"):
        return creds.has_scopes(SCOPES)

    granted_scopes = set(creds.scopes or [])
    return set(SCOPES).issubset(granted_scopes)


def get_credentials():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.expired and creds.refresh_token and has_required_scopes(creds):
        try:
            creds.refresh(Request())
        except RefreshError:
            TOKEN_FILE.unlink(missing_ok=True)
            creds = None

    if creds and creds.valid and has_required_scopes(creds):
        return creds

    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()

    client_config = get_google_client_config()

    if CREDENTIALS_FILE.exists():
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    elif client_config:
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    else:
        st.error("Google OAuth is not configured.")
        st.write("Add your Google OAuth client info in one of these places:")
        st.write("1. Put credentials.json beside app.py:")
        st.code(str(CREDENTIALS_FILE), language="text")
        st.write("2. Or set Streamlit secrets:")
        st.code(
            """
[google]
client_id = "your-client-id"
client_secret = "your-client-secret"
sheet_id = "your-google-sheet-id"
            """.strip(),
            language="toml",
        )
        st.write("Alternative top-level secrets are also supported:")
        st.code(
            """
GOOGLE_CLIENT_ID = "your-client-id"
GOOGLE_CLIENT_SECRET = "your-client-secret"
GOOGLE_SHEET_ID = "your-google-sheet-id"
            """.strip(),
            language="toml",
        )
        st.stop()

    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_google_service(api_name, api_version):
    return build(api_name, api_version, credentials=get_credentials())


def get_drive_service():
    return get_google_service("drive", "v3")


def get_sheets_service():
    return get_google_service("sheets", "v4")


# ======================
# GOOGLE DRIVE
# ======================
def get_or_create_folder(service, name, parent_id=None):
    safe_name = escape_drive_query_value(name)
    query = (
        f"name = '{safe_name}' and "
        "mimeType = 'application/vnd.google-apps.folder' and "
        "trashed = false"
    )

    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute()
    )
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def upload_to_drive(file_path, filename, category):
    service = get_drive_service()

    root_id = get_or_create_folder(service, APP_FOLDER_NAME)
    category_id = get_or_create_folder(service, category, root_id)

    metadata = {"name": filename, "parents": [category_id]}
    media = MediaFileUpload(str(file_path), resumable=True)

    uploaded = (
        service.files()
        .create(body=metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )

    service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return uploaded["webViewLink"], uploaded["id"]


def delete_drive_file(file_id):
    if not file_id:
        return False

    get_drive_service().files().delete(fileId=file_id).execute()
    return True


# ======================
# GOOGLE SHEETS STORAGE
# ======================
def get_configured_spreadsheet_id():
    return (
        os.environ.get("GOOGLE_SHEET_ID")
        or get_secret_value("google", "sheet_id")
        or get_secret_value("google", "sheet-id")
        or get_any_secret("GOOGLE_SHEET_ID", "google_sheet_id")
        or (SHEET_ID_FILE.read_text(encoding="utf-8").strip() if SHEET_ID_FILE.exists() else None)
    )


def save_spreadsheet_id(spreadsheet_id):
    try:
        SHEET_ID_FILE.write_text(spreadsheet_id, encoding="utf-8")
    except OSError:
        pass


@st.cache_resource
def get_spreadsheet_id():
    configured_id = get_configured_spreadsheet_id()
    if configured_id:
        return configured_id

    spreadsheet = {
        "properties": {"title": DATA_SPREADSHEET_NAME},
        "sheets": [{"properties": {"title": name}} for name in SHEETS],
    }
    created = (
        get_sheets_service()
        .spreadsheets()
        .create(body=spreadsheet, fields="spreadsheetId")
        .execute()
    )
    spreadsheet_id = created["spreadsheetId"]
    save_spreadsheet_id(spreadsheet_id)
    return spreadsheet_id


def get_sheet_metadata():
    return (
        get_sheets_service()
        .spreadsheets()
        .get(spreadsheetId=get_spreadsheet_id(), fields="sheets(properties(title))")
        .execute()
    )


def ensure_sheet_schema():
    service = get_sheets_service()
    spreadsheet_id = get_spreadsheet_id()
    metadata = get_sheet_metadata()
    existing_titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}

    requests = []
    for sheet_name in SHEETS:
        if sheet_name not in existing_titles:
            requests.append({"addSheet": {"properties": {"title": sheet_name}}})

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()

    for sheet_name, headers in SHEETS.items():
        current = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!1:1")
            .execute()
            .get("values", [])
        )
        if not current or current[0] != headers:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": [headers]},
            ).execute()


def sheet_values(sheet_name):
    values = (
        get_sheets_service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=get_spreadsheet_id(), range=f"{sheet_name}!A:Z")
        .execute()
        .get("values", [])
    )
    return values


def sheet_records(sheet_name):
    headers = SHEETS[sheet_name]
    values = sheet_values(sheet_name)
    records = []

    for index, row in enumerate(values[1:], start=2):
        padded = row + [""] * (len(headers) - len(row))
        record = dict(zip(headers, padded))
        record["_row"] = index
        records.append(record)

    return records


def append_record(sheet_name, record):
    headers = SHEETS[sheet_name]
    values = [[record.get(header, "") for header in headers]]
    get_sheets_service().spreadsheets().values().append(
        spreadsheetId=get_spreadsheet_id(),
        range=f"{sheet_name}!A:Z",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()


def delete_record(sheet_name, row_number):
    sheet_id = get_sheet_numeric_id(sheet_name)
    get_sheets_service().spreadsheets().batchUpdate(
        spreadsheetId=get_spreadsheet_id(),
        body={
            "requests": [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row_number - 1,
                            "endIndex": row_number,
                        }
                    }
                }
            ]
        },
    ).execute()


def get_sheet_numeric_id(sheet_name):
    metadata = (
        get_sheets_service()
        .spreadsheets()
        .get(spreadsheetId=get_spreadsheet_id(), fields="sheets(properties(sheetId,title))")
        .execute()
    )
    for sheet in metadata.get("sheets", []):
        properties = sheet["properties"]
        if properties["title"] == sheet_name:
            return properties["sheetId"]
    raise ValueError(f"Missing sheet: {sheet_name}")


def next_id(sheet_name):
    ids = []
    for record in sheet_records(sheet_name):
        try:
            ids.append(int(record["id"]))
        except (TypeError, ValueError):
            pass
    return str(max(ids, default=0) + 1)


def find_user_by_login(email, password):
    password_hash = hash_password(password)
    for user in sheet_records("Users"):
        if user["email"].lower() == email.lower() and user["password"] == password_hash:
            return user
    return None


def user_email_exists(email):
    return any(user["email"].lower() == email.lower() for user in sheet_records("Users"))


def create_user(username, email, password, role="viewer"):
    if user_email_exists(email):
        raise ValueError("A user with that email already exists.")

    append_record(
        "Users",
        {
            "id": next_id("Users"),
            "username": username,
            "email": email,
            "password": hash_password(password),
            "role": role,
        },
    )


def get_categories():
    return sorted(record["name"] for record in sheet_records("Categories") if record["name"])


def add_category(name):
    if name.lower() in {category.lower() for category in get_categories()}:
        raise ValueError("That category already exists.")

    append_record("Categories", {"id": next_id("Categories"), "name": name})


def delete_category(name):
    for record in sheet_records("Categories"):
        if record["name"] == name:
            delete_record("Categories", record["_row"])
            return


def count_documents(category=None):
    documents = sheet_records("Documents")
    if category:
        return sum(1 for document in documents if document["category"] == category)
    return len(documents)


def get_documents(search="", category="All", limit=None):
    search = search.lower()
    documents = sheet_records("Documents")

    if search:
        documents = [
            document
            for document in documents
            if search in document["title"].lower() or search in document["category"].lower()
        ]

    if category != "All":
        documents = [document for document in documents if document["category"] == category]

    documents.sort(key=lambda item: int(item["id"] or 0), reverse=True)
    return documents[:limit] if limit else documents


def add_document(title, category, file_link, drive_file_id):
    append_record(
        "Documents",
        {
            "id": next_id("Documents"),
            "title": title,
            "category": category,
            "file_link": file_link,
            "drive_file_id": drive_file_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )


def delete_document(row_number):
    delete_record("Documents", row_number)


def stop_for_google_api_error(exc):
    status = getattr(getattr(exc, "resp", None), "status", None)
    content = getattr(exc, "content", b"")

    try:
        payload = json.loads(content.decode("utf-8"))
    except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
        payload = {}

    details = payload.get("error", {}).get("details", [])
    activation_url = None
    service_title = None

    for detail in details:
        metadata = detail.get("metadata", {})
        activation_url = activation_url or metadata.get("activationUrl")
        service_title = service_title or metadata.get("serviceTitle")

        for link in detail.get("links", []):
            activation_url = activation_url or link.get("url")

    if status == 403 and activation_url:
        st.error(f"{service_title or 'A required Google API'} is disabled.")
        st.write("Open this Google Cloud link, enable the API, wait a few minutes, then restart Streamlit:")
        st.markdown(f"[Enable API]({activation_url})")
        st.stop()

    raise exc


def stop_for_refresh_error():
    TOKEN_FILE.unlink(missing_ok=True)
    st.error("Google authorization expired or belongs to a deleted OAuth client.")
    st.write("I removed the old local token. Stop Streamlit, start it again, and authorize Google with your current client.")
    st.code(str(TOKEN_FILE), language="text")
    st.stop()


def seed_defaults():
    if not user_email_exists(DEFAULT_ADMIN_EMAIL):
        create_user("admin", DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD, "admin")

    existing_categories = {category.lower() for category in get_categories()}
    for category in DEFAULT_CATEGORIES:
        if category.lower() not in existing_categories:
            append_record("Categories", {"id": next_id("Categories"), "name": category})


def migrate_sqlite_to_sheets():
    if not SQLITE_DB_PATH.exists():
        return

    if sheet_records("Documents"):
        return

    try:
        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        for row in cur.execute("SELECT username, email, password, role FROM users"):
            if not user_email_exists(row["email"]):
                append_record(
                    "Users",
                    {
                        "id": next_id("Users"),
                        "username": row["username"] or "",
                        "email": row["email"] or "",
                        "password": row["password"] or "",
                        "role": row["role"] or "viewer",
                    },
                )

        category_names = {category.lower() for category in get_categories()}
        for row in cur.execute("SELECT name FROM categories"):
            name = row["name"] or ""
            if name and name.lower() not in category_names:
                append_record("Categories", {"id": next_id("Categories"), "name": name})
                category_names.add(name.lower())

        document_columns = {
            info[1] for info in cur.execute("PRAGMA table_info(documents)").fetchall()
        }
        link_column = "file_link" if "file_link" in document_columns else "file_path"
        drive_column = "drive_file_id" if "drive_file_id" in document_columns else "''"

        for row in cur.execute(
            f"""
            SELECT title, category, {link_column} AS file_link,
                   {drive_column} AS drive_file_id, created_at
            FROM documents
            """
        ):
            append_record(
                "Documents",
                {
                    "id": next_id("Documents"),
                    "title": row["title"] or "",
                    "category": row["category"] or "",
                    "file_link": row["file_link"] or "",
                    "drive_file_id": row["drive_file_id"] or "",
                    "created_at": row["created_at"] or "",
                },
            )
    except sqlite3.Error:
        return


# ======================
# INITIALIZE
# ======================
try:
    ensure_sheet_schema()
    migrate_sqlite_to_sheets()
    seed_defaults()
except HttpError as exc:
    stop_for_google_api_error(exc)
except RefreshError:
    stop_for_refresh_error()

if "user" not in st.session_state:
    st.session_state.user = None
    st.session_state.role = None


# ======================
# LOGIN
# ======================
def render_login():
    st.title("🔐 Login")

    login_tab, create_tab = st.tabs(["Login", "Create Account"])

    with login_tab:
        with st.form("login_form"):
            email = clean_text(st.text_input("Email"))
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", use_container_width=True)

        if submitted:
            if not email or not password:
                st.error("Enter your email and password.")
                return

            user = find_user_by_login(email, password)

            if user:
                st.session_state.user = user["username"]
                st.session_state.role = user["role"]
                st.success("Logged in")
                st.rerun()
            else:
                st.error("Invalid login")

    with create_tab:
        with st.form("create_account_form", clear_on_submit=True):
            username = clean_text(st.text_input("Name"))
            email = clean_text(st.text_input("Email", key="create_email"))
            password = st.text_input("Password", type="password", key="create_password")
            confirm_password = st.text_input(
                "Confirm Password",
                type="password",
                key="create_confirm_password",
            )
            submitted = st.form_submit_button("Create Account", use_container_width=True)

        if submitted:
            if not username or not email or not password:
                st.error("Name, email, and password are required.")
            elif "@" not in email or "." not in email:
                st.error("Enter a valid email address.")
            elif len(password) < 6:
                st.error("Password must be at least 6 characters.")
            elif password != confirm_password:
                st.error("Passwords do not match.")
            else:
                try:
                    create_user(username, email, password, "viewer")
                    st.session_state.user = username
                    st.session_state.role = "viewer"
                    st.success("Account created.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


if not st.session_state.user:
    render_login()
    st.stop()


# ======================
# SIDEBAR
# ======================
with st.sidebar:
    st.title("📂 Document System")
    st.caption(f"Signed in as {st.session_state.user} ({st.session_state.role})")

    menu_items = ["Dashboard", "Upload", "View Documents", "Categories"]
    if is_admin():
        menu_items.append("Users")

    menu = st.selectbox("Menu", menu_items)

    if st.button("Logout", use_container_width=True):
        st.session_state.user = None
        st.session_state.role = None
        st.rerun()


st.title("Document System")


# ======================
# DASHBOARD
# ======================
if menu == "Dashboard":
    st.header("📊 Analytics")

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Documents", count_documents())
    col2.metric("Total Users", len(sheet_records("Users")))
    col3.metric("Total Categories", len(get_categories()))

    st.subheader("Recent Documents")
    recent_documents = get_documents(limit=10)

    if recent_documents:
        for document in recent_documents:
            st.markdown(f"**{document['title']}**")
            st.write(f"Category: {document['category']} | Uploaded: {document['created_at']}")
            st.markdown(f"[Open File]({document['file_link']})")
            st.divider()
    else:
        st.info("No documents uploaded yet.")


# ======================
# UPLOAD
# ======================
elif menu == "Upload":
    require_admin()

    st.header("Upload Document")
    categories = get_categories()

    if not categories:
        st.warning("Create a category before uploading documents.")
        st.stop()

    if "upload_form_version" not in st.session_state:
        st.session_state.upload_form_version = 0

    upload_success = st.session_state.pop("upload_success", None)
    if upload_success:
        st.success("Uploaded successfully.")
        st.markdown(f"[Open uploaded file]({upload_success['link']})")

    form_version = st.session_state.upload_form_version

    with st.form(f"upload_form_{form_version}"):
        title = clean_text(st.text_input("Title", key=f"upload_title_{form_version}"))
        category = st.selectbox("Category", categories, key=f"upload_category_{form_version}")
        uploaded_file = st.file_uploader("Upload File", key=f"upload_file_{form_version}")
        submitted = st.form_submit_button("Upload", use_container_width=True)

    if submitted:
        if not title:
            st.error("Enter a document title.")
        elif not uploaded_file:
            st.error("Choose a file to upload.")
        else:
            temp_path = None

            try:
                suffix = Path(uploaded_file.name).suffix

                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOAD_DIR) as temp_file:
                    temp_file.write(uploaded_file.getbuffer())
                    temp_path = Path(temp_file.name)

                with st.spinner("Uploading to Google Drive..."):
                    link, drive_file_id = upload_to_drive(temp_path, uploaded_file.name, category)

                add_document(title, category, link, drive_file_id)

                st.session_state.upload_success = {"link": link}
                st.session_state.upload_form_version += 1
                st.rerun()

            except HttpError as exc:
                st.error(f"Google upload failed: {exc}")
            except Exception as exc:
                st.error(f"Upload failed: {exc}")
            finally:
                if temp_path and temp_path.exists():
                    os.remove(temp_path)


# ======================
# VIEW + DELETE DOCUMENTS
# ======================
elif menu == "View Documents":
    st.header("Documents")

    categories = ["All"] + get_categories()

    col1, col2 = st.columns([2, 1])
    search = clean_text(col1.text_input("Search"))
    selected_category = col2.selectbox("Filter by Category", categories)

    documents = get_documents(search, selected_category)

    if not documents:
        st.info("No documents found.")

    for document in documents:
        with st.container(border=True):
            left, right = st.columns([4, 1])

            with left:
                st.subheader(document["title"])
                st.write(f"Category: {document['category']}")
                st.caption(f"Uploaded: {document['created_at']}")
                st.markdown(f"[📥 Open File]({document['file_link']})")

            with right:
                download_link = get_download_link(document["drive_file_id"])
                if download_link:
                    st.link_button("Download", download_link, use_container_width=True)
                else:
                    st.link_button("Open", document["file_link"], use_container_width=True)

                if is_admin():
                    confirm = st.checkbox("Confirm delete", key=f"confirm_doc_{document['id']}")
                    if st.button(
                        "Delete file",
                        key=f"delete_doc_{document['id']}",
                        disabled=not confirm,
                        use_container_width=True,
                    ):
                        try:
                            if document["drive_file_id"]:
                                delete_drive_file(document["drive_file_id"])
                            delete_document(document["_row"])
                            st.success("File deleted.")
                            st.rerun()
                        except HttpError as exc:
                            st.error(f"Google Drive delete failed: {exc}")
                        except Exception as exc:
                            st.error(f"Delete failed: {exc}")


# ======================
# CATEGORIES
# ======================
elif menu == "Categories":
    require_admin()

    st.header("Categories")

    with st.form("category_form"):
        new_category = clean_text(st.text_input("New Category"))
        submitted = st.form_submit_button("Add", use_container_width=True)

    if submitted:
        if not new_category:
            st.error("Enter a category name.")
        else:
            try:
                add_category(new_category)
                st.success("Category added.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.subheader("Existing Categories")
    categories = get_categories()

    if not categories:
        st.info("No categories yet.")

    for category in categories:
        document_count = count_documents(category)

        with st.container(border=True):
            left, right = st.columns([4, 1])

            with left:
                st.markdown(f"**{category}**")
                st.caption(f"{document_count} document(s)")

            with right:
                confirm = st.checkbox(
                    "Confirm delete",
                    key=f"confirm_cat_{category}",
                    disabled=document_count > 0,
                )
                if st.button(
                    "Delete category",
                    key=f"delete_cat_{category}",
                    disabled=document_count > 0 or not confirm,
                    use_container_width=True,
                ):
                    delete_category(category)
                    st.success("Category deleted.")
                    st.rerun()

                if document_count > 0:
                    st.caption("Delete or move documents first.")


# ======================
# USERS
# ======================
elif menu == "Users":
    require_admin()

    st.header("Users")

    with st.form("user_form"):
        username = clean_text(st.text_input("Name"))
        email = clean_text(st.text_input("Email"))
        password = st.text_input("Password", type="password")
        role = st.selectbox("Role", ["admin", "viewer"])
        submitted = st.form_submit_button("Create", use_container_width=True)

    if submitted:
        if not username or not email or not password:
            st.error("Name, email, and password are required.")
        else:
            try:
                create_user(username, email, password, role)
                st.success("User created.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.subheader("Existing Users")
    for user in sorted(sheet_records("Users"), key=lambda item: item["username"].lower()):
        st.write(f"**{user['username']}** | {user['email']} | {user['role']}")
