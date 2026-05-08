# =========================================================
# DOCUMENT STORAGE SYSTEM
# Storage: Supabase (replaces Google Drive)
# Database: SQLite
# Auth: Streamlit session
# =========================================================

import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from supabase import create_client, Client


# =========================================================
# CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
SQLITE_DB_PATH = BASE_DIR / "documents.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

DEFAULT_ADMIN_EMAIL = "admin@gmail.com"
DEFAULT_ADMIN_PASSWORD = "Admin@2026"

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
# SUPABASE CLIENT
# =========================================================

@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)


# =========================================================
# HELPERS
# =========================================================

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def is_admin():
    return st.session_state.get("role") == "admin"


def require_admin():
    if not is_admin():
        st.error("Admin only")
        st.stop()


# =========================================================
# SUPABASE STORAGE
# =========================================================

def upload_to_supabase(file_path: Path, filename: str, category: str):
    supabase = get_supabase()

    bucket = "documents"

    # Unique path: category/timestamp_filename
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    storage_path = f"{category}/{timestamp}_{filename}"

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    # Upload file
    supabase.storage.from_(bucket).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"upsert": "true"}
    )

    # Get public URL
    public_url = supabase.storage.from_(bucket).get_public_url(storage_path)

    return public_url, storage_path


def delete_from_supabase(storage_path: str):
    if not storage_path:
        return
    try:
        get_supabase().storage.from_("documents").remove([storage_path])
    except Exception:
        pass


# =========================================================
# SQLITE SETUP
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
    storage_path TEXT,
    created_at TEXT
)
""")

conn.commit()


# =========================================================
# SEED DEFAULTS
# =========================================================

cur.execute("SELECT * FROM users WHERE email=?", (DEFAULT_ADMIN_EMAIL,))

if not cur.fetchone():
    cur.execute(
        "INSERT INTO users(username,email,password,role) VALUES(?,?,?,?)",
        ("admin", DEFAULT_ADMIN_EMAIL, hash_password(DEFAULT_ADMIN_PASSWORD), "admin")
    )
    conn.commit()

for category in DEFAULT_CATEGORIES:
    try:
        cur.execute("INSERT INTO categories(name) VALUES(?)", (category,))
    except Exception:
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

    tab1, tab2 = st.tabs(["Login", "Create Account"])

    with tab1:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Login", use_container_width=True)

        if submit:
            cur.execute(
                "SELECT username, role FROM users WHERE email=? AND password=?",
                (email, hash_password(password))
            )
            user = cur.fetchone()

            if user:
                st.session_state.user = user[0]
                st.session_state.role = user[1]
                st.success("Logged in")
                st.rerun()
            else:
                st.error("Invalid login")

    with tab2:
        with st.form("register_form"):
            username = st.text_input("Name")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Create Account", use_container_width=True)

        if submit:
            try:
                cur.execute(
                    "INSERT INTO users(username,email,password,role) VALUES(?,?,?,?)",
                    (username, email, hash_password(password), "viewer")
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
    st.caption(f"{st.session_state.user} ({st.session_state.role})")

    menu_items = ["Dashboard", "Upload", "View Documents"]

    if is_admin():
        menu_items += ["Categories", "Users"]

    menu = st.selectbox("Menu", menu_items)

    if st.button("Logout", use_container_width=True):
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
    categories = [row[0] for row in cur.fetchall()]

    with st.form("upload_form"):
        title = st.text_input("Title")
        category = st.selectbox("Category", categories)
        uploaded_file = st.file_uploader("Choose File")
        submit = st.form_submit_button("Upload", use_container_width=True)

    if submit:

        if not uploaded_file:
            st.error("Choose a file")

        else:
            temp_path = None

            try:
                suffix = Path(uploaded_file.name).suffix

                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=suffix,
                    dir=UPLOAD_DIR
                ) as temp_file:
                    temp_file.write(uploaded_file.getbuffer())
                    temp_path = Path(temp_file.name)

                with st.spinner("Uploading..."):
                    public_url, storage_path = upload_to_supabase(
                        temp_path,
                        uploaded_file.name,
                        category
                    )

                cur.execute(
                    """
                    INSERT INTO documents(title, category, file_link, storage_path, created_at)
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        title,
                        category,
                        public_url,
                        storage_path,
                        datetime.now().strftime("%Y-%m-%d %H:%M")
                    )
                )
                conn.commit()

                st.success("Uploaded successfully!")
                st.markdown(f"[Open File]({public_url})")

            except Exception as e:
                st.error(f"Upload failed: {e}")

            finally:
                if temp_path and temp_path.exists():
                    temp_path.unlink(missing_ok=True)


# =========================================================
# VIEW DOCUMENTS
# =========================================================

elif menu == "View Documents":

    st.title("📁 Documents")

    search = st.text_input("Search")

    cur.execute("""
    SELECT id, title, category, file_link, storage_path, created_at
    FROM documents
    ORDER BY id DESC
    """)

    documents = cur.fetchall()

    if not documents:
        st.info("No documents uploaded yet.")

    for doc in documents:

        if search.lower() not in str(doc[1]).lower():
            continue

        with st.container(border=True):

            st.subheader(doc[1])
            st.write(f"Category: {doc[2]}")
            st.caption(doc[5])

            col1, col2 = st.columns([1, 1])

            with col1:
                st.markdown(f"[📥 Open File]({doc[3]})")

            with col2:
                st.link_button("Download", doc[3])

            if is_admin():
                if st.button("Delete", key=f"delete_{doc[0]}"):
                    try:
                        delete_from_supabase(doc[4])
                        cur.execute("DELETE FROM documents WHERE id=?", (doc[0],))
                        conn.commit()
                        st.success("Deleted")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Delete failed: {e}")


# =========================================================
# CATEGORIES
# =========================================================

elif menu == "Categories":

    require_admin()

    st.title("📂 Categories")

    with st.form("category_form"):
        new_category = st.text_input("New Category")
        submit = st.form_submit_button("Add")

    if submit:
        try:
            cur.execute("INSERT INTO categories(name) VALUES(?)", (new_category,))
            conn.commit()
            st.success("Added")
            st.rerun()
        except sqlite3.IntegrityError:
            st.error("Category already exists")

    cur.execute("SELECT id, name FROM categories")
    categories = cur.fetchall()

    for cat in categories:
        st.write(cat[1])


# =========================================================
# USERS
# =========================================================

elif menu == "Users":

    require_admin()

    st.title("👥 Users")

    cur.execute("SELECT username, email, role FROM users ORDER BY username")
    users = cur.fetchall()

    for user in users:
        st.write(f"**{user[0]}** | {user[1]} | {user[2]}")
