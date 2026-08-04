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

# ===================================================================
# إعدادات الصفحة
# ===================================================================
st.set_page_config(
    page_title="مخزن النظافة - نظام مراقبة المخزون",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===================================================================
# CSS مخصص للواجهة العربية
# ===================================================================
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap');
    
    * {
        font-family: 'Tajawal', sans-serif;
    }
    
    html, body, [class*="css"] {
        direction: rtl;
        text-align: right;
    }
    
    .stock-critical {
        background-color: #ff4444;
        color: white;
        padding: 5px 10px;
        border-radius: 5px;
        font-weight: bold;
    }
    
    .stock-warning {
        background-color: #ffbb33;
        color: black;
        padding: 5px 10px;
        border-radius: 5px;
        font-weight: bold;
    }
    
    .stock-good {
        background-color: #00C851;
        color: white;
        padding: 5px 10px;
        border-radius: 5px;
        font-weight: bold;
    }
    
    .expired {
        background-color: #ff4444;
        color: white;
        padding: 3px 8px;
        border-radius: 3px;
        font-size: 0.9rem;
    }
    
    .expiring-soon {
        background-color: #ffbb33;
        color: black;
        padding: 3px 8px;
        border-radius: 3px;
        font-size: 0.9rem;
    }
    
    [data-testid="stMetricLabel"] {
        font-size: 1.3rem !important;
        font-weight: bold !important;
        color: #2c3e50 !important;
    }
    
    [data-testid="stMetricValue"] {
        font-size: 2.5rem !important;
        font-weight: 900 !important;
        color: #00a86b !important;
    }
    
    .stRadio label {
        font-size: 1.2rem !important;
        font-weight: bold !important;
    }
    
    div[data-testid="stTable"] *, div[data-testid="stDataFrame"] * {
        font-size: 1.1rem !important;
    }
    
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        border: 2px solid #e0e0e0;
        padding: 15px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }
    </style>
""", unsafe_allow_html=True)

# ===================================================================
# إعدادات قاعدة البيانات والمجلدات
# ===================================================================
DB_NAME = 'cleaning_inventory.db'
BACKUP_FOLDER = 'backups'
CONFIG_FILE = 'backup_config.json'

if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)

# ===================================================================
# دوال تهيئة قاعدة البيانات
# ===================================================================
def init_database():
    """تهيئة قاعدة البيانات بجميع الجداول المطلوبة"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # جدول وحدات القياس
    c.execute('''CREATE TABLE IF NOT EXISTS units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        unit_name TEXT UNIQUE,
        unit_symbol TEXT
    )''')
    
    # جدول التصنيفات
    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_name TEXT UNIQUE,
        description TEXT
    )''')
    
    # جدول أماكن التخزين
    c.execute('''CREATE TABLE IF NOT EXISTS storage_locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location_name TEXT UNIQUE,
        description TEXT
    )''')
    
    # جدول الموردين
    c.execute('''CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_name TEXT UNIQUE,
        contact_info TEXT,
        notes TEXT
    )''')
    
    # جدول الأصناف المطور
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
    
    # جدول الفنادق
    c.execute('''CREATE TABLE IF NOT EXISTS hotels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        contact_person TEXT,
        phone TEXT,
        notes TEXT
    )''')
    
    # جدول الحركات
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
    
    # جدول الجرد الدوري
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
    
    # جدول تنبيهات الصلاحية
    c.execute('''CREATE TABLE IF NOT EXISTS expiry_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        batch_number TEXT,
        expiry_date TEXT,
        qty_remaining REAL,
        is_consumed BOOLEAN DEFAULT 0,
        FOREIGN KEY (item_id) REFERENCES items(id)
    )''')
    
    # إدخال البيانات الأساسية
    insert_default_data(c)
    
    conn.commit()
    conn.close()

def insert_default_data(c):
    """إدخال البيانات الأساسية (الوحدات، التصنيفات، أماكن التخزين)"""
    
    # وحدات القياس
    units = [
        ('قطعة', 'قطعة'), ('لتر', 'لتر'), ('كيلو', 'كجم'),
        ('متر', 'متر'), ('كرتونة', 'كرتونة'), ('رول', 'رول'),
        ('زجاجة', 'زجاجة'), ('جالون', 'جالون'), ('علبة', 'علبة'),
        ('كيس', 'كيس')
    ]
    for unit_name, symbol in units:
        c.execute("INSERT OR IGNORE INTO units (unit_name, unit_symbol) VALUES (?, ?)", 
                 (unit_name, symbol))
    
    # تصنيفات الأصناف
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
        c.execute("INSERT OR IGNORE INTO categories (category_name, description) VALUES (?, ?)",
                 (cat_name, desc))
    
    # أماكن تخزين
    locations = [
        ('المخزن الرئيسي', 'الرفوف الرئيسية'),
        ('رف المواد السائلة', 'المواد السائلة والمنظفات'),
        ('رف المعدات', 'المعدات والأدوات الكبيرة'),
        ('رف المواد الورقية', 'المناديل والمستهلكات الورقية'),
        ('خزانة المواد الخطرة', 'الكلور والمواد الكاوية')
    ]
    for loc_name, desc in locations:
        c.execute("INSERT OR IGNORE INTO storage_locations (location_name, description) VALUES (?, ?)",
                 (loc_name, desc))

# ===================================================================
# دوال مساعدة
# ===================================================================
def get_db_connection():
    """الحصول على اتصال بقاعدة البيانات"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def generate_item_code(category_id):
    """توليد كود تلقائي للصنف"""
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) FROM items WHERE category_id = ?", (category_id,)).fetchone()[0]
    conn.close()
    return f"CLN-{category_id:03d}-{count+1:04d}"

def check_stock_status(current_qty, min_qty, max_qty):
    """تحديد حالة المخزون"""
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
# دوال PDF
# ===================================================================
def get_arabic_font():
    font_path = "Amiri-Regular.ttf"
    if not os.path.exists(font_path):
        try:
            url = "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf"
            urllib.request.urlretrieve(url, font_path)
        except:
            pass
    return font_path

def generate_pdf_voucher(order_id, doc_type, date_str, party_name, item_name, qty, unit, notes):
    font_path = get_arabic_font()
    pdf = FPDF()
    pdf.add_page()
    if os.path.exists(font_path):
        pdf.add_font("Amiri", "", font_path)
    
    title = "إذن صرف مخزني - مستلزمات نظافة" if doc_type == "صادر" else "إذن استلام مشتريات (وارد)"
    
    pdf.set_font("Amiri", size=18)
    pdf.cell(0, 10, txt=title, ln=True, align="C")
    pdf.set_font("Amiri", size=12)
    pdf.ln(10)
    
    pdf.cell(0, 8, txt=f"رقم الحركة: #{order_id}     التاريخ: {date_str}", ln=True, align="R")
    pdf.cell(0, 8, txt=f"{'الجهة المستلمة:' if doc_type == 'صادر' else 'المورد:'} {party_name}", ln=True, align="R")
    pdf.ln(10)
    
    if doc_type == "صادر":
        pdf.set_fill_color(0, 168, 107)
    else:
        pdf.set_fill_color(41, 128, 185)
    pdf.set_text_color(255, 255, 255)
    
    pdf.cell(30, 10, "الوحدة", border=1, align="C", fill=True)
    pdf.cell(30, 10, "الكمية", border=1, align="C", fill=True)
    pdf.cell(90, 10, "الصنف", border=1, align="C", fill=True)
    pdf.cell(20, 10, "م", border=1, align="C", fill=True)
    pdf.ln()
    
    pdf.set_text_color(0, 0, 0)
    pdf.cell(30, 10, unit if unit else "-", border=1, align="C")
    pdf.cell(30, 10, str(qty), border=1, align="C")
    pdf.cell(90, 10, item_name, border=1, align="C")
    pdf.cell(20, 10, "1", border=1, align="C")
    pdf.ln(25)
    
    pdf.cell(95, 8, f"{'توقيع المستلم:' if doc_type == 'صادر' else 'توقيع مسؤول الاستلام:'} ....................", align="R")
    pdf.cell(95, 8, "توقيع أمين المخزن: ....................", align="R")
    
    return bytes(pdf.output())

# ===================================================================
# دوال النسخ الاحتياطي
# ===================================================================
def load_backup_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        'auto_backup': False,
        'backup_interval_days': 7,
        'last_backup_date': None,
        'max_backups': 10,
        'backup_history': []
    }

def save_backup_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def create_backup(backup_type="يدوي", notes=""):
    """إنشاء نسخة احتياطية كاملة"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{timestamp}"
        backup_path = os.path.join(BACKUP_FOLDER, backup_name)
        os.makedirs(backup_path, exist_ok=True)
        
        # نسخ قاعدة البيانات
        if os.path.exists(DB_NAME):
            shutil.copy2(DB_NAME, os.path.join(backup_path, DB_NAME))
        
        # تصدير البيانات إلى Excel للمعاينة
        conn = sqlite3.connect(DB_NAME)
        with pd.ExcelWriter(os.path.join(backup_path, 'data_preview.xlsx'), engine='xlsxwriter') as writer:
            tables = ['items', 'hotels', 'transactions', 'categories', 'units', 'suppliers', 'storage_locations']
            for table_name in tables:
                try:
                    df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
                    df.to_excel(writer, sheet_name=table_name, index=False)
                except:
                    pass
        conn.close()
        
        # معلومات النسخة
        system_info = {
            'backup_date': timestamp,
            'backup_type': backup_type,
            'notes': notes,
            'database_name': DB_NAME
        }
        with open(os.path.join(backup_path, 'info.json'), 'w', encoding='utf-8') as f:
            json.dump(system_info, f, ensure_ascii=False, indent=2)
        
        # ضغط النسخة
        zip_filename = os.path.join(BACKUP_FOLDER, f"{backup_name}.zip")
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(backup_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, backup_path)
                    zipf.write(file_path, arcname)
        
        # تنظيف المجلد المؤقت
        shutil.rmtree(backup_path)
        
        # تحديث السجل
        config = load_backup_config()
        config['last_backup_date'] = datetime.now().isoformat()
        config['backup_history'].append({
            'filename': f"{backup_name}.zip",
            'date': timestamp,
            'type': backup_type,
            'notes': notes,
            'size': os.path.getsize(zip_filename)
        })
        
        # الاحتفاظ بآخر N نسخة فقط
        if len(config['backup_history']) > config['max_backups']:
            old_backups = sorted(config['backup_history'], key=lambda x: x['date'])[:-config['max_backups']]
            for old_backup in old_backups:
                old_file = os.path.join(BACKUP_FOLDER, old_backup['filename'])
                if os.path.exists(old_file):
                    os.remove(old_file)
                config['backup_history'].remove(old_backup)
        
        save_backup_config(config)
        return True, zip_filename, f"تم إنشاء النسخة الاحتياطية: {backup_name}.zip"
    
    except Exception as e:
        return False, None, f"فشل إنشاء النسخة: {str(e)}"

def restore_backup(zip_file_path):
    """استعادة نسخة احتياطية"""
    try:
        temp_folder = "temp_restore"
        if os.path.exists(temp_folder):
            shutil.rmtree(temp_folder)
        os.makedirs(temp_folder)
        
        with zipfile.ZipFile(zip_file_path, 'r') as zipf:
            zipf.extractall(temp_folder)
        
        db_backup_path = os.path.join(temp_folder, DB_NAME)
        if os.path.exists(db_backup_path):
            # نسخة طوارئ قبل الاستبدال
            if os.path.exists(DB_NAME):
                emergency = f"{DB_NAME}.emergency_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                shutil.copy2(DB_NAME, emergency)
            
            shutil.copy2(db_backup_path, DB_NAME)
        
        shutil.rmtree(temp_folder)
        return True, "تم استعادة النسخة الاحتياطية بنجاح!"
    except Exception as e:
        return False, f"فشل استعادة النسخة: {str(e)}"

# ===================================================================
# تهيئة قاعدة البيانات
# ===================================================================
init_database()

# ===================================================================
# الشريط الجانبي (Sidebar)
# ===================================================================
st.sidebar.title("🧹 مخزن النظافة")
st.sidebar.caption("نظام مراقبة المخزون - غير هادف للربح")

# حالة النسخ الاحتياطي
config = load_backup_config()
if config['last_backup_date']:
    last_backup = datetime.fromisoformat(config['last_backup_date'])
    days_ago = (datetime.now() - last_backup).days
    if days_ago > 7:
        st.sidebar.warning(f"⚠️ آخر نسخة احتياطية منذ {days_ago} يوم")
    else:
        st.sidebar.success(f"✅ آخر نسخة: {last_backup.strftime('%Y-%m-%d %H:%M')}")

# تنبيهات المخزون
st.sidebar.markdown("---")
st.sidebar.markdown("### ⚠️ التنبيهات")

conn = get_db_connection()
today = date.today()

# تنبيهات الصلاحية
expiring = conn.execute("""
    SELECT i.name, ea.expiry_date, ea.qty_remaining
    FROM expiry_alerts ea
    JOIN items i ON ea.item_id = i.id
    WHERE ea.is_consumed = 0 AND ea.expiry_date <= ?
""", ((today + timedelta(days=30)).isoformat(),)).fetchall()

if expiring:
    st.sidebar.error(f"🚨 {len(expiring)} أصناف تقترب من انتهاء الصلاحية")

# تنبيهات المخزون المنخفض
low_stock = conn.execute("""
    SELECT name, current_balance, min_qty
    FROM items
    WHERE current_balance <= min_qty AND is_active = 1
""").fetchall()

if low_stock:
    st.sidebar.warning(f"📉 {len(low_stock)} أصناف وصلت للحد الأدنى")

conn.close()

# القائمة الرئيسية
st.sidebar.markdown("---")
menu = st.sidebar.radio("القائمة الرئيسية", [
    "📊 لوحة التحكم",
    "📦 إدارة الأصناف",
    "📂 التصنيفات والوحدات",
    "🏨 الفنادق",
    "🏢 الموردين",
    "📍 أماكن التخزين",
    "📥 الوارد (المشتريات)",
    "📤 الصادر (الصرف)",
    "📝 الجرد الدوري",
    "⚠️ متابعة الصلاحيات",
    "📈 التقارير والفلترة",
    "💾 النسخ الاحتياطي"
])

# ===================================================================
# لوحة التحكم
# ===================================================================
if menu == "📊 لوحة التحكم":
    st.header("📊 لوحة التحكم - مراقبة المخزون")
    
    conn = get_db_connection()
    
    # إحصائيات رئيسية
    col1, col2, col3, col4 = st.columns(4)
    
    total_items = conn.execute("SELECT COUNT(*) FROM items WHERE is_active = 1").fetchone()[0]
    col1.metric("إجمالي الأصناف", total_items)
    
    total_qty = conn.execute("SELECT SUM(current_balance) FROM items WHERE is_active = 1").fetchone()[0] or 0
    col2.metric("إجمالي الكميات", f"{total_qty:,.1f}")
    
    low_count = len(low_stock) if 'low_stock' in dir() else conn.execute(
        "SELECT COUNT(*) FROM items WHERE current_balance <= min_qty AND is_active = 1"
    ).fetchone()[0]
    col3.metric("أصناف منخفضة 📉", low_count)
    
    expired_count = conn.execute(
        "SELECT COUNT(*) FROM expiry_alerts WHERE is_consumed = 0 AND expiry_date < ?",
        (today.isoformat(),)
    ).fetchone()[0]
    col4.metric("منتهية الصلاحية ❌", expired_count)
    
    st.divider()
    
    # الأصناف الأقل من الحد الأدنى
    st.subheader("🚨 أصناف تحتاج مراجعة")
    
    low_items = conn.execute("""
        SELECT i.item_code, i.name, i.current_balance, i.min_qty,
               c.category_name, u.unit_symbol, sl.location_name
        FROM items i
        LEFT JOIN categories c ON i.category_id = c.id
        LEFT JOIN units u ON i.unit_id = u.id
        LEFT JOIN storage_locations sl ON i.storage_location_id = sl.id
        WHERE i.current_balance <= i.min_qty AND i.is_active = 1
        ORDER BY i.current_balance
    """).fetchall()
    
    if low_items:
        df_low = pd.DataFrame(low_items, columns=[
            'الكود', 'الصنف', 'الرصيد', 'الحد الأدنى',
            'التصنيف', 'الوحدة', 'مكان التخزين'
        ])
        st.dataframe(df_low, use_container_width=True)
        
        st.info("💡 **الكميات المقترحة للطلب:**")
        for item in low_items:
            suggested = (item['min_qty'] * 2) - item['current_balance']
            st.write(f"• {item['name']}: {suggested:.1f} {item['unit_symbol']}")
    else:
        st.success("✅ جميع الأصناف أعلى من الحد الأدنى")
    
    conn.close()

# ===================================================================
# إدارة الأصناف
# ===================================================================
elif menu == "📦 إدارة الأصناف":
    st.header("📦 إدارة الأصناف")
    
    conn = get_db_connection()
    
    tab1, tab2 = st.tabs(["➕ إضافة صنف جديد", "📋 قائمة الأصناف"])
    
    with tab1:
        st.subheader("إضافة صنف جديد")
        
        # جلب القوائم المساعدة
        categories = conn.execute("SELECT id, category_name FROM categories").fetchall()
        units = conn.execute("SELECT id, unit_name, unit_symbol FROM units").fetchall()
        locations = conn.execute("SELECT id, location_name FROM storage_locations").fetchall()
        suppliers = conn.execute("SELECT id, supplier_name FROM suppliers").fetchall()
        
        with st.form("add_item_form"):
            col1, col2 = st.columns(2)
            
            with col1:
                item_name = st.text_input("اسم الصنف *")
                cat_choice = st.selectbox("التصنيف", [c['category_name'] for c in categories] if categories else ["عام"])
                unit_choice = st.selectbox("وحدة القياس", 
                                          [f"{u['unit_name']} ({u['unit_symbol']})" for u in units] if units else ["قطعة"])
            
            with col2:
                loc_choice = st.selectbox("مكان التخزين", 
                                         [l['location_name'] for l in locations] if locations else ["المخزن الرئيسي"])
                supp_choice = st.selectbox("المورد الأساسي", 
                                          [s['supplier_name'] for s in suppliers] if suppliers else ["-"])
                shelf_life = st.number_input("مدة الصلاحية (بالأيام)", min_value=0, value=365,
                                           help="0 = لا توجد صلاحية محددة")
            
            st.divider()
            
            col3, col4, col5 = st.columns(3)
            with col3:
                min_qty = st.number_input("الحد الأدنى", min_value=0.0, value=10.0, step=1.0)
            with col4:
                max_qty = st.number_input("الحد الأقصى", min_value=0.0, value=100.0, step=1.0)
            with col5:
                initial_balance = st.number_input("الرصيد الافتتاحي", min_value=0.0, value=0.0, step=1.0)
            
            notes = st.text_area("ملاحظات")
            
            if st.form_submit_button("حفظ الصنف", type="primary"):
                if item_name:
                    try:
                        cat_id = [c['id'] for c in categories if c['category_name'] == cat_choice][0] if categories else None
                        unit_id = [u['id'] for u in units if f"{u['unit_name']} ({u['unit_symbol']})" == unit_choice][0] if units else None
                        loc_id = [l['id'] for l in locations if l['location_name'] == loc_choice][0] if locations else None
                        supp_id = [s['id'] for s in suppliers if s['supplier_name'] == supp_choice][0] if suppliers and supp_choice != "-" else None
                        
                        item_code = generate_item_code(cat_id or 1)
                        
                        conn.execute("""
                            INSERT INTO items (item_code, name, category_id, unit_id,
                                             min_qty, max_qty, current_balance,
                                             storage_location_id, primary_supplier_id,
                                             shelf_life_days, notes, created_date, last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (item_code, item_name, cat_id, unit_id,
                             min_qty, max_qty, initial_balance,
                             loc_id, supp_id, shelf_life, notes,
                             today.isoformat(), today.isoformat()))
                        
                        conn.commit()
                        st.success(f"✅ تم إضافة الصنف! الكود: {item_code}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ خطأ: {str(e)}")
    
    with tab2:
        st.subheader("قائمة الأصناف")
        
        items = conn.execute("""
            SELECT i.item_code, i.name, c.category_name, i.current_balance,
                   u.unit_symbol, i.min_qty, i.max_qty, sl.location_name
            FROM items i
            LEFT JOIN categories c ON i.category_id = c.id
            LEFT JOIN units u ON i.unit_id = u.id
            LEFT JOIN storage_locations sl ON i.storage_location_id = sl.id
            WHERE i.is_active = 1
            ORDER BY i.name
        """).fetchall()
        
        if items:
            df_items = pd.DataFrame(items, columns=[
                'الكود', 'الصنف', 'التصنيف', 'الرصيد',
                'الوحدة', 'الحد الأدنى', 'الحد الأقصى', 'مكان التخزين'
            ])
            st.dataframe(df_items, use_container_width=True)
            
            # تصدير Excel
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_items.to_excel(writer, sheet_name='الأصناف', index=False)
            
            st.download_button(
                label="📥 تحميل Excel",
                data=output.getvalue(),
                file_name=f"items_{today}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("لا توجد أصناف مسجلة")
    
    conn.close()

# ===================================================================
# التصنيفات والوحدات
# ===================================================================
elif menu == "📂 التصنيفات والوحدات":
    st.header("📂 إدارة التصنيفات والوحدات")
    
    tab1, tab2, tab3 = st.tabs(["تصنيفات الأصناف", "وحدات القياس", "أماكن التخزين"])
    
    conn = get_db_connection()
    
    with tab1:
        st.subheader("التصنيفات")
        with st.form("add_category"):
            cat_name = st.text_input("اسم التصنيف")
            cat_desc = st.text_area("الوصف")
            if st.form_submit_button("إضافة"):
                if cat_name:
                    conn.execute("INSERT OR IGNORE INTO categories (category_name, description) VALUES (?, ?)",
                               (cat_name, cat_desc))
                    conn.commit()
                    st.success("تمت الإضافة!")
                    st.rerun()
        
        categories = conn.execute("SELECT * FROM categories").fetchall()
        if categories:
            st.dataframe(pd.DataFrame(categories, columns=['م', 'التصنيف', 'الوصف']))
    
    with tab2:
        st.subheader("وحدات القياس")
        with st.form("add_unit"):
            col1, col2 = st.columns(2)
            with col1:
                unit_name = st.text_input("اسم الوحدة")
            with col2:
                unit_symbol = st.text_input("الرمز")
            if st.form_submit_button("إضافة"):
                if unit_name:
                    conn.execute("INSERT OR IGNORE INTO units (unit_name, unit_symbol) VALUES (?, ?)",
                               (unit_name, unit_symbol))
                    conn.commit()
                    st.success("تمت الإضافة!")
                    st.rerun()
        
        units = conn.execute("SELECT * FROM units").fetchall()
        if units:
            st.dataframe(pd.DataFrame(units, columns=['م', 'الوحدة', 'الرمز']))
    
    with tab3:
        st.subheader("أماكن التخزين")
        with st.form("add_location"):
            loc_name = st.text_input("اسم المكان")
            loc_desc = st.text_area("الوصف")
            if st.form_submit_button("إضافة"):
                if loc_name:
                    conn.execute("INSERT OR IGNORE INTO storage_locations (location_name, description) VALUES (?, ?)",
                               (loc_name, loc_desc))
                    conn.commit()
                    st.success("تمت الإضافة!")
                    st.rerun()
        
        locations = conn.execute("SELECT * FROM storage_locations").fetchall()
        if locations:
            st.dataframe(pd.DataFrame(locations, columns=['م', 'المكان', 'الوصف']))
    
    conn.close()

# ===================================================================
# الفنادق
# ===================================================================
elif menu == "🏨 الفنادق":
    st.header("🏨 إدارة الفنادق")
    
    conn = get_db_connection()
    
    with st.form("add_hotel"):
        hotel_name = st.text_input("اسم الفندق *")
        contact_person = st.text_input("الشخص المسؤول")
        phone = st.text_input("رقم الهاتف")
        notes = st.text_area("ملاحظات")
        
        if st.form_submit_button("إضافة فندق"):
            if hotel_name:
                try:
                    conn.execute("""
                        INSERT INTO hotels (name, contact_person, phone, notes)
                        VALUES (?, ?, ?, ?)
                    """, (hotel_name, contact_person, phone, notes))
                    conn.commit()
                    st.success(f"تمت إضافة {hotel_name}")
                    st.rerun()
                except:
                    st.error("الفندق موجود مسبقاً!")
    
    hotels = conn.execute("SELECT * FROM hotels").fetchall()
    if hotels:
        df_hotels = pd.DataFrame(hotels, columns=['م', 'الفندق', 'المسؤول', 'الهاتف', 'ملاحظات'])
        st.dataframe(df_hotels)
    
    conn.close()

# ===================================================================
# الموردين
# ===================================================================
elif menu == "🏢 الموردين":
    st.header("🏢 إدارة الموردين")
    
    conn = get_db_connection()
    
    with st.form("add_supplier"):
        supplier_name = st.text_input("اسم المورد *")
        contact_info = st.text_input("معلومات الاتصال")
        notes = st.text_area("ملاحظات")
        
        if st.form_submit_button("إضافة مورد"):
            if supplier_name:
                try:
                    conn.execute("""
                        INSERT INTO suppliers (supplier_name, contact_info, notes)
                        VALUES (?, ?, ?)
                    """, (supplier_name, contact_info, notes))
                    conn.commit()
                    st.success(f"تمت إضافة {supplier_name}")
                    st.rerun()
                except:
                    st.error("المورد موجود مسبقاً!")
    
    suppliers = conn.execute("SELECT * FROM suppliers").fetchall()
    if suppliers:
        st.dataframe(pd.DataFrame(suppliers, columns=['م', 'المورد', 'الاتصال', 'ملاحظات']))
    
    conn.close()

# ===================================================================
# الوارد (المشتريات)
# ===================================================================
elif menu == "📥 الوارد (المشتريات)":
    st.header("📥 تسجيل المشتريات (وارد)")
    
    conn = get_db_connection()
    
    items = conn.execute("SELECT id, name, unit_id FROM items WHERE is_active = 1").fetchall()
    suppliers = conn.execute("SELECT id, supplier_name FROM suppliers").fetchall()
    units = conn.execute("SELECT id, unit_symbol FROM units").fetchall()
    
    if items:
        with st.form("add_inward"):
            item_choice = st.selectbox("الصنف", [i['name'] for i in items])
            supplier_choice = st.selectbox("المورد", [s['supplier_name'] for s in suppliers] if suppliers else ["-"])
            
            col1, col2, col3 = st.columns(3)
            with col1:
                qty = st.number_input("الكمية", min_value=0.1, value=1.0, step=0.1)
            with col2:
                batch_number = st.text_input("رقم التشغيلة")
            with col3:
                expiry_date = st.date_input("تاريخ انتهاء الصلاحية", value=today + timedelta(days=365))
            
            transaction_date = st.date_input("تاريخ الاستلام", value=today)
            notes = st.text_area("ملاحظات (رقم الفاتورة، الخ)")
            
            if st.form_submit_button("تسجيل المشتريات", type="primary"):
                item_id = [i['id'] for i in items if i['name'] == item_choice][0]
                unit_id = [i['unit_id'] for i in items if i['name'] == item_choice][0]
                
                # إضافة الحركة
                conn.execute("""
                    INSERT INTO transactions (transaction_type, item_id, qty, unit_id, 
                                            batch_number, expiry_date, transaction_date, notes)
                    VALUES ('وارد', ?, ?, ?, ?, ?, ?, ?)
                """, ('وارد', item_id, qty, unit_id, batch_number, 
                     expiry_date.isoformat() if expiry_date else None, 
                     transaction_date.isoformat(), notes))
                
                # تحديث الرصيد
                conn.execute("""
                    UPDATE items 
                    SET current_balance = current_balance + ?, last_updated = ?
                    WHERE id = ?
                """, (qty, today.isoformat(), item_id))
                
                # إضافة تنبيه صلاحية
                if expiry_date:
                    conn.execute("""
                        INSERT INTO expiry_alerts (item_id, batch_number, expiry_date, qty_remaining)
                        VALUES (?, ?, ?, ?)
                    """, (item_id, batch_number, expiry_date.isoformat(), qty))
                
                conn.commit()
                order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                st.success(f"✅ تم تسجيل المشتريات! رقم الحركة: #{order_id}")
                
                # طباعة إذن استلام
                item_unit = [u['unit_symbol'] for u in units if u['id'] == unit_id][0] if units else ""
                supplier_name = [s['supplier_name'] for s in suppliers if s['supplier_name'] == supplier_choice][0] if suppliers and supplier_choice != "-" else ""
                
                pdf_bytes = generate_pdf_voucher(
                    order_id, "وارد", transaction_date.isoformat(),
                    supplier_name, item_choice, qty, item_unit, notes
                )
                
                st.download_button(
                    label="📄 تحميل إذن الاستلام PDF",
                    data=pdf_bytes,
                    file_name=f"Purchase_{order_id}.pdf",
                    mime="application/pdf"
                )
                
                st.rerun()
    
    conn.close()

# ===================================================================
# الصادر (الصرف)
# ===================================================================
elif menu == "📤 الصادر (الصرف)":
    st.header("📤 صرف مستلزمات للفنادق")
    
    conn = get_db_connection()
    
    items = conn.execute("SELECT id, name, current_balance, unit_id FROM items WHERE is_active = 1").fetchall()
    hotels = conn.execute("SELECT id, name FROM hotels").fetchall()
    units = conn.execute("SELECT id, unit_symbol FROM units").fetchall()
    
    if items and hotels:
        with st.form("add_outward"):
            item_choice = st.selectbox("الصنف", [f"{i['name']} (الرصيد: {i['current_balance']})" for i in items])
            hotel_choice = st.selectbox("الفندق المستلم", [h['name'] for h in hotels])
            
            col1, col2 = st.columns(2)
            with col1:
                qty = st.number_input("الكمية المصروفة", min_value=0.1, value=1.0, step=0.1)
            with col2:
                transaction_date = st.date_input("تاريخ الصرف", value=today)
            
            notes = st.text_area("ملاحظات")
            
            if st.form_submit_button("تأكيد الصرف", type="primary"):
                item_name = item_choice.split(" (الرصيد:")[0]
                item_data = [i for i in items if i['name'] == item_name][0]
                
                if qty > item_data['current_balance']:
                    st.error(f"❌ الرصيد غير كاف! المتاح: {item_data['current_balance']}")
                else:
                    # إضافة الحركة
                    conn.execute("""
                        INSERT INTO transactions (transaction_type, item_id, hotel_id, qty, 
                                                unit_id, transaction_date, notes)
                        VALUES ('صادر', ?, ?, ?, ?, ?, ?)
                    """, ('صادر', item_data['id'], 
                         [h['id'] for h in hotels if h['name'] == hotel_choice][0],
                         qty, item_data['unit_id'], transaction_date.isoformat(), notes))
                    
                    # تحديث الرصيد
                    conn.execute("""
                        UPDATE items 
                        SET current_balance = current_balance - ?, last_updated = ?
                        WHERE id = ?
                    """, (qty, today.isoformat(), item_data['id']))
                    
                    conn.commit()
                    order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    
                    st.success(f"✅ تم الصرف! رقم الحركة: #{order_id}")
                    
                    # طباعة إذن صرف
                    item_unit = [u['unit_symbol'] for u in units if u['id'] == item_data['unit_id']][0] if units else ""
                    
                    pdf_bytes = generate_pdf_voucher(
                        order_id, "صادر", transaction_date.isoformat(),
                        hotel_choice, item_name, qty, item_unit, notes
                    )
                    
                    st.download_button(
                        label="📄 تحميل إذن الصرف PDF",
                        data=pdf_bytes,
                        file_name=f"Voucher_{order_id}.pdf",
                        mime="application/pdf"
                    )
                    
                    st.rerun()
    
    conn.close()

# ===================================================================
# الجرد الدوري
# ===================================================================
elif menu == "📝 الجرد الدوري":
    st.header("📝 الجرد الدوري")
    
    conn = get_db_connection()
    
    items = conn.execute("""
        SELECT id, name, current_balance, unit_id 
        FROM items WHERE is_active = 1
    """).fetchall()
    
    units = conn.execute("SELECT id, unit_symbol FROM units").fetchall()
    
    if items:
        item_choice = st.selectbox("اختر الصنف للجرد", [i['name'] for i in items])
        item_data = [i for i in items if i['name'] == item_choice][0]
        item_unit = [u['unit_symbol'] for u in units if u['id'] == item_data['unit_id']][0] if units else ""
        
        st.info(f"📊 الرصيد المسجل: {item_data['current_balance']} {item_unit}")
        
        actual_qty = st.number_input("الكمية الفعلية بعد الجرد", 
                                    min_value=0.0, value=float(item_data['current_balance']), step=0.1)
        
        difference = actual_qty - item_data['current_balance']
        
        if difference > 0:
            st.warning(f"⚠️ توجد زيادة: +{difference:.1f} {item_unit}")
        elif difference < 0:
            st.error(f"❌ يوجد عجز: {difference:.1f} {item_unit}")
        else:
            st.success("✅ الجرد مطابق")
        
        notes = st.text_area("ملاحظات الجرد")
        counted_by = st.text_input("القائم بالجرد")
        
        if st.button("تأكيد وحفظ الجرد", type="primary"):
            if difference != 0:
                conn.execute("""
                    INSERT INTO transactions (transaction_type, item_id, qty, unit_id,
                                            transaction_date, notes, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, ('تسوية إضافة' if difference > 0 else 'تسوية عجز',
                     item_data['id'], abs(difference), item_data['unit_id'],
                     today.isoformat(), f"تسوية جرد - {notes}", counted_by))
            
            # تحديث الرصيد
            conn.execute("""
                UPDATE items SET current_balance = ?, last_updated = ?
                WHERE id = ?
            """, (actual_qty, today.isoformat(), item_data['id']))
            
            # حفظ سجل الجرد
            conn.execute("""
                INSERT INTO inventory_counts (count_date, item_id, expected_qty, actual_qty, 
                                            difference, notes, counted_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (today.isoformat(), item_data['id'], item_data['current_balance'],
                 actual_qty, difference, notes, counted_by))
            
            conn.commit()
            st.success("✅ تم حفظ الجرد بنجاح!")
            st.rerun()
    
    conn.close()

# ===================================================================
# متابعة الصلاحيات
# ===================================================================
elif menu == "⚠️ متابعة الصلاحيات":
    st.header("⚠️ متابعة تواريخ الصلاحية")
    
    conn = get_db_connection()
    
    tab1, tab2 = st.tabs(["📋 المنتهية والقريبة", "➕ إضافة تشغيلة جديدة"])
    
    with tab1:
        filter_days = st.selectbox("عرض الأصناف التي تنتهي خلال:", [30, 60, 90, 180, 365])
        
        expiry_items = conn.execute("""
            SELECT i.name, ea.batch_number, ea.expiry_date, ea.qty_remaining,
                   u.unit_symbol,
                   CAST(JULIANDAY(ea.expiry_date) - JULIANDAY(?) AS INTEGER) as days_left
            FROM expiry_alerts ea
            JOIN items i ON ea.item_id = i.id
            LEFT JOIN units u ON i.unit_id = u.id
            WHERE ea.is_consumed = 0 
            AND ea.expiry_date <= ?
            ORDER BY ea.expiry_date
        """, (today.isoformat(), (today + timedelta(days=filter_days)).isoformat())).fetchall()
        
        if expiry_items:
            df_exp = pd.DataFrame(expiry_items, columns=[
                'الصنف', 'رقم التشغيلة', 'تاريخ الانتهاء', 'الكمية', 'الوحدة', 'الأيام المتبقية'
            ])
            st.dataframe(df_exp, use_container_width=True)
            
            # تصدير Excel
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_exp.to_excel(writer, sheet_name='الصلاحيات', index=False)
            
            st.download_button(
                label="📥 تحميل تقرير الصلاحيات",
                data=output.getvalue(),
                file_name=f"expiry_report_{today}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.success(f"✅ لا توجد أصناف تنتهي خلال {filter_days} يوم")
    
    with tab2:
        st.subheader("إضافة تشغيلة جديدة لصنف موجود")
        
        items = conn.execute("SELECT id, name FROM items WHERE is_active = 1").fetchall()
        
        if items:
            with st.form("add_batch"):
                item_choice = st.selectbox("الصنف", [i['name'] for i in items])
                batch_number = st.text_input("رقم التشغيلة *")
                expiry_date = st.date_input("تاريخ انتهاء الصلاحية", value=today + timedelta(days=365))
                qty = st.number_input("الكمية في هذه التشغيلة", min_value=0.1, value=1.0)
                
                if st.form_submit_button("إضافة التشغيلة"):
                    if batch_number:
                        item_id = [i['id'] for i in items if i['name'] == item_choice][0]
                        conn.execute("""
                            INSERT INTO expiry_alerts (item_id, batch_number, expiry_date, qty_remaining)
                            VALUES (?, ?, ?, ?)
                        """, (item_id, batch_number, expiry_date.isoformat(), qty))
                        conn.commit()
                        st.success("✅ تمت إضافة التشغيلة!")
                        st.rerun()
    
    conn.close()

# ===================================================================
# التقارير
# ===================================================================
elif menu == "📈 التقارير والفلترة":
    st.header("📈 التقارير والفلترة")
    
    conn = get_db_connection()
    
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("من تاريخ", value=today - timedelta(days=30))
    with col2:
        end_date = st.date_input("إلى تاريخ", value=today)
    
    transaction_type = st.selectbox("نوع الحركة", ["الكل", "وارد", "صادر", "تسوية إضافة", "تسوية عجز"])
    
    query = """
        SELECT t.id, t.transaction_type, i.name as item_name, 
               COALESCE(h.name, '-') as hotel_name,
               t.qty, u.unit_symbol, t.transaction_date, t.notes
        FROM transactions t
        JOIN items i ON t.item_id = i.id
        LEFT JOIN hotels h ON t.hotel_id = h.id
        LEFT JOIN units u ON t.unit_id = u.id
        WHERE t.transaction_date BETWEEN ? AND ?
    """
    params = [start_date.isoformat(), end_date.isoformat()]
    
    if transaction_type != "الكل":
        query += " AND t.transaction_type = ?"
        params.append(transaction_type)
    
    query += " ORDER BY t.id DESC"
    
    transactions = conn.execute(query, params).fetchall()
    
    if transactions:
        df_trans = pd.DataFrame(transactions, columns=[
            'رقم الحركة', 'النوع', 'الصنف', 'الفندق/المورد',
            'الكمية', 'الوحدة', 'التاريخ', 'ملاحظات'
        ])
        st.dataframe(df_trans, use_container_width=True)
        
        st.metric("عدد الحركات", len(transactions))
        st.metric("إجمالي الكميات", sum(t['qty'] for t in transactions))
        
        # تصدير
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_trans.to_excel(writer, sheet_name='الحركات', index=False)
        
        st.download_button(
            label="📥 تحميل التقرير Excel",
            data=output.getvalue(),
            file_name=f"report_{start_date}_{end_date}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.info("لا توجد حركات في الفترة المحددة")
    
    conn.close()

# ===================================================================
# النسخ الاحتياطي
# ===================================================================
elif menu == "💾 النسخ الاحتياطي":
    st.header("💾 نظام النسخ الاحتياطي")
    
    config = load_backup_config()
    backup_list = config.get('backup_history', [])
    
    # إحصائيات
    col1, col2, col3 = st.columns(3)
    col1.metric("عدد النسخ", len(backup_list))
    col2.metric("آخر نسخة", config.get('last_backup_date', 'لا يوجد')[:10] if config.get('last_backup_date') else 'لا يوجد')
    total_size = sum(b.get('size', 0) for b in backup_list) / (1024*1024)
    col3.metric("المساحة", f"{total_size:.2f} MB")
    
    st.divider()
    
    tab1, tab2 = st.tabs(["📥 إنشاء نسخة", "📤 استعادة نسخة"])
    
    with tab1:
        notes = st.text_input("ملاحظات (اختياري)", placeholder="مثال: نسخة قبل التعديل")
        
        if st.button("🔄 إنشاء نسخة احتياطية", type="primary", use_container_width=True):
            with st.spinner("جاري إنشاء النسخة..."):
                success, filename, message = create_backup("يدوي", notes)
                if success:
                    st.success(f"✅ {message}")
                    
                    with open(filename, 'rb') as f:
                        st.download_button(
                            label="📥 تحميل النسخة",
                            data=f,
                            file_name=os.path.basename(filename),
                            mime="application/zip",
                            use_container_width=True
                        )
                else:
                    st.error(f"❌ {message}")
        
        # تحميل مباشر لقاعدة البيانات
        st.divider()
        if os.path.exists(DB_NAME):
            with open(DB_NAME, 'rb') as f:
                st.download_button(
                    label="📥 تحميل قاعدة البيانات مباشرة (.db)",
                    data=f,
                    file_name=f"database_{today}.db",
                    mime="application/x-sqlite3",
                    use_container_width=True
                )
    
    with tab2:
        uploaded_file = st.file_uploader("اختر ملف النسخة الاحتياطية (.zip)", type=['zip'])
        
        if uploaded_file:
            temp_path = f"temp_upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            with open(temp_path, 'wb') as f:
                f.write(uploaded_file.read())
            
            if st.button("🔄 استعادة النسخة", type="primary"):
                with st.spinner("جاري الاستعادة..."):
                    success, message = restore_backup(temp_path)
                    if success:
                        st.success(f"✅ {message}")
                        st.rerun()
                    else:
                        st.error(f"❌ {message}")
            
            # تنظيف
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    # سجل النسخ
    st.divider()
    st.subheader("📋 سجل النسخ الاحتياطية")
    
    if backup_list:
        for backup in backup_list[::-1][:5]:
            with st.expander(f"📦 {backup['filename']} - {backup['date']}"):
                st.write(f"النوع: {backup['type']}")
                st.write(f"الحجم: {backup['size'] / 1024:.2f} KB")
                st.write(f"ملاحظات: {backup['notes'] or 'لا يوجد'}")
    else:
        st.info("لا توجد نسخ سابقة")

# ===================================================================
# أماكن التخزين (قسم منفصل)
# ===================================================================
elif menu == "📍 أماكن التخزين":
    st.header("📍 إدارة أماكن التخزين")
    
    conn = get_db_connection()
    
    with st.form("add_storage_location"):
        loc_name = st.text_input("اسم المكان *")
        loc_desc = st.text_area("الوصف")
        
        if st.form_submit_button("إضافة"):
            if loc_name:
                try:
                    conn.execute("INSERT OR IGNORE INTO storage_locations (location_name, description) VALUES (?, ?)",
                               (loc_name, loc_desc))
                    conn.commit()
                    st.success("تمت الإضافة!")
                    st.rerun()
                except:
                    st.error("خطأ في الإضافة")
    
    locations = conn.execute("SELECT * FROM storage_locations").fetchall()
    if locations:
        st.dataframe(pd.DataFrame(locations, columns=['م', 'المكان', 'الوصف']))
    
    conn.close()

# ===================================================================
# تذييل الصفحة
# ===================================================================
st.sidebar.markdown("---")
st.sidebar.caption(f"© {datetime.now().year} - نظام مراقبة مخزون النظافة")
st.sidebar.caption(f"آخر تحديث: {datetime.now().strftime('%Y-%m-%d')}")

# زر نسخ احتياطي سريع
if st.sidebar.button("🔄 نسخة احتياطية سريعة", use_container_width=True):
    success, filename, message = create_backup("سريعة", "نسخة سريعة")
    if success:
        st.sidebar.success(message)
    else:
        st.sidebar.error(message)