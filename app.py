# =========================================================
# DOCUMENT STORAGE SYSTEM (STREAMLIT CLOUD READY)
# Auth: Supabase
# Storage: Supabase
# DB: SQLite (documents only)
# =========================================================

import streamlit as st
import sqlite3
import tempfile
import os
from pathlib import Path
from datetime import datetime
from supabase import create_client

# =========================================================
# CONFIG (CLOUD SAFE)
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(os.getenv("STREAMLIT_APP_DIR", BASE_DIR))
SQLITE_DB_PATH = DATA_DIR / "documents.db"

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CATEGORIES = [
    "Guidelines",
    "Policies",
    "Reports",
    "SOPs",
    "Work Plans",
]

# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="Document Storage",
    page_icon="📂",
    layout="wide",
)

# =========================================================
# SUPABASE CLIENT (CACHED)
# =========================================================

@st.cache_resource
def get_supabase():
    return create_client(
        st.secrets["supabase"]["url"],
        st.secrets["supabase"]["key"]
    )

# =========================================================
# SQLITE (DOCUMENTS ONLY)
# =========================================================

def get_db_connection():
    conn = sqlite3.connect(SQLITE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    conn = get_db_connection()
    cur = conn.cursor()

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
        created_at TEXT,
        is_hidden INTEGER DEFAULT 0
    )
    """)

    conn.commit()

    for cat in DEFAULT_CATEGORIES:
        try:
            cur.execute("INSERT INTO categories(name) VALUES(?)", (cat,))
        except:
            pass

    conn.commit()
    conn.close()


# Auto init safe
if "db_ready" not in st.session_state:
    init_database()
    st.session_state.db_ready = True

# =========================================================
# SESSION STATE
# =========================================================

if "user" not in st.session_state:
    st.session_state.user = None
if "user_id" not in st.session_state:
    st.session_state.user_id = None

# =========================================================
# AUTH HELPERS
# =========================================================

def require_auth():
    supabase = get_supabase()
    session = supabase.auth.get_session()

    if not session or not session.user:
        return False

    st.session_state.user = session.user.email
    st.session_state.user_id = session.user.id
    return True


def login_page():
    st.title("🔐 Login")

    supabase = get_supabase()

    tab1, tab2 = st.tabs(["Login", "Create Account"])

    # ---------------- LOGIN ----------------
    with tab1:
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        if st.button("Login"):
            try:
                res = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })

                user = res.user

                st.session_state.user = user.email
                st.session_state.user_id = user.id

                st.success("Login successful")
                st.rerun()

            except:
                st.error("Invalid credentials")

    # ---------------- SIGNUP ----------------
    with tab2:
        email = st.text_input("New Email")
        password = st.text_input("New Password", type="password")

        if st.button("Create Account"):
            try:
                supabase.auth.sign_up({
                    "email": email,
                    "password": password
                })
                st.success("Account created. Please login.")
            except:
                st.error("Signup failed")


# =========================================================
# LOGOUT
# =========================================================

def logout():
    if st.sidebar.button("Logout"):
        supabase = get_supabase()
        supabase.auth.sign_out()

        st.session_state.user = None
        st.session_state.user_id = None
        st.rerun()

# =========================================================
# MAIN AUTH CHECK
# =========================================================

if not require_auth():
    login_page()
    st.stop()

logout()

# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.title("📂 Document System")
st.sidebar.caption(f"{st.session_state.user}")

menu = st.sidebar.selectbox(
    "Menu",
    ["Dashboard", "Upload", "View Documents", "Categories"]
)

# =========================================================
# DASHBOARD
# =========================================================

if menu == "Dashboard":
    st.title("📊 Dashboard")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM documents")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM categories")
    cats = cur.fetchone()[0]

    col1, col2 = st.columns(2)
    col1.metric("Documents", total)
    col2.metric("Categories", cats)

    conn.close()

# =========================================================
# UPLOAD
# =========================================================

elif menu == "Upload":
    st.title("📤 Upload Document")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT name FROM categories")
    categories = [c[0] for c in cur.fetchall()]

    title = st.text_input("Title")
    category = st.selectbox("Category", categories)
    file = st.file_uploader("File")

    if st.button("Upload"):
        if file:
            supabase = get_supabase()

            path = f"{category}/{file.name}"

            supabase.storage.from_("documents").upload(
                path,
                file.getvalue(),
                {"upsert": "true"}
            )

            url = supabase.storage.from_("documents").get_public_url(path)

            cur.execute("""
                INSERT INTO documents(title, category, file_link, storage_path, created_at)
                VALUES(?,?,?,?,?)
            """, (
                title,
                category,
                url,
                path,
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ))

            conn.commit()
            st.success("Uploaded successfully")
            st.markdown(f"[Open File]({url})")

    conn.close()

# =========================================================
# VIEW DOCUMENTS
# =========================================================

elif menu == "View Documents":
    st.title("📁 Documents")

    conn = get_db_connection()
    cur = conn.cursor()

    search = st.text_input("Search")

    cur.execute("""
        SELECT id, title, category, file_link, created_at
        FROM documents
        ORDER BY id DESC
    """)

    docs = cur.fetchall()

    for d in docs:
        if search and search.lower() not in d["title"].lower():
            continue

        with st.container(border=True):
            st.subheader(d["title"])
            st.write(d["category"])
            st.caption(d["created_at"])
            st.markdown(f"[Open File]({d['file_link']})")

    conn.close()

# =========================================================
# CATEGORIES
# =========================================================

elif menu == "Categories":
    st.title("📂 Categories")

    conn = get_db_connection()
    cur = conn.cursor()

    new_cat = st.text_input("New Category")

    if st.button("Add"):
        try:
            cur.execute("INSERT INTO categories(name) VALUES(?)", (new_cat,))
            conn.commit()
            st.success("Added")
        except:
            st.error("Category exists")

    cur.execute("SELECT name FROM categories")
    cats = cur.fetchall()

    for c in cats:
        st.write("📁", c[0])

    conn.close()
