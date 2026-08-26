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

# ======================== التشفير وقواعد البيانات ========================
def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

@st.cache_resource
def init_db():
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS units (id SERIAL PRIMARY KEY, unit_name TEXT UNIQUE, unit_symbol TEXT);''')
            c.execute('''CREATE TABLE IF NOT EXISTS suppliers (id SERIAL PRIMARY KEY, supplier_name TEXT UNIQUE, contact_info TEXT, notes TEXT);''')
            c.execute('''CREATE TABLE IF NOT EXISTS hotels (id SERIAL PRIMARY KEY, name TEXT UNIQUE, contact_person TEXT, phone TEXT, notes TEXT);''')
            
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
            );''')

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
            );''')

            c.execute('''CREATE TABLE IF NOT EXISTS inventory_counts (
                id SERIAL PRIMARY KEY,
                count_date TEXT,
                item_id INTEGER REFERENCES items(id),
                expected_qty REAL,
                actual_qty REAL,
                difference REAL,
                notes TEXT,
                counted_by TEXT
            );''')

            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                full_name TEXT,
                is_active BOOLEAN DEFAULT TRUE
            );''')

            # --- التحديث التلقائي لكافة الهياكل والأعمدة في السحابة ---
            alter_queries = [
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS transaction_type TEXT;",
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS item_id INTEGER;",
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS hotel_id INTEGER;",
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS qty REAL;",
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS transaction_date TEXT;",
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS notes TEXT;",
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS supplier_name TEXT;",
                "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS created_by TEXT DEFAULT 'أمين المخزن';",
                "ALTER TABLE items ADD COLUMN IF NOT EXISTS item_code TEXT;",
                "ALTER TABLE items ADD COLUMN IF NOT EXISTS min_qty REAL DEFAULT 0;",
                "ALTER TABLE items ADD COLUMN IF NOT EXISTS max_qty REAL DEFAULT 100;",
                "ALTER TABLE items ADD COLUMN IF NOT EXISTS current_balance REAL DEFAULT 0;",
                "ALTER TABLE items ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"
            ]
            for q in alter_queries:
                c.execute(q)

            # البيانات الافتراضية للوحدات والمستخدمين
            for u_name, u_sym in [('قطعة','قطعة'),('لتر','لتر'),('كيلو','كجم'),('متر','متر'),('كرتونة','كرتونة')]:
                c.execute("INSERT INTO units (unit_name, unit_symbol) VALUES (%s,%s) ON CONFLICT (unit_name) DO NOTHING;", (u_name, u_sym))

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

# ======================== تسجيل الدخول ========================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None

if not st.session_state.logged_in:
    st.title("🔐 تسجيل الدخول - نظام إدارة المخزن")
    with st.form("login"):
        uname = st.text_input("اسم المستخدم")
        pwd = st.text_input("كلمة المرور", type="password")
        if st.form_submit_button("🔑 دخول"):
            if login(uname, pwd):
                st.success("تم الدخول بنجاح"); st.rerun()
            else: st.error("اسم المستخدم أو كلمة المرور غير صحيحة.")
    st.stop()

# ======================== القائمة الجانبية ========================
st.sidebar.title("🧹 مخزن النظافة")
st.sidebar.write(f"👤 **{st.session_state.user['full_name']}**")
if st.sidebar.button("🚪 تسجيل الخروج"): logout()

menu = ["📊 لوحة التحكم", "📦 الأصناف", "🏨 الفنادق", "🏢 الموردين", "📥 إذن وارد", "📤 إذن صادر", "📝 الجرد", "📈 التقارير", "💾 النسخ الاحتياطي", "👥 المستخدمين"]
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
            c.execute("SELECT COUNT(*) FROM hotels")
            hotels_cnt = c.fetchone()['count']
            c.execute("SELECT COUNT(*) FROM suppliers")
            sup_cnt = c.fetchone()['count']
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("📦 إجمالي الأصناف", total)
            c2.metric("⚠️ أصناف تحت الحد الأدنى", low)
            c3.metric("🏨 عدد الفنادق", hotels_cnt)
            c4.metric("🏢 عدد الموردين", sup_cnt)

elif choice == "📦 الأصناف":
    st.header("📦 إدارة الأصناف")
    with st.expander("➕ إضافة صنف جديد"):
        with st.form("add_item"):
            name = st.text_input("اسم الصنف")
            code = st.text_input("كود الصنف")
            min_q = st.number_input("الحد الأدنى", min_value=0.0, value=5.0)
            max_q = st.number_input("الحد الأقصى", min_value=0.0, value=100.0)
            bal = st.number_input("الرصيد الافتتاحي", min_value=0.0, value=0.0)
            if st.form_submit_button("حفظ"):
                if name:
                    with get_db() as conn:
                        with conn.cursor() as c:
                            c.execute("INSERT INTO items (name, item_code, min_qty, max_qty, current_balance) VALUES (%s,%s,%s,%s,%s)",
                                      (name, code if code else None, min_q, max_q, bal))
                    st.success("تم إضافة الصنف بنجاح!")
                    st.rerun()
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, item_code as الكود, name as الاسم, current_balance as الرصيد, min_qty as الحد_الأدنى FROM items ORDER BY id DESC")
            items = c.fetchall()
    st.dataframe(pd.DataFrame(items), use_container_width=True)

elif choice == "🏨 الفنادق":
    st.header("🏨 إدارة الفنادق")
    with st.expander("➕ إضافة فندق جديد"):
        with st.form("add_hotel"):
            h_name = st.text_input("اسم الفندق")
            person = st.text_input("الشخص المسؤول")
            phone = st.text_input("رقم الهاتف")
            if st.form_submit_button("حفظ الفندق"):
                if h_name:
                    with get_db() as conn:
                        with conn.cursor() as c:
                            c.execute("INSERT INTO hotels (name, contact_person, phone) VALUES (%s,%s,%s)", (h_name, person, phone))
                    st.success("تمت إضافة الفندق بنجاح")
                    st.rerun()
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, name as الفندق, contact_person as المسؤول, phone as الهاتف FROM hotels")
            st.dataframe(pd.DataFrame(c.fetchall()), use_container_width=True)

elif choice == "🏢 الموردين":
    st.header("🏢 إدارة الموردين")
    with st.expander("➕ إضافة مورد جديد"):
        with st.form("add_sup"):
            s_name = st.text_input("اسم المورد")
            info = st.text_input("بيانات الاتصال")
            if st.form_submit_button("حفظ المورد"):
                if s_name:
                    with get_db() as conn:
                        with conn.cursor() as c:
                            c.execute("INSERT INTO suppliers (supplier_name, contact_info) VALUES (%s,%s)", (s_name, info))
                    st.success("تمت إضافة المورد بنجاح")
                    st.rerun()
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, supplier_name as المورد, contact_info as التواصل FROM suppliers")
            st.dataframe(pd.DataFrame(c.fetchall()), use_container_width=True)

elif choice == "📥 إذن وارد":
    st.header("📥 تسجيل إذن وارد")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, name FROM items ORDER BY name")
            items_list = c.fetchall()
            c.execute("SELECT supplier_name FROM suppliers ORDER BY supplier_name")
            sups_list = [s['supplier_name'] for s in c.fetchall()]
    
    if items_list:
        item_dict = {item['name']: item['id'] for item in items_list}
        with st.form("in_form"):
            selected_item = st.selectbox("الصنف", list(item_dict.keys()))
            qty = st.number_input("الكمية الواردة", min_value=0.1, value=1.0)
            supplier = st.selectbox("المورد", sups_list) if sups_list else st.text_input("اسم المورد")
            notes = st.text_input("ملاحظات")
            if st.form_submit_button("حفظ الوارد"):
                item_id = item_dict[selected_item]
                with get_db() as conn:
                    with conn.cursor() as c:
                        c.execute("INSERT INTO transactions (transaction_type, item_id, qty, transaction_date, notes, supplier_name) VALUES ('IN', %s, %s, %s, %s, %s)",
                                  (item_id, qty, date.today().isoformat(), notes, supplier))
                        c.execute("UPDATE items SET current_balance = current_balance + %s WHERE id = %s", (qty, item_id))
                st.success("تم تسجيل الوارد وتحديث الرصيد!")

elif choice == "📤 إذن صادر":
    st.header("📤 تسجيل إذن صادر (صرف)")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, name, current_balance FROM items ORDER BY name")
            items_list = c.fetchall()
            c.execute("SELECT id, name FROM hotels ORDER BY name")
            hotels_list = c.fetchall()
            
    if items_list:
        item_dict = {f"{item['name']} (الرصيد: {item['current_balance']})": item for item in items_list}
        hotel_dict = {h['name']: h['id'] for h in hotels_list} if hotels_list else {}
        
        with st.form("out_form"):
            selected_label = st.selectbox("اختر الصنف", list(item_dict.keys()))
            selected_item = item_dict[selected_label]
            hotel_name = st.selectbox("الفندق المستلم", list(hotel_dict.keys())) if hotel_dict else None
            qty = st.number_input("الكمية المصروفة", min_value=0.1, value=1.0)
            notes = st.text_input("ملاحظات / اسم المستلم")
            if st.form_submit_button("تسجيل الصرف"):
                if qty > selected_item['current_balance']:
                    st.error("الكمية المطلوبة تتجاوز الرصيد الحالي!")
                else:
                    h_id = hotel_dict[hotel_name] if hotel_name else None
                    with get_db() as conn:
                        with conn.cursor() as c:
                            c.execute("INSERT INTO transactions (transaction_type, item_id, hotel_id, qty, transaction_date, notes) VALUES ('OUT', %s, %s, %s, %s, %s)",
                                      (selected_item['id'], h_id, qty, date.today().isoformat(), notes))
                            c.execute("UPDATE items SET current_balance = current_balance - %s WHERE id = %s", (qty, selected_item['id']))
                    st.success("تم تسجيل الصرف بنجاح وتحديث الرصيد!")

elif choice == "📝 الجرد":
    st.header("📝 تسوية الجرد")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, name, current_balance FROM items ORDER BY name")
            items_list = c.fetchall()
    if items_list:
        item_dict = {i['name']: i for i in items_list}
        selected_i = st.selectbox("الصنف للجرد", list(item_dict.keys()))
        curr = item_dict[selected_i]
        st.info(f"الرصيد النظامي الحقيقي حالياً: **{curr['current_balance']}**")
        actual = st.number_input("الرصيد الفعلي في المخزن", min_value=0.0, value=float(curr['current_balance']))
        notes = st.text_input("سبب الفروقات (إن وجد)")
        if st.button("حفظ تسوية الجرد"):
            diff = actual - curr['current_balance']
            with get_db() as conn:
                with conn.cursor() as c:
                    c.execute("INSERT INTO inventory_counts (count_date, item_id, expected_qty, actual_qty, difference, notes, counted_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                              (date.today().isoformat(), curr['id'], curr['current_balance'], actual, diff, notes, st.session_state.user['full_name']))
                    c.execute("UPDATE items SET current_balance = %s WHERE id = %s", (actual, curr['id']))
            st.success("تم تحديث المخزون وحفظ حركة التسوية!")

elif choice == "📈 التقارير":
    st.header("📈 تقارير الحركة")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("""
                SELECT t.id, 
                       CASE WHEN t.transaction_type='IN' THEN 'وارد 📥' ELSE 'صادر 📤' END as نوع_الحركة, 
                       i.name as الصنف, 
                       t.qty as الكمية, 
                       h.name as الفندق,
                       t.supplier_name as المورد,
                       t.transaction_date as التاريخ, 
                       t.notes as ملاحظات
                FROM transactions t
                LEFT JOIN items i ON t.item_id = i.id
                LEFT JOIN hotels h ON t.hotel_id = h.id
                ORDER BY t.id DESC
            """)
            trans = c.fetchall()
    if trans:
        st.dataframe(pd.DataFrame(trans), use_container_width=True)
    else:
        st.info("لا توجد حركات مسجلة بعد.")

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

elif choice == "👥 المستخدمين":
    st.header("👥 إدارة المستخدمين")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, username as اسم_المستخدم, full_name as الاسم_الكامل, role as الدور FROM users")
            st.dataframe(pd.DataFrame(c.fetchall()), use_container_width=True)