#
# DOCUMENT STORAGE SYSTEM
# Storage: Supabase
# Database: SQLite
# Auth: Streamlit session
# =========================================================

import hashlib
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
import time
import streamlit as st
from supabase import create_client, Client

# =========================================================
# CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
SQLITE_DB_PATH = BASE_DIR / "documents.db"
BACKUP_DIR = BASE_DIR / "backups"
UPLOAD_DIR = BASE_DIR / "uploads"
BACKUP_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

DEFAULT_CATEGORIES = [
    "Guidelines",
    "Policies",
    "Reports",
    "Standard Operating Procedures (SOPs)",
    "Work Plans",
]


# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="Document Storage",
    page_icon="📂",
    layout="wide",
)


# =========================================================
# SECRETS
# =========================================================

def get_secret(key, default=""):
    try:
        value = st.secrets.get(key)
        if value:
            return value
    except Exception:
        pass

    for section in ("supabase", "admin"):
        try:
            value = st.secrets.get(section, {}).get(key)
            if value:
                return value
        except Exception:
            pass

    return default


def get_secret_section(section):
    try:
        return st.secrets.get(section, {})
    except Exception:
        return {}


DEFAULT_ADMIN_EMAIL = get_secret("EMAIL_ADDRESS") or get_secret("DEFAULT_ADMIN_EMAIL")
DEFAULT_ADMIN_PASSWORD = get_secret("EMAIL_PASSWORD") or get_secret("DEFAULT_ADMIN_PASSWORD")


# =========================================================
# SESSION STATE
# =========================================================

if "user" not in st.session_state:
    st.session_state.user = None
if "role" not in st.session_state:
    st.session_state.role = None
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "renaming_id" not in st.session_state:
    st.session_state.renaming_id = None
if "upload_key" not in st.session_state:
    st.session_state.upload_key = 0


# =========================================================
# SUPABASE CLIENT
# =========================================================

@st.cache_resource
def get_supabase() -> Client:
    supabase_config = get_secret_section("supabase")
    url = supabase_config.get("url")
    key = supabase_config.get("key")

    if not url or not key:
        st.error("Supabase secrets are missing.")
        st.write("Create `.streamlit/secrets.toml` with:")
        st.code(
            """
[supabase]
url = "https://your-project.supabase.co"
key = "your-anon-public-key"

EMAIL_ADDRESS = "your-admin-email"
EMAIL_PASSWORD = "your-admin-password"
            """.strip(),
            language="toml",
        )
        st.stop()

    return create_client(url, key)


# =========================================================
# DATABASE CONNECTION (Thread-safe)
# =========================================================

def get_db_connection():
    """Create a new database connection for the current thread"""
    conn = sqlite3.connect(SQLITE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def backup_database(reason="manual"):
    if not SQLITE_DB_PATH.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    today = datetime.now().strftime("%Y%m%d")
    existing_backup = next(BACKUP_DIR.glob(f"documents_{reason}_{today}_*.db"), None)
    if existing_backup:
        return existing_backup

    backup_path = BACKUP_DIR / f"documents_{reason}_{timestamp}.db"
    shutil.copy2(SQLITE_DB_PATH, backup_path)
    return backup_path


def column_exists(cur, table, column):
    cur.execute(f"PRAGMA table_info({table})")
    return column in [row[1] for row in cur.fetchall()]


def init_database():
    """Initialize database tables and seed data"""
    backup_database("startup")

    conn = get_db_connection()
    cur = conn.cursor()
    
    # Create tables
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        email TEXT UNIQUE,
        password TEXT,
        role TEXT,
        is_blocked INTEGER DEFAULT 0
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
        created_at TEXT,
        is_hidden INTEGER DEFAULT 0
    )
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS document_visibility(
        document_id INTEGER,
        user_id INTEGER,
        PRIMARY KEY (document_id, user_id)
    )
    """)
    
    # Run migrations
    for table, column, definition in [
        ("documents", "storage_path", "TEXT"),
        ("documents", "is_hidden", "INTEGER DEFAULT 0"),
        ("users", "is_blocked", "INTEGER DEFAULT 0"),
    ]:
        if not column_exists(cur, table, column):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    
    conn.commit()
    
    # Seed default categories
    for cat in DEFAULT_CATEGORIES:
        try:
            cur.execute("INSERT INTO categories(name) VALUES(?)", (cat,))
        except sqlite3.IntegrityError:
            pass
    
    conn.commit()
    
    # Keep the emergency admin account aligned with Streamlit secrets.
    if DEFAULT_ADMIN_EMAIL and DEFAULT_ADMIN_PASSWORD:
        cur.execute("SELECT id FROM users WHERE email=?", (DEFAULT_ADMIN_EMAIL,))
        existing = cur.fetchone()
        password_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
        
        if existing:
            cur.execute(
                """
                UPDATE users
                SET username=?, password=?, role=?, is_blocked=0
                WHERE email=?
                """,
                ("admin", password_hash, "admin", DEFAULT_ADMIN_EMAIL)
            )
        else:
            cur.execute(
                "INSERT INTO users(username,email,password,role,is_blocked) VALUES(?,?,?,?,?)",
                (
                    "admin",
                    DEFAULT_ADMIN_EMAIL,
                    password_hash,
                    "admin",
                    0
                )
            )
        conn.commit()
    
    conn.close()


# Initialize database and keep the secret-based admin account synced.
init_database()


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def is_admin():
    return st.session_state.get("role") == "admin"


def require_admin():
    if not is_admin():
        st.error("Admin only")
        st.stop()


# =========================================================
# SUPABASE STORAGE FUNCTIONS
# =========================================================

def upload_to_supabase(file_path: Path, filename: str, category: str):
    supabase = get_supabase()
    bucket = "documents"
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    storage_path = f"{category}/{timestamp}_{filename}"

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    supabase.storage.from_(bucket).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"upsert": "true"}
    )

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
# LOGIN PAGE
# =========================================================

def login_page():
    conn = get_db_connection()
    cur = conn.cursor()

    st.title("🔐 Login")

    tab1, tab2 = st.tabs(["Login", "Create Account"])

    # =====================================================
    # LOGIN TAB
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
                SELECT id, username, role, is_blocked
                FROM users
                WHERE email=? AND password=?
                """,
                (email, hash_password(password))
            )

            user = cur.fetchone()

            if not user:

                st.error("Invalid email or password")

            elif user[3] == 1:

                st.error("Your account has been blocked. Contact admin.")

            else:

                st.session_state.user_id = user[0]
                st.session_state.user = user[1]
                st.session_state.role = user[2]

                st.success("Logged in successfully")
                conn.close()
                st.rerun()

    # =====================================================
    # CREATE ACCOUNT TAB
    # =====================================================

    with tab2:
        with st.form("register_form", clear_on_submit=True):
            username = st.text_input("Name")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            
            submit = st.form_submit_button("Create Account", use_container_width=True)

        if submit:
            if not username or not email or not password:
                st.error("All fields are required")
            elif password != confirm_password:
                st.error("Passwords do not match")
            elif len(password) < 6:
                st.error("Password must be at least 6 characters")
            else:
                cur.execute("SELECT id FROM users WHERE email=?", (email,))
                existing = cur.fetchone()
                
                if existing:
                    st.error("Email already exists")
                else:
                    # Create account directly without verification
                    cur.execute(
                        "INSERT INTO users(username, email, password, role) VALUES(?,?,?,?)",
                        (username, email, hash_password(password), "viewer")
                    )
                    conn.commit()
                    st.success("✅ Account created successfully! You can now login.")
                    st.info("Go to the Login tab to sign in.")
                    time.sleep(2)
                    conn.close()
                    st.rerun()
    
    conn.close()


# =========================================================
# SHOW LOGIN PAGE IF NOT LOGGED IN
# =========================================================

if not st.session_state.user:
    login_page()
    st.stop()


# =========================================================
# SIDEBAR NAVIGATION
# =========================================================

with st.sidebar:

    st.title("📂 Document Storage")
    st.caption(f"{st.session_state.user} ({st.session_state.role})")

    menu_items = ["Dashboard", "Upload", "View Documents"]

    if is_admin():
        menu_items += ["Categories", "Users", "Admin Tools"]

    menu = st.selectbox(
        "Menu",
        menu_items,
        key="main_menu"
    )

    if st.button("Logout", use_container_width=True):
        st.session_state.user = None
        st.session_state.role = None
        st.session_state.user_id = None
        st.rerun()


# =========================================================
# DASHBOARD
# =========================================================

if menu == "Dashboard":
    conn = get_db_connection()
    cur = conn.cursor()

    st.title("📊 Dashboard")

    cur.execute("SELECT COUNT(*) FROM documents WHERE is_hidden=0")
    total_docs = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM categories")
    total_categories = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1")
    blocked_users = cur.fetchone()[0]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Documents", total_docs)
    col2.metric("Users", total_users)
    col3.metric("Categories", total_categories)
    col4.metric("Blocked Users", blocked_users)
    
    conn.close()


# =========================================================
# UPLOAD
# =========================================================

elif menu == "Upload":
    conn = get_db_connection()
    cur = conn.cursor()

    require_admin()

    st.title("📤 Upload")

    cur.execute("SELECT name FROM categories")
    categories = [row[0] for row in cur.fetchall()]

    with st.form(key=f"upload_form_{st.session_state.upload_key}"):
        title = st.text_input("Title", value="")
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

                st.session_state.upload_key += 1
                conn.close()
                st.rerun()

            except Exception as e:
                st.error(f"Upload failed: {e}")

            finally:
                if temp_path and temp_path.exists():
                    temp_path.unlink(missing_ok=True)
    
    conn.close()


# =========================================================
# VIEW DOCUMENTS
# =========================================================

elif menu == "View Documents":
    conn = get_db_connection()
    cur = conn.cursor()

    st.title("📁 Documents")

    col1, col2 = st.columns([2, 1])
    with col1:
        search = st.text_input("Search")
    with col2:
        cur.execute("SELECT name FROM categories")
        all_categories = ["All"] + [row[0] for row in cur.fetchall()]
        selected_category = st.selectbox("Filter by Category", all_categories)

    # Admins see all docs including hidden
    # Viewers only see non-hidden docs
    if is_admin():
        cur.execute("""
            SELECT id, title, category, file_link, storage_path, created_at, is_hidden
            FROM documents
            ORDER BY id DESC
        """)
    else:
        cur.execute("""
            SELECT DISTINCT d.id, d.title, d.category, d.file_link, d.storage_path, d.created_at, d.is_hidden
            FROM documents d
            WHERE d.is_hidden = 0
            AND d.id NOT IN (
                SELECT dv.document_id 
                FROM document_visibility dv 
                WHERE dv.user_id = ?
            )
            ORDER BY d.id DESC
        """, (st.session_state.user_id,))

    documents = cur.fetchall()

    if not documents:
        st.info("No documents found.")

    for doc in documents:

        doc_id, title, category, file_link, storage_path, created_at, is_hidden = doc

        if search.lower() not in str(title).lower():
            continue

        if selected_category != "All" and category != selected_category:
            continue

        with st.container(border=True):

            # ── Rename mode ──────────────────────────────
            if is_admin() and st.session_state.renaming_id == doc_id:

                new_title = st.text_input(
                    "New name",
                    value=title,
                    key=f"rename_input_{doc_id}"
                )

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ Save", key=f"save_{doc_id}", use_container_width=True):
                        if new_title.strip():
                            cur.execute(
                                "UPDATE documents SET title=? WHERE id=?",
                                (new_title.strip(), doc_id)
                            )
                            conn.commit()
                            st.session_state.renaming_id = None
                            st.success("Renamed!")
                            conn.close()
                            st.rerun()
                        else:
                            st.error("Name cannot be empty")
                with c2:
                    if st.button("❌ Cancel", key=f"cancel_{doc_id}", use_container_width=True):
                        st.session_state.renaming_id = None
                        st.rerun()

            # ── Normal mode ──────────────────────────────
            else:

                # Hidden badge
                col_title, col_badge = st.columns([4, 1])
                with col_title:
                    st.subheader(title)
                with col_badge:
                    if is_hidden:
                        st.warning("🔒 Hidden")

                st.write(f"Category: {category}")
                st.caption(created_at)

                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"[📥 Open File]({file_link})")
                with c2:
                    st.link_button("Download", file_link)

                if is_admin():

                    c3, c4, c5 = st.columns(3)

                    with c3:
                        if st.button("✏️ Rename", key=f"rename_{doc_id}", use_container_width=True):
                            st.session_state.renaming_id = doc_id
                            st.rerun()

                    with c4:
                        hide_label = "👁️ Unhide" if is_hidden else "🙈 Hide"
                        if st.button(hide_label, key=f"hide_{doc_id}", use_container_width=True):
                            cur.execute(
                                "UPDATE documents SET is_hidden=? WHERE id=?",
                                (0 if is_hidden else 1, doc_id)
                            )
                            conn.commit()
                            st.rerun()

                    with c5:
                        if st.button("🗑️ Delete", key=f"delete_{doc_id}", use_container_width=True):
                            try:
                                delete_from_supabase(storage_path)
                                cur.execute("DELETE FROM documents WHERE id=?", (doc_id,))
                                conn.commit()
                                st.success("Deleted")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Delete failed: {e}")
    
    conn.close()


# =========================================================
# CATEGORIES
# =========================================================

elif menu == "Categories":
    conn = get_db_connection()
    cur = conn.cursor()

    require_admin()

    st.title("📂 Categories")

    with st.form("category_form"):
        new_category = st.text_input("New Category")
        submit = st.form_submit_button("Add")

    if submit:
        if new_category.strip():
            try:
                cur.execute("INSERT INTO categories(name) VALUES(?)", (new_category.strip(),))
                conn.commit()
                st.success("Added")
                conn.close()
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("Category already exists")
        else:
            st.error("Category name cannot be empty")

    st.subheader("Existing Categories")
    cur.execute("SELECT id, name FROM categories")
    categories = cur.fetchall()
    
    if categories:
        for cat in categories:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"📁 {cat[1]}")
            with col2:
                if st.button("🗑️", key=f"del_cat_{cat[0]}", help="Delete category"):
                    try:
                        cur.execute("DELETE FROM categories WHERE id=?", (cat[0],))
                        conn.commit()
                        conn.close()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Cannot delete: {e}")
    else:
        st.info("No categories found")
    
    conn.close()


# =========================================================
# USERS
# =========================================================

elif menu == "Users":
    conn = get_db_connection()
    cur = conn.cursor()

    require_admin()

    st.title("👥 Users")

    cur.execute("SELECT id, username, email, role, is_blocked FROM users ORDER BY username")
    users = cur.fetchall()

    for u in users:
        uid, uname, uemail, urole, ublocked = u

        with st.container(border=True):

            c1, c2, c3 = st.columns([3, 1, 1])

            with c1:
                status = "🚫 Blocked" if ublocked else "✅ Active"
                st.write(f"**{uname}** | {uemail} | {urole} | {status}")

            with c2:
                new_role = "viewer" if urole == "admin" else "admin"
                label = "⬇️ Demote" if urole == "admin" else "⬆️ Promote"
                if st.button(label, key=f"role_{uid}", use_container_width=True):
                    if uemail != DEFAULT_ADMIN_EMAIL:
                        cur.execute(
                            "UPDATE users SET role=? WHERE id=?",
                            (new_role, uid)
                        )
                        conn.commit()
                        st.success(f"Role changed to {new_role}")
                        conn.close()
                        st.rerun()
                    else:
                        st.error("Cannot change default admin role")

            with c3:
                block_label = "✅ Unblock" if ublocked else "🚫 Block"
                if st.button(block_label, key=f"block_{uid}", use_container_width=True):
                    if uemail != DEFAULT_ADMIN_EMAIL:
                        cur.execute(
                            "UPDATE users SET is_blocked=? WHERE id=?",
                            (0 if ublocked else 1, uid)
                        )
                        conn.commit()
                        conn.close()
                        st.rerun()
                    else:
                        st.error("Cannot block default admin")
    
    conn.close()


# =========================================================
# ADMIN TOOLS
# =========================================================

elif menu == "Admin Tools":
    conn = get_db_connection()
    cur = conn.cursor()

    require_admin()

    st.title("🛠️ Admin Tools")

    tab1, tab2, tab3 = st.tabs([
        "📊 Storage Stats",
        "🔑 Reset Password",
        "🙈 Manage Visibility",
    ])

    # ── Storage Stats ─────────────────────────────────
    with tab1:

        st.subheader("Storage Stats")

        cur.execute("SELECT COUNT(*) FROM documents")
        total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM documents WHERE is_hidden=1")
        hidden = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM documents WHERE is_hidden=0")
        visible = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE is_blocked=0")
        active_users = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1")
        blocked_users = cur.fetchone()[0]

        cur.execute("SELECT category, COUNT(*) as cnt FROM documents GROUP BY category ORDER BY cnt DESC")
        cat_counts = cur.fetchall()

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Documents", total)
        c2.metric("Visible", visible)
        c3.metric("Hidden", hidden)

        c4, c5 = st.columns(2)
        c4.metric("Active Users", active_users)
        c5.metric("Blocked Users", blocked_users)

        if cat_counts:
            st.divider()
            st.write("**Documents per Category:**")
            for cat, count in cat_counts:
                st.write(f"- {cat}: **{count}** file(s)")

    # ── Reset Password ────────────────────────────────
    with tab2:

        st.subheader("Reset User Password")

        cur.execute("SELECT id, username, email FROM users ORDER BY username")
        all_users = cur.fetchall()

        if not all_users:
            st.info("No users found.")
        else:
            user_options = {f"{u[1]} ({u[2]})": u[0] for u in all_users}

            with st.form("reset_password_form"):
                selected_user = st.selectbox("Select User", list(user_options.keys()))
                new_password = st.text_input("New Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                submit = st.form_submit_button("Reset Password", use_container_width=True)

            if submit:
                if not new_password:
                    st.error("Password cannot be empty")
                elif new_password != confirm_password:
                    st.error("Passwords do not match")
                elif len(new_password) < 6:
                    st.error("Password must be at least 6 characters")
                else:
                    uid = user_options[selected_user]
                    cur.execute(
                        "UPDATE users SET password=? WHERE id=?",
                        (hash_password(new_password), uid)
                    )
                    conn.commit()
                    st.success(f"Password reset for {selected_user}")

        st.caption("The default admin account is synced from Streamlit secrets on startup. Update EMAIL_ADDRESS and EMAIL_PASSWORD in secrets to recover admin access.")

    # ── Manage Visibility ─────────────────────────────
    with tab3:

        st.subheader("Manage Document Visibility")
        st.caption("Restrict specific documents from specific users")

        cur.execute("""
            SELECT id, title, category, is_hidden
            FROM documents
            ORDER BY category, title
        """)
        all_docs = cur.fetchall()

        cur.execute(
            "SELECT id, username, email FROM users WHERE role='viewer' AND is_blocked=0 ORDER BY username"
        )
        viewers = cur.fetchall()

        if not all_docs:
            st.info("No documents found.")
        elif not viewers:
            st.info("No active viewers found.")
        else:
            doc_options = {f"{d[2]} — {d[1]}": d[0] for d in all_docs}
            viewer_options = {f"{v[1]} ({v[2]})": v[0] for v in viewers}

            selected_doc = st.selectbox("Select Document", list(doc_options.keys()))
            doc_id = doc_options[selected_doc]

            # Show current visibility status
            cur.execute("""
                SELECT u.username, u.email
                FROM document_visibility dv
                JOIN users u ON u.id = dv.user_id
                WHERE dv.document_id = ?
            """, (doc_id,))
            restricted_users = cur.fetchall()

            if restricted_users:
                st.warning(f"This file is hidden from: {', '.join([r[0] for r in restricted_users])}")
                st.info("These users cannot see this file. All other viewers can see it.")
            else:
                st.success("This file is visible to all viewers")

            st.divider()

            c1, c2 = st.columns(2)

            with c1:
                st.write("**Hide from specific user:**")
                selected_viewer = st.selectbox("Select Viewer", list(viewer_options.keys()))
                if st.button("🔒 Hide from User", use_container_width=True):
                    vid = viewer_options[selected_viewer]
                    try:
                        cur.execute(
                            "INSERT INTO document_visibility(document_id, user_id) VALUES(?,?)",
                            (doc_id, vid)
                        )
                        conn.commit()
                        st.success("Document hidden from this user!")
                        conn.close()
                        st.rerun()
                    except Exception:
                        st.error("Already hidden from this user")

            with c2:
                st.write("**Remove restriction:**")
                if restricted_users:
                    remove_options = {r[0]: r for r in restricted_users}
                    selected_remove = st.selectbox("Select User to Unhide", list(remove_options.keys()))
                    if st.button("🔓 Show to User", use_container_width=True):
                        cur.execute("""
                            DELETE FROM document_visibility
                            WHERE document_id=? AND user_id=(
                                SELECT id FROM users WHERE username=?
                            )
                        """, (doc_id, selected_remove))
                        conn.commit()
                        st.success("Restriction removed!")
                        conn.close()
                        st.rerun()
                else:
                    st.info("No restrictions to remove")
    
    conn.close()
