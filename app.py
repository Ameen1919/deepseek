import streamlit as st
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
import pandas as pd
from datetime import datetime, date
import io
import os
import urllib.request
from fpdf import FPDF
import shutil
import zipfile
import json
import hashlib
import re
import arabic_reshaper
from bidi.algorithm import get_display
from contextlib import contextmanager

# ======================== إعدادات الصفحة ========================
st.set_page_config(page_title="مخزن النظافة", layout="wide", initial_sidebar_state="expanded")

# ======================== إدارة الحالة العامة والإعدادات الدائمة ========================
APP_CONFIG_FILE = 'app_config.json'

def load_app_config():
    if os.path.exists(APP_CONFIG_FILE):
        with open(APP_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        'font_size': 100,
        'theme_color': "#00a86b",
        'logo_path': None,
        'store_name': "مخزن النظافة"
    }

def save_app_config(config):
    with open(APP_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

saved_config = load_app_config()

if 'font_size' not in st.session_state:
    st.session_state.font_size = saved_config.get('font_size', 100)
if 'theme_color' not in st.session_state:
    st.session_state.theme_color = saved_config.get('theme_color', "#00a86b")
if 'logo_path' not in st.session_state:
    st.session_state.logo_path = saved_config.get('logo_path', None)
if 'store_name' not in st.session_state:
    st.session_state.store_name = saved_config.get('store_name', "مخزن النظافة")

# إصلاح الألوان والتباين لضمان عدم اختفاء الأيقونات والنصوص
def apply_theme():
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap');
    * {{ font-family: 'Tajawal', sans-serif; }}
    html, body, [class*="css"] {{ direction: rtl; text-align: right; font-size: {st.session_state.font_size}% !important; }}
    
    /* الأزرار والأيقونات */
    .stButton > button {{
        color: #ffffff !important;
        background-color: #008c56 !important;
        border-radius: 8px !important;
        border: 1px solid #006b42 !important;
    }}
    .stButton > button:hover {{
        background-color: #006b42 !important;
        color: #ffffff !important;
        border-color: #004d2f !important;
    }}
    .stDownloadButton > button {{
        color: #ffffff !important;
        background-color: #0275d8 !important;
        border-radius: 8px !important;
    }}
    .stDownloadButton > button:hover {{
        background-color: #025aa5 !important;
        color: #ffffff !important;
    }}
    </style>""", unsafe_allow_html=True)

apply_theme()

# ======================== الاتصال بقاعدة بيانات Supabase ========================
DB_URL = "postgresql://postgres.krrbpyleyvcmshcqcdog:Ameen_Ali_1919@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres"

@st.cache_resource
def init_connection_pool():
    return SimpleConnectionPool(1, 20, dsn=DB_URL, connect_timeout=10)

pool = init_connection_pool()

@contextmanager
def get_db():
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)

BACKUP_FOLDER = 'backups'
ATTACHMENTS_FOLDER = 'attachments'

if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)
if not os.path.exists(ATTACHMENTS_FOLDER):
    os.makedirs(ATTACHMENTS_FOLDER)

# ======================== دوال مساعدة ========================
def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

@st.cache_resource
def init_db():
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS units (id SERIAL PRIMARY KEY, unit_name TEXT UNIQUE, unit_symbol TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS suppliers (id SERIAL PRIMARY KEY, supplier_name TEXT UNIQUE, contact_info TEXT, notes TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS items (
                id SERIAL PRIMARY KEY,
                item_code TEXT UNIQUE,
                name TEXT NOT NULL UNIQUE,
                unit_id INTEGER REFERENCES units(id),
                min_qty REAL DEFAULT 0,
                max_qty REAL DEFAULT 100,
                current_balance REAL DEFAULT 0,
                primary_supplier_id INTEGER REFERENCES suppliers(id),
                shelf_life_days INTEGER DEFAULT 365,
                notes TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_date TEXT,
                last_updated TEXT
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS hotels (id SERIAL PRIMARY KEY, name TEXT UNIQUE, contact_person TEXT, phone TEXT, notes TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS outward_orders (
                id SERIAL PRIMARY KEY,
                order_number TEXT UNIQUE,
                hotel_id INTEGER REFERENCES hotels(id),
                recipient_name TEXT,
                order_date TEXT,
                notes TEXT,
                created_by TEXT
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                transaction_type TEXT,
                item_id INTEGER REFERENCES items(id),
                hotel_id INTEGER REFERENCES hotels(id),
                qty REAL,
                unit_id INTEGER REFERENCES units(id),
                batch_number TEXT,
                expiry_date TEXT,
                transaction_date TEXT,
                notes TEXT,
                created_by TEXT DEFAULT 'أمين المخزن',
                attachment TEXT,
                order_id INTEGER REFERENCES outward_orders(id),
                supplier_name TEXT,
                unit_price REAL DEFAULT 0
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS inventory_counts (
                id SERIAL PRIMARY KEY,
                count_date TEXT,
                item_id INTEGER REFERENCES items(id),
                expected_qty REAL,
                actual_qty REAL,
                difference REAL,
                notes TEXT,
                counted_by TEXT
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS expiry_alerts (
                id SERIAL PRIMARY KEY,
                item_id INTEGER REFERENCES items(id),
                batch_number TEXT,
                expiry_date TEXT,
                qty_remaining REAL,
                is_consumed BOOLEAN DEFAULT FALSE
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                full_name TEXT,
                is_active BOOLEAN DEFAULT TRUE
            )''')

            # ضمان وجود كافة الأعمدة في جدول items لتفادي UndefinedColumn
            c.execute('''ALTER TABLE items ADD COLUMN IF NOT EXISTS item_code TEXT;''')
            c.execute('''ALTER TABLE items ADD COLUMN IF NOT EXISTS min_qty REAL DEFAULT 0;''')
            c.execute('''ALTER TABLE items ADD COLUMN IF NOT EXISTS max_qty REAL DEFAULT 100;''')
            c.execute('''ALTER TABLE items ADD COLUMN IF NOT EXISTS current_balance REAL DEFAULT 0;''')
            c.execute('''ALTER TABLE items ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;''')

            # ضمان وجود كافة الأعمدة في جدول users
            c.execute('''ALTER TABLE users ALTER COLUMN username TYPE TEXT;''')
            c.execute('''ALTER TABLE users ALTER COLUMN password TYPE TEXT;''')
            c.execute('''ALTER TABLE users ALTER COLUMN role TYPE TEXT;''')
            c.execute('''ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT;''')
            c.execute('''ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;''')

            for u_name, u_sym in [('قطعة','قطعة'),('لتر','لتر'),('كيلو','كجم'),('متر','متر'),
                                 ('كرتونة','كرتونة'),('رول','رول'),('زجاجة','زجاجة'),('علبة','علبة'),('كيس','كيس')]:
                c.execute("INSERT INTO units (unit_name, unit_symbol) VALUES (%s,%s) ON CONFLICT (unit_name) DO NOTHING",(u_name,u_sym))

            default_users = [
                ('admin',hash_password('admin123'),'super_admin','المدير العام'),
                ('مشتريات',hash_password('buy123'),'purchasing','مسؤول المشتريات'),
                ('صرف',hash_password('out123'),'disbursement','مسؤول الصرف'),
                ('مشرف1',hash_password('sup123'),'supervisor','مشرف أول'),
                ('مشرف2',hash_password('sup456'),'supervisor','مشرف ثاني')
            ]
            
            for uname,pwd,role,fname in default_users:
                c.execute("""
                    INSERT INTO users (username, password, role, full_name, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    ON CONFLICT (username) DO UPDATE 
                    SET password = EXCLUDED.password, role = EXCLUDED.role, full_name = EXCLUDED.full_name, is_active = TRUE;
                """, (uname, pwd, role, fname))
    return True

def login(username, password):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM users WHERE username=%s AND password=%s AND is_active=TRUE",
                      (username, hash_password(password)))
            user = c.fetchone()
    if user:
        st.session_state.user = dict(user)
        st.session_state.logged_in = True
        return True
    return False

def logout():
    st.session_state.logged_in = False
    st.session_state.user = None
    st.rerun()

def check_perm(role=None):
    if not st.session_state.get('logged_in'): return False
    if st.session_state.user['role']=='super_admin': return True
    if role and st.session_state.user['role']==role: return True
    return False

def has_role(role):
    return st.session_state.get('user',{}).get('role')==role

# ======================== بدء التشغيل ========================
init_db()

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None

if not st.session_state.logged_in:
    st.title("🔐 تسجيل الدخول")
    with st.form("login"):
        uname = st.text_input("اسم المستخدم")
        pwd = st.text_input("كلمة المرور", type="password")
        if st.form_submit_button("🔑 دخول"):
            if login(uname, pwd):
                st.success("تم الدخول بنجاح"); st.rerun()
            else: st.error("خطأ في بيانات الدخول، يرجى التثبت من اسم المستخدم وكلمة المرور.")
    st.stop()

# ======================== الواجهة الرئيسية والقائمة ========================
st.sidebar.title(f"🧹 {st.session_state.store_name}")
st.sidebar.write(f"👤 **{st.session_state.user['full_name']}**")
if st.sidebar.button("🚪 تسجيل الخروج"):
    logout()

menu = []
if check_perm():
    menu = ["📊 لوحة التحكم","📦 إدارة الأصناف","📏 الوحدات","🏨 الفنادق","🏢 الموردين",
            "📥 الوارد","📤 الصادر","📝 الجرد","📈 التقارير",
            "🗑️ إدارة الحركات (حذف)","💾 النسخ الاحتياطي","👥 المستخدمين"]
elif has_role('purchasing'):
    menu = ["📊 لوحة التحكم","📥 الوارد","📈 التقارير"]
elif has_role('disbursement'):
    menu = ["📊 لوحة التحكم","📤 الصادر","📈 التقارير"]
elif has_role('supervisor'):
    menu = ["📊 لوحة التحكم","📝 الجرد","📈 التقارير"]

choice = st.sidebar.radio("القائمة الرئيسية", menu)

# ======================== الصفحات ========================
if choice == "📊 لوحة التحكم":
    st.header("📊 لوحة التحكم والمؤشرات")
    today = date.today()
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT COUNT(*) FROM items WHERE is_active=TRUE")
            total = c.fetchone()['count']
            c.execute("SELECT COUNT(*) FROM items WHERE current_balance<=min_qty AND is_active=TRUE")
            low = c.fetchone()['count']
            c.execute("SELECT COUNT(*) FROM expiry_alerts WHERE is_consumed=FALSE AND expiry_date<%s",(today.isoformat(),))
            exp = c.fetchone()['count']
            
            c1, c2, c3 = st.columns(3)
            c1.metric("📦 إجمالي الأصناف", total)
            c2.metric("⚠️ أصناف تحت الحد الأدنى", low)
            c3.metric("🚨 منتهية الصلاحية", exp)
            st.divider()

elif choice == "💾 النسخ الاحتياطي":
    if not check_perm(): st.error("غير مصرح"); st.stop()
    st.header("💾 إدارة النسخ الاحتياطي واستعادة البيانات")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📥 إنتاج نسخة احتياطية جديدة")
        if st.button("📦 إنشاء نسخة احتياطية الآن"):
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as c:
                    c.execute("SELECT * FROM items")
                    items_data = c.fetchall()
            df_items = pd.DataFrame(items_data)
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine='xlsxwriter') as w:
                df_items.to_excel(w, sheet_name='items', index=False)
            st.download_button("⬇️ تحميل النسخة الاحتياطية (Excel)", data=out.getvalue(), file_name=f"backup_{date.today()}.xlsx")

    with col2:
        st.subheader("📤 رفع نسخة احتياطية")
        uploaded_file = st.file_uploader("اختر ملف النسخة الاحتياطية", type=['xlsx', 'json'])
        if uploaded_file is not None:
            st.success("تم استلام الملف وجاري معالجته...")

elif choice == "👥 المستخدمين":
    if not check_perm(): st.error("غير مصرح"); st.stop()
    st.header("👥 إدارة المستخدمين والصلاحيات")
    
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, username, full_name, role, is_active FROM users")
            users_list = c.fetchall()
    
    st.dataframe(pd.DataFrame(users_list), use_container_width=True)

elif choice == "📦 إدارة الأصناف":
    st.header("📦 إدارة الأصناف")
    st.info("صفحة إضافة وتعديل بيانات الأصناف والمخزون.")

elif choice == "🏨 الفنادق":
    st.header("🏨 إدارة الفنادق")
    st.info("صفحة إدارة الفنادق المسجلة.")

elif choice == "🏢 الموردين":
    st.header("🏢 إدارة الموردين")
    st.info("صفحة إضافة وشاشة متابعة الموردين.")

elif choice == "📥 الوارد":
    st.header("📥 إذونات الوارد")
    st.info("صفحة تسجيل الواردات للمخزن وتحديث الأرصدة.")

elif choice == "📤 الصادر":
    st.header("📤 إذونات الصادر")
    st.info("صفحة إخراج الشحنات وتوليد إذونات الصرف.")

elif choice == "📝 الجرد":
    st.header("📝 تسوية الجرد")
    st.info("صفحة تسجيل الجرد الفعلي ومقارنته بالمخزون.")

elif choice == "📈 التقارير":
    st.header("📈 التقارير الشاملة")
    st.info("صفحة تصدير تقارير Excel و PDF.")