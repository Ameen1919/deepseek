import streamlit as st
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
import pandas as pd
from datetime import datetime, date
import io
import os
import sqlite3
import zipfile
import json
import hashlib
import re
import urllib.request
from fpdf import FPDF
from contextlib import contextmanager

# ======================== إعدادات الصفحة ========================
st.set_page_config(page_title="مخزن النظافة", layout="wide", initial_sidebar_state="expanded")

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

# ======================== التشفير والدوال الأساسية ========================
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
                supplier_name TEXT,
                unit_price REAL DEFAULT 0
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                full_name TEXT,
                is_active BOOLEAN DEFAULT TRUE
            )''')

            # إضافة القيم الافتراضية
            for u_name, u_sym in [('قطعة','قطعة'),('لتر','لتر'),('كيلو','كجم'),('متر','متر'),('كرتونة','كرتونة')]:
                c.execute("INSERT INTO units (unit_name, unit_symbol) VALUES (%s,%s) ON CONFLICT (unit_name) DO NOTHING",(u_name,u_sym))

            c.execute("""
                INSERT INTO users (username, password, role, full_name, is_active)
                VALUES ('admin', %s, 'super_admin', 'المدير العام', TRUE)
                ON CONFLICT (username) DO UPDATE SET password = EXCLUDED.password;
            """, (hash_password('admin123'),))
    return True

def login(username, password):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM users WHERE username=%s AND password=%s AND is_active=TRUE", (username, hash_password(password)))
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

init_db()

# ======================== إدارة تسجيل الدخول ========================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None

if not st.session_state.logged_in:
    st.title("🔐 تسجيل الدخول - مخزن النظافة")
    with st.form("login"):
        uname = st.text_input("اسم المستخدم")
        pwd = st.text_input("كلمة المرور", type="password")
        if st.form_submit_button("🔑 دخول"):
            if login(uname, pwd):
                st.success("تم الدخول بنجاح"); st.rerun()
            else: st.error("اسم المستخدم أو كلمة المرور غير صحيحة.")
    st.stop()

# ======================== القائمة الرئيسية ========================
st.sidebar.title("🧹 مخزن النظافة")
st.sidebar.write(f"👤 **{st.session_state.user['full_name']}**")
if st.sidebar.button("🚪 تسجيل الخروج"): logout()

menu = ["📊 لوحة التحكم", "📦 الأصناف", "📥 إذن وارد", "📤 إذن صادر", "🏨 الفنادق", "🏢 الموردين", "📈 التقارير", "💾 النسخ الاحتياطي"]
choice = st.sidebar.radio("التنقل بين الصفحات", menu)

# ======================== الصفحات ========================
if choice == "📊 لوحة التحكم":
    st.header("📊 لوحة التحكم والمؤشرات")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT COUNT(*) FROM items WHERE is_active=TRUE")
            total = c.fetchone()['count']
            c.execute("SELECT COUNT(*) FROM items WHERE current_balance<=min_qty AND is_active=TRUE")
            low = c.fetchone()['count']
            c1, c2 = st.columns(2)
            c1.metric("📦 إجمالي الأصناف", total)
            c2.metric("⚠️ أصناف تحت الحد الأدنى", low)

elif choice == "📦 الأصناف":
    st.header("📦 إدارة الأصناف والمخزون")
    
    with st.expander("➕ إضافة صنف جديد"):
        with st.form("add_item"):
            name = st.text_input("اسم الصنف")
            code = st.text_input("كود الصنف (اختياري)")
            min_q = st.number_input("الحد الأدنى", min_value=0.0, value=5.0)
            max_q = st.number_input("الحد الأقصى", min_value=0.0, value=100.0)
            bal = st.number_input("الرصيد الافتتاحي الحالي", min_value=0.0, value=0.0)
            if st.form_submit_button("حفظ الصنف"):
                if name:
                    try:
                        with get_db() as conn:
                            with conn.cursor() as c:
                                c.execute("INSERT INTO items (name, item_code, min_qty, max_qty, current_balance) VALUES (%s,%s,%s,%s,%s)",
                                          (name, code if code else None, min_q, max_q, bal))
                        st.success("تم إضافة الصنف بنجاح!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"خطأ: قد يكون اسم الصنف مكرر. التفاصيل: {e}")
                else: st.warning("يرجى كتابة اسم الصنف.")

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, item_code, name, current_balance, min_qty, max_qty FROM items ORDER BY id DESC")
            items = c.fetchall()
    st.dataframe(pd.DataFrame(items), use_container_width=True)

elif choice == "📥 إذن وارد":
    st.header("📥 تسجيل إذن وارد جديد")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, name FROM items ORDER BY name")
            items_list = c.fetchall()
    
    if not items_list:
        st.warning("لا توجد أصناف مسجلة بالمخزن، يرجى إضافة أصناف أولاً من صفحة الأصناف.")
    else:
        item_dict = {item['name']: item['id'] for item in items_list}
        with st.form("in_form"):
            selected_item = st.selectbox("الصنف", list(item_dict.keys()))
            qty = st.number_input("الكمية الواردة", min_value=0.1, value=1.0)
            supplier = st.text_input("اسم المورد (اختياري)")
            notes = st.text_input("ملاحظات")
            if st.form_submit_button("حفظ الوارد"):
                item_id = item_dict[selected_item]
                with get_db() as conn:
                    with conn.cursor() as c:
                        # إضافة حركة وارد
                        c.execute("""
                            INSERT INTO transactions (transaction_type, item_id, qty, transaction_date, notes, supplier_name)
                            VALUES ('IN', %s, %s, %s, %s, %s)
                        """, (item_id, qty, date.today().isoformat(), notes, supplier))
                        # تحديث الرصيد
                        c.execute("UPDATE items SET current_balance = current_balance + %s WHERE id = %s", (qty, item_id))
                st.success("تم تسجيل الوارد وتحديث الرصيد بنجاح!")

elif choice == "📤 إذن صادر":
    st.header("📤 تسجيل إذن صادر (صرف)")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, name, current_balance FROM items ORDER BY name")
            items_list = c.fetchall()
    
    if not items_list:
        st.warning("لا توجد أصناف مسجلة.")
    else:
        item_dict = {f"{item['name']} (الرصيد المتاح: {item['current_balance']})": item for item in items_list}
        with st.form("out_form"):
            selected_label = st.selectbox("اختر الصنف", list(item_dict.keys()))
            selected_item = item_dict[selected_label]
            qty = st.number_input("الكمية المصروفة", min_value=0.1, value=1.0)
            notes = st.text_input("جهة الصرف / ملاحظات")
            if st.form_submit_button("تسجيل الصرف"):
                if qty > selected_item['current_balance']:
                    st.error("الكمية المطلوبة أكبر من الرصيد المتاح بالمخزن!")
                else:
                    with get_db() as conn:
                        with conn.cursor() as c:
                            c.execute("""
                                INSERT INTO transactions (transaction_type, item_id, qty, transaction_date, notes)
                                VALUES ('OUT', %s, %s, %s, %s)
                            """, (selected_item['id'], qty, date.today().isoformat(), notes))
                            c.execute("UPDATE items SET current_balance = current_balance - %s WHERE id = %s", (qty, selected_item['id']))
                    st.success("تم تسجيل الصرف وتحديث الرصيد!")

elif choice == "📈 التقارير":
    st.header("📈 تقارير الحركة والمخزون")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("""
                SELECT t.id, t.transaction_type as النوع, i.name as الصنف, t.qty as الكمية, 
                       t.transaction_date as التاريخ, t.notes as ملاحظات
                FROM transactions t
                JOIN items i ON t.item_id = i.id
                ORDER BY t.id DESC
            """)
            trans = c.fetchall()
    df_trans = pd.DataFrame(trans)
    st.dataframe(df_trans, use_container_width=True)

elif choice == "💾 النسخ الاحتياطي":
    st.header("💾 استعادة وتصدير البيانات")
    uploaded_file = st.file_uploader("رفع ملف الاستعادة (.db أو .zip)", type=['db', 'zip'])
    if uploaded_file is not None:
        try:
            file_bytes = uploaded_file.read()
            temp_db_path = "temp_restore.db"
            if zipfile.is_zipfile(io.BytesIO(file_bytes)):
                with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as z:
                    for filename in z.namelist():
                        if filename.endswith('.db'):
                            with open(temp_db_path, "wb") as f: f.write(z.read(filename))
                            break
            else:
                with open(temp_db_path, "wb") as f: f.write(file_bytes)

            if os.path.exists(temp_db_path):
                sq_conn = sqlite3.connect(temp_db_path)
                sq_curr = sq_conn.cursor()
                sq_curr.execute("SELECT name, current_balance, min_qty, max_qty FROM items")
                imported_items = sq_curr.fetchall()
                
                with get_db() as pg_conn:
                    with pg_conn.cursor() as pg_c:
                        for item in imported_items:
                            pg_c.execute("""
                                INSERT INTO items (name, current_balance, min_qty, max_qty, is_active)
                                VALUES (%s, %s, %s, %s, TRUE)
                                ON CONFLICT (name) DO UPDATE 
                                SET current_balance = EXCLUDED.current_balance;
                            """, item)
                st.success(f"✅ تم نقل وتحديث {len(imported_items)} صنف بنجاح!")
                sq_conn.close()
                os.remove(temp_db_path)
        except Exception as e:
            st.error(f"خطأ أثناء استيراد البيانات: {e}")