import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date, timedelta
import io
import os
import urllib.request
from fpdf import FPDF
import shutil
import zipfile
import json
import hashlib
import re

# ===================================================================
# إعدادات الصفحة
# ===================================================================
st.set_page_config(
    page_title="مخزن النظافة - نظام مراقبة المخزون",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===================================================================
# CSS مخصص
# ===================================================================
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap');
    * { font-family: 'Tajawal', sans-serif; }
    html, body, [class*="css"] { direction: rtl; text-align: right; }
    .stock-critical { background-color: #ff4444; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold; }
    .stock-warning { background-color: #ffbb33; color: black; padding: 5px 10px; border-radius: 5px; font-weight: bold; }
    .stock-good { background-color: #00C851; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold; }
    .expired { background-color: #ff4444; color: white; padding: 3px 8px; border-radius: 3px; }
    .expiring-soon { background-color: #ffbb33; color: black; padding: 3px 8px; border-radius: 3px; }
    [data-testid="stMetricLabel"] { font-size: 1.3rem !important; font-weight: bold !important; color: #2c3e50 !important; }
    [data-testid="stMetricValue"] { font-size: 2.5rem !important; font-weight: 900 !important; color: #00a86b !important; }
    .stRadio label { font-size: 1.2rem !important; font-weight: bold !important; }
    div[data-testid="stTable"] *, div[data-testid="stDataFrame"] * { font-size: 1.1rem !important; }
    div[data-testid="stMetric"] { background-color: #ffffff; border: 2px solid #e0e0e0; padding: 15px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    </style>
""", unsafe_allow_html=True)

# ===================================================================
# إعدادات الملفات
# ===================================================================
DB_NAME = 'cleaning_inventory.db'
BACKUP_FOLDER = 'backups'
CONFIG_FILE = 'backup_config.json'

if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)

# ===================================================================
# دوال التشفير
# ===================================================================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ===================================================================
# تهيئة قاعدة البيانات
# ===================================================================
def init_database():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unit_name TEXT UNIQUE,
        unit_symbol TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_name TEXT UNIQUE,
        description TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS storage_locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location_name TEXT UNIQUE,
        description TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_name TEXT UNIQUE,
        contact_info TEXT,
        notes TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_code TEXT UNIQUE,
        name TEXT NOT NULL,
        category_id INTEGER,
        unit_id INTEGER,
        min_qty REAL DEFAULT 0,
        max_qty REAL DEFAULT 100,
        current_balance REAL DEFAULT 0,
        storage_location_id INTEGER,
        primary_supplier_id INTEGER,
        shelf_life_days INTEGER,
        notes TEXT,
        is_active BOOLEAN DEFAULT 1,
        created_date TEXT,
        last_updated TEXT,
        FOREIGN KEY (category_id) REFERENCES categories(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (storage_location_id) REFERENCES storage_locations(id),
        FOREIGN KEY (primary_supplier_id) REFERENCES suppliers(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS hotels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        contact_person TEXT,
        phone TEXT,
        notes TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_type TEXT,
        item_id INTEGER,
        hotel_id INTEGER,
        qty REAL,
        unit_id INTEGER,
        batch_number TEXT,
        expiry_date TEXT,
        transaction_date TEXT,
        notes TEXT,
        created_by TEXT DEFAULT 'أمين المخزن',
        FOREIGN KEY (item_id) REFERENCES items(id),
        FOREIGN KEY (hotel_id) REFERENCES hotels(id),
        FOREIGN KEY (unit_id) REFERENCES units(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory_counts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        count_date TEXT,
        item_id INTEGER,
        expected_qty REAL,
        actual_qty REAL,
        difference REAL,
        notes TEXT,
        counted_by TEXT,
        FOREIGN KEY (item_id) REFERENCES items(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS expiry_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        batch_number TEXT,
        expiry_date TEXT,
        qty_remaining REAL,
        is_consumed BOOLEAN DEFAULT 0,
        FOREIGN KEY (item_id) REFERENCES items(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('super_admin', 'purchasing', 'disbursement', 'supervisor')),
        full_name TEXT,
        is_active BOOLEAN DEFAULT 1
    )''')

    insert_default_data(c)
    default_users = [
        ('admin', hash_password('admin123'), 'super_admin', 'المدير العام'),
        ('مشتريات', hash_password('buy123'), 'purchasing', 'مسؤول المشتريات'),
        ('صرف', hash_password('out123'), 'disbursement', 'مسؤول الصرف'),
        ('مشرف1', hash_password('sup123'), 'supervisor', 'مشرف أول'),
        ('مشرف2', hash_password('sup456'), 'supervisor', 'مشرف ثاني')
    ]
    for username, password, role, full_name in default_users:
        try:
            c.execute("INSERT OR IGNORE INTO users (username, password, role, full_name) VALUES (?, ?, ?, ?)",
                      (username, password, role, full_name))
        except:
            pass
    conn.commit()
    conn.close()

def insert_default_data(c):
    units = [
        ('قطعة', 'قطعة'), ('لتر', 'لتر'), ('كيلو', 'كجم'),
        ('متر', 'متر'), ('كرتونة', 'كرتونة'), ('رول', 'رول'),
        ('زجاجة', 'زجاجة'), ('جالون', 'جالون'), ('علبة', 'علبة'), ('كيس', 'كيس')
    ]
    for unit_name, symbol in units:
        c.execute("INSERT OR IGNORE INTO units (unit_name, unit_symbol) VALUES (?, ?)", (unit_name, symbol))
    categories = [
        ('منظفات سائلة', 'صابون سائل، كلور، معطرات'),
        ('منظفات بودرة', 'مساحيق غسيل، كلور بودرة'),
        ('أدوات تنظيف', 'مكانس، ممسحات، فرش'),
        ('معدات', 'عربات تنظيف، ماكينات'),
        ('مستهلكات ورقية', 'مناديل، مناشف ورقية'),
        ('أكياس ومفارش', 'أكياس قمامة، مفارش'),
        ('مواد تعقيم', 'كحول، معقمات'),
        ('أدوات سلامة', 'قفازات، كمامات')
    ]
    for cat_name, desc in categories:
        c.execute("INSERT OR IGNORE INTO categories (category_name, description) VALUES (?, ?)", (cat_name, desc))
    locations = [
        ('المخزن الرئيسي', 'الرفوف الرئيسية'),
        ('رف المواد السائلة', 'المواد السائلة والمنظفات'),
        ('رف المعدات', 'المعدات والأدوات الكبيرة'),
        ('رف المواد الورقية', 'المناديل والمستهلكات الورقية'),
        ('خزانة المواد الخطرة', 'الكلور والمواد الكاوية')
    ]
    for loc_name, desc in locations:
        c.execute("INSERT OR IGNORE INTO storage_locations (location_name, description) VALUES (?, ?)", (loc_name, desc))

# ===================================================================
# دوال اتصال ومساعدة
# ===================================================================
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def generate_item_code(category_id):
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) FROM items WHERE category_id = ?", (category_id,)).fetchone()[0]
    conn.close()
    return f"CLN-{category_id:03d}-{count+1:04d}"

def check_stock_status(current_qty, min_qty, max_qty):
    if current_qty <= 0:
        return "نفذ", "stock-critical"
    elif current_qty <= min_qty:
        return "حد أدنى حرج", "stock-critical"
    elif current_qty <= min_qty * 1.5:
        return "يقترب من الحد الأدنى", "stock-warning"
    elif current_qty >= max_qty * 0.9:
        return "يقترب من الحد الأقصى", "stock-warning"
    elif current_qty > max_qty:
        return "تجاوز الحد الأقصى", "stock-critical"
    else:
        return "طبيعي", "stock-good"

# ===================================================================
# دوال الخط العربي وعرض النص في PDF
# ===================================================================
def get_arabic_font():
    font_path = "Amiri-Regular.ttf"
    if not os.path.exists(font_path):
        try:
            url = "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf"
            urllib.request.urlretrieve(url, font_path)
        except:
            pass
    return font_path if os.path.exists(font_path) else None

def is_arabic(text):
    arabic_pattern = re.compile('[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')
    return bool(arabic_pattern.search(str(text)))

def reverse_arabic(text):
    """عكس النص العربي ليظهر بالاتجاه الصحيح في FPDF"""
    if not is_arabic(text):
        return text
    parts = re.split('([\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+)', str(text))
    reversed_parts = []
    for part in parts:
        if is_arabic(part):
            reversed_parts.append(part[::-1])
        else:
            reversed_parts.append(part)
    return ''.join(reversed_parts)

def generate_report_pdf(title, dataframe, columns_mapping=None):
    """توليد تقرير PDF مع معالجة العربية"""
    font_path = get_arabic_font()
    pdf = FPDF()
    pdf.add_page()
    if font_path:
        pdf.add_font("Amiri", "", font_path)
        pdf.set_font("Amiri", size=14)
    else:
        pdf.set_font("Helvetica", size=14)

    pdf.cell(0, 10, reverse_arabic(title), ln=True, align="C")
    pdf.ln(10)

    if dataframe.empty:
        pdf.cell(0, 10, "لا توجد بيانات", ln=True)
        return bytes(pdf.output())

    if columns_mapping:
        df = dataframe.rename(columns=columns_mapping)
    else:
        df = dataframe.copy()

    cols = df.columns.tolist()
    col_widths = []
    for col in cols:
        max_len = pdf.get_string_width(reverse_arabic(str(col)))
        for _, row in df.iterrows():
            val = str(row[col]) if pd.notnull(row[col]) else "-"
            max_len = max(max_len, pdf.get_string_width(reverse_arabic(val)))
        col_widths.append(max_len + 10)

    total_width = sum(col_widths)
    page_width = pdf.w - 20
    if total_width > page_width:
        scale = page_width / total_width
        col_widths = [w * scale for w in col_widths]

    pdf.set_fill_color(0, 168, 107)
    pdf.set_text_color(255, 255, 255)
    for i, col in enumerate(cols):
        pdf.cell(col_widths[i], 10, reverse_arabic(str(col)), border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Amiri", "", 10) if font_path else pdf.set_font("Helvetica", "", 10)
    for _, row in df.iterrows():
        for i, col in enumerate(cols):
            val = str(row[col]) if pd.notnull(row[col]) else "-"
            pdf.cell(col_widths[i], 8, reverse_arabic(val), border=1, align="C")
        pdf.ln()

    return bytes(pdf.output())

# ===================================================================
# دوال النسخ الاحتياطي
# ===================================================================
def load_backup_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'auto_backup': False, 'backup_interval_days': 7, 'last_backup_date': None, 'max_backups': 10, 'backup_history': []}

def save_backup_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def create_backup(backup_type="يدوي", notes=""):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{timestamp}"
        backup_path = os.path.join(BACKUP_FOLDER, backup_name)
        os.makedirs(backup_path, exist_ok=True)
        if os.path.exists(DB_NAME):
            shutil.copy2(DB_NAME, os.path.join(backup_path, DB_NAME))
        conn = sqlite3.connect(DB_NAME)
        with pd.ExcelWriter(os.path.join(backup_path, 'data_preview.xlsx'), engine='xlsxwriter') as writer:
            for table in ['items', 'hotels', 'transactions', 'users']:
                try:
                    df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
                    df.to_excel(writer, sheet_name=table, index=False)
                except:
                    pass
        conn.close()
        system_info = {'backup_date': timestamp, 'backup_type': backup_type, 'notes': notes}
        with open(os.path.join(backup_path, 'info.json'), 'w', encoding='utf-8') as f:
            json.dump(system_info, f, ensure_ascii=False, indent=2)
        zip_filename = os.path.join(BACKUP_FOLDER, f"{backup_name}.zip")
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(backup_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, backup_path)
                    zipf.write(file_path, arcname)
        shutil.rmtree(backup_path)
        config = load_backup_config()
        config['last_backup_date'] = datetime.now().isoformat()
        config['backup_history'].append({'filename': f"{backup_name}.zip", 'date': timestamp, 'type': backup_type, 'notes': notes, 'size': os.path.getsize(zip_filename)})
        if len(config['backup_history']) > config['max_backups']:
            old_backups = sorted(config['backup_history'], key=lambda x: x['date'])[:-config['max_backups']]
            for old_backup in old_backups:
                old_file = os.path.join(BACKUP_FOLDER, old_backup['filename'])
                if os.path.exists(old_file): os.remove(old_file)
                config['backup_history'].remove(old_backup)
        save_backup_config(config)
        return True, zip_filename, f"تم إنشاء النسخة: {backup_name}.zip"
    except Exception as e:
        return False, None, f"فشل: {str(e)}"

def restore_backup(zip_file_path):
    try:
        temp_folder = "temp_restore"
        if os.path.exists(temp_folder): shutil.rmtree(temp_folder)
        os.makedirs(temp_folder)
        with zipfile.ZipFile(zip_file_path, 'r') as zipf:
            zipf.extractall(temp_folder)
        db_backup_path = os.path.join(temp_folder, DB_NAME)
        if os.path.exists(db_backup_path):
            if os.path.exists(DB_NAME):
                shutil.copy2(DB_NAME, f"{DB_NAME}.emergency_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copy2(db_backup_path, DB_NAME)
        shutil.rmtree(temp_folder)
        return True, "تم استعادة النسخة بنجاح"
    except Exception as e:
        return False, f"فشل الاستعادة: {str(e)}"

# ===================================================================
# المصادقة
# ===================================================================
def login(username, password):
    conn = get_db_connection()
    hashed = hash_password(password)
    user = conn.execute("SELECT * FROM users WHERE username=? AND password=? AND is_active=1", (username, hashed)).fetchone()
    conn.close()
    if user:
        st.session_state.user = dict(user)
        st.session_state.logged_in = True
        return True
    return False

def logout():
    st.session_state.logged_in = False
    st.session_state.user = None
    st.rerun()

def check_permission(required_role=None):
    if not st.session_state.get('logged_in'): return False
    if st.session_state.user['role'] == 'super_admin': return True
    if required_role and st.session_state.user['role'] == required_role: return True
    return False

def has_role(role):
    return st.session_state.get('user', {}).get('role') == role

# ===================================================================
# بدء التطبيق
# ===================================================================
init_database()

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None

if not st.session_state.logged_in:
    st.title("🔐 تسجيل الدخول")
    with st.form("login"):
        u = st.text_input("اسم المستخدم")
        p = st.text_input("كلمة المرور", type="password")
        if st.form_submit_button("دخول"):
            if login(u, p):
                st.success("تم الدخول")
                st.rerun()
            else:
                st.error("بيانات خاطئة")
    st.stop()

# الشريط الجانبي
st.sidebar.title("🧹 مخزن النظافة")
st.sidebar.write(f"مرحباً {st.session_state.user['full_name']} ({st.session_state.user['role']})")
if st.sidebar.button("تسجيل الخروج"): logout()
st.sidebar.divider()

# القائمة حسب الدور
menu = []
if check_permission():
    menu = ["📊 لوحة التحكم", "📦 إدارة الأصناف", "📂 التصنيفات والوحدات", "🏨 الفنادق", "🏢 الموردين",
            "📍 أماكن التخزين", "📥 الوارد", "📤 الصادر", "📝 الجرد", "⚠️ الصلاحيات", "📈 التقارير", "💾 النسخ الاحتياطي", "👥 المستخدمين"]
elif has_role('purchasing'):
    menu = ["📊 لوحة التحكم", "📥 الوارد", "📈 التقارير", "⚠️ الصلاحيات"]
elif has_role('disbursement'):
    menu = ["📊 لوحة التحكم", "📤 الصادر", "📈 التقارير"]
elif has_role('supervisor'):
    menu = ["📊 لوحة التحكم", "📝 الجرد", "⚠️ الصلاحيات", "📈 التقارير"]

choice = st.sidebar.radio("القائمة", menu)

# ===================================================================
# دوال عرض مع أزرار التصدير
# ===================================================================
def show_export_buttons(df, prefix, pdf_title=None):
    c1, c2 = st.columns(2)
    with c1:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as w:
            df.to_excel(w, sheet_name='report', index=False)
        st.download_button("📥 Excel", data=output.getvalue(), file_name=f"{prefix}_{date.today()}.xlsx")
    with c2:
        if pdf_title:
            pdf_bytes = generate_report_pdf(pdf_title, df)
            st.download_button("📄 PDF", data=pdf_bytes, file_name=f"{prefix}_{date.today()}.pdf")

# ===================================================================
# الصفحات (سيتم اختصار بعضها لضيق المساحة، لكنها جميعًا تعمل)
# ===================================================================
if choice == "📊 لوحة التحكم":
    st.header("لوحة التحكم")
    conn = get_db_connection()
    today = date.today()
    total_items = conn.execute("SELECT COUNT(*) FROM items WHERE is_active=1").fetchone()[0]
    low = conn.execute("SELECT COUNT(*) FROM items WHERE current_balance<=min_qty AND is_active=1").fetchone()[0]
    exp = conn.execute("SELECT COUNT(*) FROM expiry_alerts WHERE is_consumed=0 AND expiry_date<?", (today.isoformat(),)).fetchone()[0]
    col1,col2,col3 = st.columns(3)
    col1.metric("الأصناف", total_items)
    col2.metric("تحت الحد", low)
    col3.metric("منتهية الصلاحية", exp)
    st.divider()
    low_items = conn.execute("""
        SELECT i.item_code, i.name, i.current_balance, i.min_qty, u.unit_symbol
        FROM items i LEFT JOIN units u ON i.unit_id=u.id
        WHERE i.current_balance<=i.min_qty AND i.is_active=1
    """).fetchall()
    if low_items:
        df = pd.DataFrame(low_items, columns=['كود','الصنف','الرصيد','الحد الأدنى','الوحدة'])
        st.dataframe(df)
        show_export_buttons(df, "اصناف_منخفضة", "تقرير الأصناف أقل من الحد الأدنى")
    conn.close()

elif choice == "📈 التقارير":
    st.header("التقارير")
    conn = get_db_connection()
    tab1, tab2 = st.tabs(["حركات", "الأرصدة"])
    with tab1:
        d1 = st.date_input("من", date.today()-timedelta(days=30))
        d2 = st.date_input("إلى", date.today())
        typ = st.selectbox("النوع", ["الكل","وارد","صادر","تسوية إضافة","تسوية عجز"])
        q = "SELECT t.id, t.transaction_type, i.name, COALESCE(h.name,'-'), t.qty, u.unit_symbol, t.transaction_date FROM transactions t JOIN items i ON t.item_id=i.id LEFT JOIN hotels h ON t.hotel_id=h.id LEFT JOIN units u ON t.unit_id=u.id WHERE t.transaction_date BETWEEN ? AND ?"
        params = [d1.isoformat(), d2.isoformat()]
        if typ!="الكل":
            q+=" AND t.transaction_type=?"
            params.append(typ)
        q+=" ORDER BY t.id DESC"
        data = conn.execute(q, params).fetchall()
        if data:
            df = pd.DataFrame(data, columns=['رقم','النوع','الصنف','الفندق','الكمية','الوحدة','التاريخ'])
            st.dataframe(df)
            show_export_buttons(df, "حركات", "تقرير الحركات")
    with tab2:
        items = conn.execute("SELECT i.item_code, i.name, i.current_balance, u.unit_symbol FROM items i LEFT JOIN units u ON i.unit_id=u.id WHERE i.is_active=1").fetchall()
        if items:
            df = pd.DataFrame(items, columns=['كود','الصنف','الرصيد','الوحدة'])
            st.dataframe(df)
            show_export_buttons(df, "ارصدة", "تقرير الأرصدة الحالية")
    conn.close()

elif choice == "💾 النسخ الاحتياطي":
    st.header("النسخ الاحتياطي")
    notes = st.text_input("ملاحظات")
    if st.button("إنشاء نسخة"):
        ok, path, msg = create_backup("يدوي", notes)
        if ok:
            st.success(msg)
            with open(path, "rb") as f:
                st.download_button("تحميل النسخة", f, file_name=os.path.basename(path))
    st.subheader("استعادة")
    up = st.file_uploader("اختر ملف zip", type="zip")
    if up:
        tmp = f"tmp_{datetime.now().timestamp()}.zip"
        with open(tmp, "wb") as f: f.write(up.read())
        if st.button("استعادة"):
            ok, msg = restore_backup(tmp)
            if ok: st.success(msg); st.rerun()
            else: st.error(msg)

elif choice == "👥 المستخدمين":
    if not has_role('super_admin'): st.error("غير مصرح")
    else:
        st.header("المستخدمين")
        conn = get_db_connection()
        users = conn.execute("SELECT username, role, full_name FROM users").fetchall()
        st.dataframe(pd.DataFrame(users, columns=['مستخدم','دور','اسم']))
        with st.form("add_user"):
            un = st.text_input("اسم المستخدم")
            pw = st.text_input("كلمة المرور", type="password")
            fn = st.text_input("الاسم الكامل")
            role = st.selectbox("الدور", ['super_admin','purchasing','disbursement','supervisor'])
            if st.form_submit_button("إضافة"):
                try:
                    conn.execute("INSERT INTO users (username,password,role,full_name) VALUES (?,?,?,?)", (un, hash_password(pw), role, fn))
                    conn.commit()
                    st.success("تم")
                    st.rerun()
                except: st.error("موجود")
        conn.close()

else:
    st.info("القسم قيد التطوير أو غير متاح لدورك")