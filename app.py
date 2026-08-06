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
import arabic_reshaper
from bidi.algorithm import get_display
import base64

# ======================== إعدادات الصفحة ========================
st.set_page_config(page_title="مخزن النظافة", layout="wide", initial_sidebar_state="expanded")

# ======================== إدارة الثيم والخط ========================
if 'font_size' not in st.session_state:
    st.session_state.font_size = 100
if 'theme_color' not in st.session_state:
    st.session_state.theme_color = "#00a86b"
if 'logo_path' not in st.session_state:
    st.session_state.logo_path = None

def apply_theme():
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap');
    *{{font-family:'Tajawal',sans-serif}}
    html,body,[class*="css"]{{direction:rtl;text-align:right;font-size:{st.session_state.font_size}% !important}}
    .stApp {{
        background-color: {st.session_state.theme_color};
        background-image: linear-gradient(135deg, {st.session_state.theme_color} 0%, #ffffff 100%);
    }}
    .stock-critical{{background-color:#ff4444;color:white;padding:5px 10px;border-radius:5px}}
    .stock-warning{{background-color:#ffbb33;color:black;padding:5px 10px;border-radius:5px}}
    .stock-good{{background-color:#00C851;color:white;padding:5px 10px;border-radius:5px}}
    </style>""", unsafe_allow_html=True)

apply_theme()

DB_NAME = 'cleaning_inventory.db'
BACKUP_FOLDER = 'backups'
ATTACHMENTS_FOLDER = 'attachments'
CONFIG_FILE = 'backup_config.json'
LOGO_FILE = 'logo.png'

if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)
if not os.path.exists(ATTACHMENTS_FOLDER):
    os.makedirs(ATTACHMENTS_FOLDER)

# ======================== دوال مساعدة ========================
def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS units (id INTEGER PRIMARY KEY AUTOINCREMENT, unit_name TEXT UNIQUE, unit_symbol TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_name TEXT UNIQUE, contact_info TEXT, notes TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_code TEXT UNIQUE,
        name TEXT NOT NULL UNIQUE,
        unit_id INTEGER,
        min_qty REAL DEFAULT 0,
        max_qty REAL DEFAULT 100,
        current_balance REAL DEFAULT 0,
        primary_supplier_id INTEGER,
        shelf_life_days INTEGER DEFAULT 365,
        notes TEXT,
        is_active BOOLEAN DEFAULT 1,
        created_date TEXT,
        last_updated TEXT,
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (primary_supplier_id) REFERENCES suppliers(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS hotels (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, contact_person TEXT, phone TEXT, notes TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS outward_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE,
        hotel_id INTEGER,
        recipient_name TEXT,
        order_date TEXT,
        notes TEXT,
        created_by TEXT,
        FOREIGN KEY (hotel_id) REFERENCES hotels(id)
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
        attachment TEXT,
        order_id INTEGER,
        FOREIGN KEY (item_id) REFERENCES items(id),
        FOREIGN KEY (hotel_id) REFERENCES hotels(id),
        FOREIGN KEY (unit_id) REFERENCES units(id),
        FOREIGN KEY (order_id) REFERENCES outward_orders(id)
    )''')
    for col, col_def in [('attachment', 'TEXT'), ('order_id', 'INTEGER')]:
        try:
            c.execute(f"ALTER TABLE transactions ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass

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

    for u_name, u_sym in [('قطعة','قطعة'),('لتر','لتر'),('كيلو','كجم'),('متر','متر'),
                         ('كرتونة','كرتونة'),('رول','رول'),('زجاجة','زجاجة'),('علبة','علبة'),('كيس','كيس')]:
        c.execute("INSERT OR IGNORE INTO units (unit_name, unit_symbol) VALUES (?,?)",(u_name,u_sym))

    default_users = [
        ('admin',hash_password('admin123'),'super_admin','المدير العام'),
        ('مشتريات',hash_password('buy123'),'purchasing','مسؤول المشتريات'),
        ('صرف',hash_password('out123'),'disbursement','مسؤول الصرف'),
        ('مشرف1',hash_password('sup123'),'supervisor','مشرف أول'),
        ('مشرف2',hash_password('sup456'),'supervisor','مشرف ثاني')
    ]
    for uname,pwd,role,fname in default_users:
        c.execute("INSERT OR IGNORE INTO users (username,password,role,full_name) VALUES (?,?,?,?)",(uname,pwd,role,fname))
    conn.commit()
    conn.close()

def login(username, password):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=? AND password=? AND is_active=1",
                        (username,hash_password(password))).fetchone()
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

def check_perm(role=None):
    if not st.session_state.get('logged_in'): return False
    if st.session_state.user['role']=='super_admin': return True
    if role and st.session_state.user['role']==role: return True
    return False

def has_role(role):
    return st.session_state.get('user',{}).get('role')==role

# ======================== PDF عربي ========================
def get_arabic_font():
    path = "Amiri-Regular.ttf"
    if not os.path.exists(path):
        try:
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf", path)
        except: pass
    return path if os.path.exists(path) else None

def shape_arabic(text):
    if not re.search('[\u0600-\u06FF]', str(text)):
        return text
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)

def generate_pdf(title, df, cols_map=None):
    font_path = get_arabic_font()
    pdf = FPDF()
    pdf.add_page()
    if font_path:
        pdf.add_font("Amiri", fname=font_path)
        pdf.set_font("Amiri", size=14)
    else:
        pdf.set_font("Helvetica", size=14)
    pdf.cell(0,10, shape_arabic(title), ln=True, align='C')
    pdf.ln(10)
    if df.empty:
        pdf.cell(0,10,shape_arabic("لا توجد بيانات"), ln=True)
        return bytes(pdf.output())
    if cols_map: df = df.rename(columns=cols_map)
    cols = list(df.columns)
    widths = []
    for col in cols:
        m = pdf.get_string_width(shape_arabic(str(col)))
        for _,r in df.iterrows():
            v = str(r[col]) if pd.notnull(r[col]) else '-'
            m = max(m, pdf.get_string_width(shape_arabic(v)))
        widths.append(m+10)
    total = sum(widths)
    if total > pdf.w-20:
        scale = (pdf.w-20)/total
        widths = [w*scale for w in widths]
    pdf.set_fill_color(0,168,107); pdf.set_text_color(255,255,255)
    for i,col in enumerate(cols):
        pdf.cell(widths[i],10, shape_arabic(str(col)), border=1, fill=True, align='C')
    pdf.ln()
    pdf.set_text_color(0,0,0)
    pdf.set_font("Amiri", size=10) if font_path else pdf.set_font("Helvetica", size=10)
    for _,row in df.iterrows():
        for i,col in enumerate(cols):
            v = str(row[col]) if pd.notnull(row[col]) else '-'
            pdf.cell(widths[i],8, shape_arabic(v), border=1, align='C')
        pdf.ln()
    return bytes(pdf.output())

def export_buttons(df, prefix, pdf_title=None):
    c1,c2 = st.columns(2)
    with c1:
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as w:
            df.to_excel(w, sheet_name='report', index=False)
        st.download_button("📥 Excel", data=out.getvalue(), file_name=f"{prefix}_{date.today()}.xlsx")
    with c2:
        if pdf_title:
            pdf_bytes = generate_pdf(pdf_title, df)
            st.download_button("📄 PDF", data=pdf_bytes, file_name=f"{prefix}_{date.today()}.pdf")

def generate_outward_order_number():
    conn = get_db()
    today_str = date.today().strftime("%Y%m%d")
    last = conn.execute("SELECT order_number FROM outward_orders WHERE order_number LIKE ? ORDER BY id DESC LIMIT 1",
                        (f"OUT-{today_str}-%",)).fetchone()
    conn.close()
    if last:
        last_num = int(last['order_number'].split('-')[-1])
        new_num = last_num + 1
    else:
        new_num = 1
    return f"OUT-{today_str}-{new_num:04d}"

# ======================== النسخ الاحتياطي ========================
def load_backup_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE,'r',encoding='utf-8') as f: return json.load(f)
    return {'backup_history':[],'last_backup_date':None,'max_backups':10}
def save_backup_config(cfg):
    with open(CONFIG_FILE,'w',encoding='utf-8') as f: json.dump(cfg,f,ensure_ascii=False,indent=2)
def create_backup(typ="يدوي",notes=""):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"backup_{ts}"
        path = os.path.join(BACKUP_FOLDER, name)
        os.makedirs(path, exist_ok=True)
        if os.path.exists(DB_NAME): shutil.copy2(DB_NAME, os.path.join(path, DB_NAME))
        conn = sqlite3.connect(DB_NAME)
        with pd.ExcelWriter(os.path.join(path,'preview.xlsx'), engine='xlsxwriter') as w:
            for t in ['items','hotels','transactions']:
                try: pd.read_sql_query(f"SELECT * FROM {t}", conn).to_excel(w, sheet_name=t, index=False)
                except: pass
        conn.close()
        with open(os.path.join(path,'info.json'),'w',encoding='utf-8') as f: json.dump({'date':ts,'type':typ,'notes':notes},f)
        zipf = os.path.join(BACKUP_FOLDER, f"{name}.zip")
        with zipfile.ZipFile(zipf,'w',zipfile.ZIP_DEFLATED) as zf:
            for root,_,files in os.walk(path):
                for file in files: zf.write(os.path.join(root,file), file)
        shutil.rmtree(path)
        cfg = load_backup_config()
        cfg['last_backup_date'] = datetime.now().isoformat()
        cfg['backup_history'].append({'filename':f"{name}.zip",'date':ts,'type':typ,'notes':notes,'size':os.path.getsize(zipf)})
        if len(cfg['backup_history']) > cfg['max_backups']:
            for old in sorted(cfg['backup_history'], key=lambda x:x['date'])[:-cfg['max_backups']]:
                old_file = os.path.join(BACKUP_FOLDER, old['filename'])
                if os.path.exists(old_file): os.remove(old_file)
                cfg['backup_history'].remove(old)
        save_backup_config(cfg)
        return True, zipf, f"تم إنشاء النسخة {name}.zip"
    except Exception as e:
        return False, None, str(e)
def restore_backup(zip_path):
    try:
        tmp = "tmp_res"
        if os.path.exists(tmp): shutil.rmtree(tmp)
        os.makedirs(tmp)
        with zipfile.ZipFile(zip_path,'r') as zf: zf.extractall(tmp)
        db_src = os.path.join(tmp, DB_NAME)
        if os.path.exists(db_src):
            if os.path.exists(DB_NAME): shutil.copy2(DB_NAME, DB_NAME+".emergency")
            shutil.copy2(db_src, DB_NAME)
        shutil.rmtree(tmp)
        return True, "تمت الاستعادة"
    except Exception as e:
        return False, str(e)
def delete_transaction(trans_id):
    conn = get_db()
    trans = conn.execute("SELECT * FROM transactions WHERE id=?", (trans_id,)).fetchone()
    if not trans:
        conn.close()
        return False, "الحركة غير موجودة"
    item_id = trans['item_id']
    qty = trans['qty']
    typ = trans['transaction_type']
    if typ == 'وارد' or typ == 'تسوية إضافة':
        conn.execute("UPDATE items SET current_balance = current_balance - ?, last_updated=? WHERE id=?", (qty, date.today().isoformat(), item_id))
    elif typ == 'صادر' or typ == 'تسوية عجز':
        conn.execute("UPDATE items SET current_balance = current_balance + ?, last_updated=? WHERE id=?", (qty, date.today().isoformat(), item_id))
    conn.execute("DELETE FROM transactions WHERE id=?", (trans_id,))
    conn.commit()
    conn.close()
    return True, "تم حذف الحركة بنجاح"
def save_attachment(uploaded_file, transaction_id):
    if uploaded_file is None: return None
    file_ext = os.path.splitext(uploaded_file.name)[1]
    safe_name = f"trans_{transaction_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}{file_ext}"
    file_path = os.path.join(ATTACHMENTS_FOLDER, safe_name)
    with open(file_path, "wb") as f: f.write(uploaded_file.getbuffer())
    return safe_name

# ======================== مزامنة Google Drive ========================
def get_drive_service():
    try:
        from pydrive2.auth import GoogleAuth
        from pydrive2.drive import GoogleDrive
        gauth = GoogleAuth()
        if st.secrets.get("google_drive", {}).get("refresh_token"):
            settings = {
                "client_config_backend": "settings",
                "client_config": {
                    "client_id": st.secrets["google_drive"]["client_id"],
                    "client_secret": st.secrets["google_drive"]["client_secret"]
                },
                "save_credentials": True,
                "save_credentials_backend": "file",
                "save_credentials_file": "credentials.json",
                "get_refresh_token": True
            }
            with open("settings.yaml", "w") as f:
                import yaml
                yaml.dump(settings, f)
            gauth.LoadCredentialsFile("credentials.json")
            if gauth.credentials is None:
                gauth.Refresh()
            elif gauth.access_token_expired:
                gauth.Refresh()
            else:
                gauth.Authorize()
            gauth.SaveCredentialsFile("credentials.json")
            return GoogleDrive(gauth)
        return None
    except Exception as e:
        return None

def upload_db_to_drive():
    if not os.path.exists(DB_NAME):
        return False
    drive = get_drive_service()
    if not drive:
        return False
    try:
        folder_id = st.secrets["google_drive"]["folder_id"]
        file_list = drive.ListFile({'q': f"'{folder_id}' in parents and title='{DB_NAME}'"}).GetList()
        for f in file_list:
            f.Delete()
        file_drive = drive.CreateFile({'title': DB_NAME, 'parents': [{'id': folder_id}]})
        file_drive.SetContentFile(DB_NAME)
        file_drive.Upload()
        return True
    except Exception as e:
        st.error(f"فشل الرفع: {str(e)}")
        return False

def download_db_from_drive():
    drive = get_drive_service()
    if not drive:
        return False
    try:
        folder_id = st.secrets["google_drive"]["folder_id"]
        file_list = drive.ListFile({'q': f"'{folder_id}' in parents and title='{DB_NAME}'"}).GetList()
        if file_list:
            file_list[0].GetContentFile(DB_NAME)
            return True
        return False
    except Exception as e:
        return False

def sync_db_if_needed():
    if not os.path.exists(DB_NAME):
        if st.secrets.get("google_drive"):
            if download_db_from_drive():
                st.success("✅ تم استعادة البيانات بنجاح من Google Drive.")
            else:
                st.warning("⚠️ لم يتم العثور على نسخة احتياطية في Drive. بدء قاعدة بيانات جديدة.")

# ======================== بدء التشغيل ========================
init_db()
if st.secrets.get("google_drive"):
    sync_db_if_needed()

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user = None

if not st.session_state.logged_in:
    st.title("🔐 تسجيل الدخول")
    with st.form("login"):
        uname = st.text_input("اسم المستخدم")
        pwd = st.text_input("كلمة المرور", type="password")
        if st.form_submit_button("دخول"):
            if login(uname, pwd):
                st.success("تم الدخول"); st.rerun()
            else: st.error("خطأ")
    st.stop()

# -------------------- الشريط الجانبي --------------------
st.sidebar.title("🧹 مخزن النظافة")
if st.session_state.logo_path and os.path.exists(st.session_state.logo_path):
    st.sidebar.image(st.session_state.logo_path, width=150)
st.sidebar.write(f"مرحباً {st.session_state.user['full_name']} ({st.session_state.user['role']})")
if st.sidebar.button("تسجيل الخروج"): logout()
st.sidebar.divider()

st.sidebar.subheader("🎨 إعدادات المظهر")
new_font_size = st.sidebar.slider("حجم الخط (%)", 50, 200, st.session_state.font_size, step=10, key="global_font")
theme_color = st.sidebar.color_picker("لون البرنامج", st.session_state.theme_color, key="global_theme")
st.sidebar.markdown("---")
uploaded_logo = st.sidebar.file_uploader("📷 رفع شعار", type=["png","jpg","jpeg"])
if uploaded_logo:
    with open(LOGO_FILE, "wb") as f: f.write(uploaded_logo.getbuffer())
    st.session_state.logo_path = LOGO_FILE
    st.rerun()

if new_font_size != st.session_state.font_size or theme_color != st.session_state.theme_color:
    st.session_state.font_size = new_font_size
    st.session_state.theme_color = theme_color
    st.rerun()

st.sidebar.divider()

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

choice = st.sidebar.radio("القائمة", menu)

# ======================== الصفحات ========================
if choice == "📊 لوحة التحكم":
    st.header("لوحة التحكم")
    conn = get_db()
    today = date.today()
    total = conn.execute("SELECT COUNT(*) FROM items WHERE is_active=1").fetchone()[0]
    low = conn.execute("SELECT COUNT(*) FROM items WHERE current_balance<=min_qty AND is_active=1").fetchone()[0]
    exp = conn.execute("SELECT COUNT(*) FROM expiry_alerts WHERE is_consumed=0 AND expiry_date<?",(today.isoformat(),)).fetchone()[0]
    c1,c2,c3 = st.columns(3)
    c1.metric("الأصناف", total); c2.metric("تحت الحد", low); c3.metric("منتهية الصلاحية", exp)
    st.divider()
    low_items = conn.execute("SELECT i.item_code, i.name, i.current_balance, i.min_qty, u.unit_symbol FROM items i LEFT JOIN units u ON i.unit_id=u.id WHERE i.current_balance<=i.min_qty AND i.is_active=1").fetchall()
    if low_items:
        df = pd.DataFrame(low_items, columns=['كود','الصنف','الرصيد','الحد الأدنى','الوحدة'])
        st.dataframe(df)
        export_buttons(df, "اصناف_منخفضة", "تقرير الأصناف أقل من الحد الأدنى")
    conn.close()

elif choice == "📦 إدارة الأصناف":
    if not check_perm(): st.error("غير مصرح"); st.stop()
    st.header("إدارة الأصناف")
    conn = get_db()
    tab1, tab2 = st.tabs(["إضافة صنف","تعديل/حذف صنف"])
    units = conn.execute("SELECT id, unit_name, unit_symbol FROM units").fetchall()
    suppliers = conn.execute("SELECT id, supplier_name FROM suppliers").fetchall()
    with tab1:
        with st.form("add_item"):
            name = st.text_input("اسم الصنف *")
            unit = st.selectbox("الوحدة", [f"{u['unit_name']} ({u['unit_symbol']})" for u in units])
            supplier = st.selectbox("المورد الأساسي (اختياري)", ["-"] + [s['supplier_name'] for s in suppliers])
            min_q = st.number_input("الحد الأدنى",0.0,10000.0,10.0)
            max_q = st.number_input("الحد الأقصى",0.0,10000.0,100.0)
            init_bal = st.number_input("الرصيد الافتتاحي",0.0,10000.0,0.0)
            shelf_life = st.number_input("مدة الصلاحية (أيام)",0,3650,365)
            notes = st.text_area("ملاحظات")
            if st.form_submit_button("حفظ"):
                if not name:
                    st.error("الرجاء إدخال اسم الصنف")
                else:
                    unit_id = [u['id'] for u in units if f"{u['unit_name']} ({u['unit_symbol']})"==unit][0]
                    supp_id = None if supplier=="-" else [s['id'] for s in suppliers if s['supplier_name']==supplier][0]
                    code = f"ITM-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    try:
                        conn.execute("INSERT INTO items (item_code, name, unit_id, min_qty, max_qty, current_balance, primary_supplier_id, shelf_life_days, notes, created_date, last_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                     (code, name.strip(), unit_id, min_q, max_q, init_bal, supp_id, shelf_life, notes, date.today().isoformat(), date.today().isoformat()))
                        conn.commit()
                        st.success("تم الحفظ بنجاح")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("اسم الصنف موجود مسبقاً!")
    with tab2:
        items = conn.execute("SELECT id, item_code, name, current_balance, unit_id, min_qty, max_qty, is_active FROM items").fetchall()
        if items:
            item_names = [f"{it['name']} (كود: {it['item_code']})" for it in items]
            selected_item_str = st.selectbox("اختر الصنف", item_names)
            selected_id = None
            selected_data = None
            for it in items:
                if f"{it['name']} (كود: {it['item_code']})" == selected_item_str:
                    selected_id = it['id']; selected_data = it; break
            if selected_data:
                st.subheader("تعديل البيانات")
                new_name = st.text_input("الاسم", value=selected_data['name'])
                unit_options = [f"{u['unit_name']} ({u['unit_symbol']})" for u in units]
                current_unit_idx = [i for i,u in enumerate(units) if u['id']==selected_data['unit_id']][0]
                new_unit = st.selectbox("الوحدة", unit_options, index=current_unit_idx)
                new_min = st.number_input("الحد الأدنى",0.0,10000.0,float(selected_data['min_qty']))
                new_max = st.number_input("الحد الأقصى",0.0,10000.0,float(selected_data['max_qty']))
                active = st.checkbox("نشط", value=bool(selected_data['is_active']))
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("حفظ التعديلات"):
                        if new_name.strip() == "":
                            st.error("الاسم لا يمكن أن يكون فارغاً")
                        else:
                            unit_id = units[[u for u in units if f"{u['unit_name']} ({u['unit_symbol']})"==new_unit][0]]['id']
                            try:
                                conn.execute("UPDATE items SET name=?, unit_id=?, min_qty=?, max_qty=?, is_active=?, last_updated=? WHERE id=?",
                                             (new_name.strip(), unit_id, new_min, new_max, int(active), date.today().isoformat(), selected_id))
                                conn.commit()
                                st.success("تم الحفظ بنجاح")
                                st.rerun()
                            except sqlite3.IntegrityError:
                                st.error("اسم الصنف موجود مسبقاً!")
                with col2:
                    if st.button("حذف (تعطيل)"):
                        conn.execute("UPDATE items SET is_active=0, last_updated=? WHERE id=?", (date.today().isoformat(), selected_id))
                        conn.commit()
                        st.success("تم تعطيل الصنف")
                        st.rerun()

                st.divider()
                st.subheader("🗑️ حذف نهائي")
                st.warning("الحذف النهائي لا يمكن التراجع عنه!")
                if st.button("حذف الصنف نهائياً", key="perm_delete"):
                    trans_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE item_id=?", (selected_id,)).fetchone()[0]
                    if trans_count > 0:
                        st.error(f"لا يمكن حذف هذا الصنف نهائياً لوجود {trans_count} حركة مرتبطة به. يمكنك تعطيله بدلاً من ذلك.")
                    else:
                        confirm = st.checkbox("أؤكد أنني أرغب في حذف الصنف نهائياً", key="confirm_delete")
                        if confirm:
                            conn.execute("DELETE FROM expiry_alerts WHERE item_id=?", (selected_id,))
                            conn.execute("DELETE FROM inventory_counts WHERE item_id=?", (selected_id,))
                            conn.execute("DELETE FROM items WHERE id=?", (selected_id,))
                            conn.commit()
                            st.success("تم حذف الصنف نهائياً")
                            st.rerun()
                        else:
                            st.info("يرجى تأكيد الحذف أعلاه")
        else:
            st.info("لا توجد أصناف")
    conn.close()

elif choice == "📏 الوحدات":
    if not check_perm(): st.error("غير مصرح"); st.stop()
    st.header("وحدات القياس")
    conn = get_db()
    with st.form("add_unit"):
        un = st.text_input("اسم الوحدة")
        us = st.text_input("الرمز")
        if st.form_submit_button("إضافة"):
            if un:
                conn.execute("INSERT OR IGNORE INTO units (unit_name, unit_symbol) VALUES (?,?)",(un,us))
                conn.commit()
                st.success("تم الحفظ بنجاح")
                st.rerun()
    units = conn.execute("SELECT * FROM units").fetchall()
    if units:
        st.dataframe(pd.DataFrame(units, columns=['م','الوحدة','الرمز']))
    conn.close()

elif choice == "🏨 الفنادق":
    if not check_perm(): st.error("غير مصرح"); st.stop()
    st.header("الفنادق")
    conn = get_db()
    tab1, tab2 = st.tabs(["إضافة","تعديل"])
    with tab1:
        with st.form("add_hotel"):
            name = st.text_input("اسم الفندق")
            contact = st.text_input("الشخص المسؤول")
            phone = st.text_input("الهاتف")
            if st.form_submit_button("إضافة"):
                conn.execute("INSERT OR IGNORE INTO hotels (name,contact_person,phone) VALUES (?,?,?)",(name,contact,phone))
                conn.commit()
                st.success("تم الحفظ بنجاح")
                st.rerun()
    with tab2:
        hotels = conn.execute("SELECT * FROM hotels").fetchall()
        if hotels:
            hotel_names = [h['name'] for h in hotels]
            selected = st.selectbox("اختر الفندق", hotel_names)
            h = [h for h in hotels if h['name']==selected][0]
            new_name = st.text_input("الاسم الجديد", value=h['name'])
            new_contact = st.text_input("الشخص المسؤول", value=h['contact_person'] or "")
            new_phone = st.text_input("الهاتف", value=h['phone'] or "")
            if st.button("حفظ التعديلات"):
                if new_name and new_name != selected:
                    exists = conn.execute("SELECT id FROM hotels WHERE name=? AND id!=?",(new_name,h['id'])).fetchone()
                    if exists: st.error("الاسم موجود")
                    else:
                        conn.execute("UPDATE hotels SET name=?, contact_person=?, phone=? WHERE id=?",(new_name, new_contact, new_phone, h['id']))
                        conn.commit()
                        st.success("تم الحفظ بنجاح"); st.rerun()
                else:
                    conn.execute("UPDATE hotels SET name=?, contact_person=?, phone=? WHERE id=?",(new_name, new_contact, new_phone, h['id']))
                    conn.commit()
                    st.success("تم الحفظ بنجاح"); st.rerun()
        else: st.info("لا توجد فنادق")
    conn.close()

elif choice == "🏢 الموردين":
    if not check_perm(): st.error("غير مصرح"); st.stop()
    st.header("الموردين")
    conn = get_db()
    tab1, tab2 = st.tabs(["إضافة","تعديل"])
    with tab1:
        with st.form("add_sup"):
            name = st.text_input("اسم المورد")
            info = st.text_input("معلومات الاتصال")
            if st.form_submit_button("إضافة"):
                conn.execute("INSERT OR IGNORE INTO suppliers (supplier_name,contact_info) VALUES (?,?)",(name,info))
                conn.commit()
                st.success("تم الحفظ بنجاح"); st.rerun()
    with tab2:
        supps = conn.execute("SELECT * FROM suppliers").fetchall()
        if supps:
            supp_names = [s['supplier_name'] for s in supps]
            selected = st.selectbox("اختر المورد", supp_names)
            s = [s for s in supps if s['supplier_name']==selected][0]
            new_name = st.text_input("الاسم الجديد", value=s['supplier_name'])
            new_info = st.text_input("معلومات الاتصال", value=s['contact_info'] or "")
            if st.button("حفظ التعديلات"):
                if new_name and new_name != selected:
                    exists = conn.execute("SELECT id FROM suppliers WHERE supplier_name=? AND id!=?",(new_name,s['id'])).fetchone()
                    if exists: st.error("الاسم موجود")
                    else:
                        conn.execute("UPDATE suppliers SET supplier_name=?, contact_info=? WHERE id=?",(new_name, new_info, s['id']))
                        conn.commit(); st.success("تم الحفظ بنجاح"); st.rerun()
                else:
                    conn.execute("UPDATE suppliers SET supplier_name=?, contact_info=? WHERE id=?",(new_name, new_info, s['id']))
                    conn.commit(); st.success("تم الحفظ بنجاح"); st.rerun()
        else: st.info("لا يوجد موردين")
    conn.close()

elif choice == "📥 الوارد":
    st.header("المشتريات (وارد)")
    conn = get_db()
    items = conn.execute("SELECT id,name,unit_id FROM items WHERE is_active=1").fetchall()
    if items:
        with st.form("inward"):
            item = st.selectbox("الصنف", [i['name'] for i in items])
            qty = st.number_input("الكمية",0.1,100000.0,1.0)
            batch = st.text_input("رقم التشغيلة")
            exp_date = st.date_input("تاريخ انتهاء الصلاحية", date.today()+timedelta(days=365))
            invoice_date = st.date_input("تاريخ الفاتورة", value=date.today())
            notes = st.text_input("ملاحظات")
            uploaded_file = st.file_uploader("📎 إرفاق ملف", type=["png","jpg","jpeg","pdf"])
            if st.form_submit_button("تسجيل"):
                it = [i for i in items if i['name']==item][0]
                conn.execute("""INSERT INTO transactions (transaction_type,item_id,qty,unit_id,batch_number,expiry_date,transaction_date,notes,created_by)
                              VALUES (?,?,?,?,?,?,?,?,?)""",
                             ('وارد',it['id'],qty,it['unit_id'],batch,exp_date.isoformat(),invoice_date.isoformat(),notes,st.session_state.user['full_name']))
                trans_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                if uploaded_file:
                    att = save_attachment(uploaded_file, trans_id)
                    conn.execute("UPDATE transactions SET attachment=? WHERE id=?", (att, trans_id))
                conn.execute("UPDATE items SET current_balance=current_balance+?, last_updated=? WHERE id=?",(qty,date.today().isoformat(),it['id']))
                if exp_date:
                    conn.execute("INSERT INTO expiry_alerts (item_id,batch_number,expiry_date,qty_remaining) VALUES (?,?,?,?)",(it['id'],batch,exp_date.isoformat(),qty))
                conn.commit()
                st.success(f"تم الحفظ بنجاح (تاريخ الفاتورة: {invoice_date.isoformat()})")
                st.rerun()
    conn.close()

elif choice == "📤 الصادر":
    st.header("صرف مستلزمات للفنادق")
    conn = get_db()
    items = conn.execute("SELECT id, name, current_balance, unit_id FROM items WHERE is_active=1").fetchall()
    hotels = conn.execute("SELECT id, name FROM hotels").fetchall()
    if not items or not hotels:
        st.warning("يجب إضافة أصناف وفنادق أولاً")
    else:
        item_options = [f"{it['name']} (الرصيد: {it['current_balance']})" for it in items]
        if 'outward_items' not in st.session_state:
            st.session_state.outward_items = []

        st.subheader("إضافة أصناف للإذن")
        col1, col2 = st.columns(2)
        with col1:
            selected_item_str = st.selectbox("الصنف", item_options, key="item_select")
        with col2:
            qty = st.number_input("الكمية", min_value=0.1, value=1.0, step=0.1, key="qty_input")

        if st.button("➕ أضف إلى الإذن"):
            if qty <= 0:
                st.error("الكمية يجب أن تكون أكبر من صفر")
            else:
                item_name = selected_item_str.split(" (الرصيد:")[0]
                it = next((i for i in items if i['name'] == item_name), None)
                if it:
                    if qty > it['current_balance']:
                        st.error(f"الرصيد غير كافٍ ({it['current_balance']})")
                    else:
                        st.session_state.outward_items.append({
                            'item_id': it['id'],
                            'item_name': it['name'],
                            'qty': qty,
                            'unit_id': it['unit_id']
                        })
                        st.success(f"تمت إضافة {item_name} ({qty})")
                        st.rerun()

        if st.session_state.outward_items:
            st.subheader("الأصناف في الإذن الحالي")
            df_current = pd.DataFrame(st.session_state.outward_items)
            units = conn.execute("SELECT id, unit_symbol FROM units").fetchall()
            unit_dict = {u['id']: u['unit_symbol'] for u in units}
            df_current['الوحدة'] = df_current['unit_id'].map(unit_dict)
            df_display = df_current[['item_name', 'qty', 'الوحدة']].copy()
            df_display.columns = ['الصنف', 'الكمية', 'الوحدة']
            st.dataframe(df_display, use_container_width=True)

            if st.button("🗑️ مسح القائمة"):
                st.session_state.outward_items = []
                st.rerun()

            st.divider()
            st.subheader("بيانات الإذن")
            col_order1, col_order2 = st.columns(2)
            with col_order1:
                hotel = st.selectbox("الفندق", [h['name'] for h in hotels], key="hotel_select")
                recipient = st.text_input("اسم مسؤول الاستلام (للتوقيع)", key="recipient")
            with col_order2:
                order_date = st.date_input("تاريخ الإذن", value=date.today(), key="order_date")
            notes = st.text_area("ملاحظات الإذن", key="notes")

            if st.button("✅ تأكيد الصرف وإنشاء الإذن", type="primary"):
                if not recipient:
                    st.error("يرجى إدخال اسم مسؤول الاستلام")
                elif len(st.session_state.outward_items) == 0:
                    st.error("لم تتم إضافة أي صنف")
                else:
                    valid = True
                    for item_entry in st.session_state.outward_items:
                        it = conn.execute("SELECT current_balance FROM items WHERE id=?", (item_entry['item_id'],)).fetchone()
                        if it['current_balance'] < item_entry['qty']:
                            st.error(f"الرصيد غير كافٍ للصنف {item_entry['item_name']}")
                            valid = False
                            break
                    if valid:
                        order_number = generate_outward_order_number()
                        hotel_id = [h['id'] for h in hotels if h['name'] == hotel][0]
                        conn.execute("""INSERT INTO outward_orders (order_number, hotel_id, recipient_name, order_date, notes, created_by)
                                      VALUES (?,?,?,?,?,?)""",
                                     (order_number, hotel_id, recipient, order_date.isoformat(), notes, st.session_state.user['full_name']))
                        order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                        for item_entry in st.session_state.outward_items:
                            conn.execute("""INSERT INTO transactions (transaction_type, item_id, hotel_id, qty, unit_id, transaction_date, notes, created_by, order_id)
                                          VALUES (?,?,?,?,?,?,?,?,?)""",
                                         ('صادر', item_entry['item_id'], hotel_id, item_entry['qty'], item_entry['unit_id'],
                                          order_date.isoformat(), f"إذن رقم {order_number}", st.session_state.user['full_name'], order_id))
                            conn.execute("UPDATE items SET current_balance = current_balance - ?, last_updated=? WHERE id=?",
                                         (item_entry['qty'], date.today().isoformat(), item_entry['item_id']))

                        conn.commit()
                        st.success(f"تم الحفظ بنجاح (تاريخ الإذن: {order_date.isoformat()})")

                        pdf_items = []
                        for item_entry in st.session_state.outward_items:
                            unit_symbol = unit_dict.get(item_entry['unit_id'], '')
                            pdf_items.append([item_entry['item_name'], str(item_entry['qty']), unit_symbol])
                        font_path = get_arabic_font()
                        pdf = FPDF()
                        pdf.add_page()
                        if font_path:
                            pdf.add_font("Amiri", fname=font_path)
                            pdf.set_font("Amiri", size=16)
                        else:
                            pdf.set_font("Helvetica", size=16)
                        pdf.cell(0, 10, shape_arabic("إذن صرف مخزني"), ln=True, align='C')
                        pdf.ln(5)
                        pdf.set_font("Amiri", size=12) if font_path else pdf.set_font("Helvetica", size=12)
                        pdf.cell(0, 8, shape_arabic(f"رقم الإذن: {order_number}"), ln=True, align='R')
                        pdf.cell(0, 8, shape_arabic(f"التاريخ: {order_date.isoformat()}"), ln=True, align='R')
                        pdf.cell(0, 8, shape_arabic(f"الفندق: {hotel}"), ln=True, align='R')
                        pdf.cell(0, 8, shape_arabic(f"مسؤول الاستلام: {recipient}"), ln=True, align='R')
                        pdf.ln(5)
                        pdf.set_fill_color(0,168,107); pdf.set_text_color(255,255,255)
                        pdf.cell(30, 10, shape_arabic("الوحدة"), border=1, fill=True, align='C')
                        pdf.cell(30, 10, shape_arabic("الكمية"), border=1, fill=True, align='C')
                        pdf.cell(100, 10, shape_arabic("الصنف"), border=1, fill=True, align='C')
                        pdf.ln()
                        pdf.set_text_color(0,0,0)
                        pdf.set_font("Amiri", size=10) if font_path else pdf.set_font("Helvetica", size=10)
                        for row in pdf_items:
                            pdf.cell(30, 8, shape_arabic(row[2]), border=1, align='C')
                            pdf.cell(30, 8, shape_arabic(row[1]), border=1, align='C')
                            pdf.cell(100, 8, shape_arabic(row[0]), border=1, align='C')
                            pdf.ln()
                        pdf.ln(10)
                        pdf.cell(0, 10, shape_arabic("توقيع مسؤول الاستلام: ________________"), ln=True, align='R')
                        pdf.cell(0, 10, shape_arabic("توقيع أمين المخزن: ________________"), ln=True, align='R')

                        pdf_bytes = bytes(pdf.output())
                        st.download_button("📄 تحميل إذن الصرف PDF", data=pdf_bytes,
                                           file_name=f"{order_number}.pdf", mime="application/pdf")

                        st.session_state.outward_items = []
                        st.rerun()
    conn.close()

elif choice == "📝 الجرد":
    st.header("الجرد الدوري")
    conn = get_db()
    items = conn.execute("SELECT id,name,current_balance,unit_id FROM items WHERE is_active=1").fetchall()
    if items:
        item = st.selectbox("الصنف", [i['name'] for i in items])
        it = [i for i in items if i['name']==item][0]
        st.info(f"الرصيد المسجل: {it['current_balance']}")
        actual = st.number_input("الكمية الفعلية",0.0, value=float(it['current_balance']))
        notes = st.text_input("ملاحظات")
        if st.button("حفظ الجرد"):
            diff = actual - it['current_balance']
            if diff != 0:
                conn.execute("INSERT INTO transactions (transaction_type,item_id,qty,unit_id,transaction_date,notes,created_by) VALUES (?,?,?,?,?,?,?)",
                             ('تسوية إضافة' if diff>0 else 'تسوية عجز', it['id'], abs(diff), it['unit_id'], date.today().isoformat(), notes, st.session_state.user['full_name']))
            conn.execute("UPDATE items SET current_balance=?, last_updated=? WHERE id=?",(actual,date.today().isoformat(),it['id']))
            conn.execute("INSERT INTO inventory_counts (count_date,item_id,expected_qty,actual_qty,difference,notes,counted_by) VALUES (?,?,?,?,?,?,?)",
                         (date.today().isoformat(),it['id'],it['current_balance'],actual,diff,notes,st.session_state.user['full_name']))
            conn.commit()
            st.success("تم الحفظ بنجاح")
            st.rerun()
    conn.close()

elif choice == "📈 التقارير":
    st.header("التقارير")
    conn = get_db()
    tab1, tab2 = st.tabs(["حركات", "أرصدة"])
    with tab1:
        st.subheader("تقرير الحركات")
        col1, col2, col3 = st.columns(3)
        with col1: d1 = st.date_input("من", date.today()-timedelta(days=30))
        with col2: d2 = st.date_input("إلى", date.today())
        with col3: typ = st.selectbox("النوع",["الكل","وارد","صادر","تسوية إضافة","تسوية عجز"])
        hotels = conn.execute("SELECT id, name FROM hotels").fetchall()
        hotel_names = ["الكل"] + [h['name'] for h in hotels]
        selected_hotel = st.selectbox("الفندق", hotel_names)
        with st.expander("🎨 تنسيق الجدول"):
            font_scale = st.slider("حجم الخط (%)", 50, 200, 100, step=10, key="report_font")
            color_option = st.selectbox("لون الجدول", ["افتراضي","أخضر","أزرق","رمادي","برتقالي"], key="report_color")
            color_map = {"افتراضي":"#f0f2f6","أخضر":"#e6ffe6","أزرق":"#e6f0ff","رمادي":"#f5f5f5","برتقالي":"#fff3e6"}
            bg_color = color_map.get(color_option, "#f0f2f6")

            all_columns = ['رقم الحركة','التاريخ','الصنف','النوع','الكمية','الوحدة','الفندق','ملاحظات','مرفق']
            if 'selected_columns_order' not in st.session_state:
                st.session_state.selected_columns_order = ['رقم الحركة','التاريخ','الصنف','النوع','الكمية','الوحدة','الفندق','ملاحظات','مرفق']
            new_order = st.multiselect("اختر الأعمدة ورتبها", options=all_columns, default=st.session_state.selected_columns_order, key="columns_order")
            if new_order != st.session_state.selected_columns_order:
                st.session_state.selected_columns_order = new_order
                st.rerun()

        query = """
            SELECT t.id, t.transaction_date, i.name AS item_name, t.transaction_type, t.qty, u.unit_symbol,
                   COALESCE(h.name, '-') AS hotel_name, t.notes, t.attachment
            FROM transactions t
            JOIN items i ON t.item_id = i.id
            LEFT JOIN hotels h ON t.hotel_id = h.id
            LEFT JOIN units u ON t.unit_id = u.id
            WHERE t.transaction_date BETWEEN ? AND ?
        """
        params = [d1.isoformat(), d2.isoformat()]
        if typ != "الكل": query += " AND t.transaction_type = ?"; params.append(typ)
        if selected_hotel != "الكل":
            hotel_id = [h['id'] for h in hotels if h['name']==selected_hotel][0]
            query += " AND t.hotel_id = ?"; params.append(hotel_id)
        query += " ORDER BY t.id DESC"
        data = conn.execute(query, params).fetchall()
        if data:
            df = pd.DataFrame(data, columns=['رقم الحركة','التاريخ','الصنف','النوع','الكمية','الوحدة','الفندق','ملاحظات','مرفق'])
            def attachment_link(fname):
                if fname:
                    path = os.path.join(ATTACHMENTS_FOLDER, fname)
                    if os.path.exists(path):
                        with open(path,"rb") as f:
                            b64 = base64.b64encode(f.read()).decode()
                        return f'<a href="data:application/octet-stream;base64,{b64}" download="{fname}">📎 تحميل</a>'
                return ""
            df['مرفق'] = df['مرفق'].apply(attachment_link)
            ordered_columns = [col for col in st.session_state.selected_columns_order if col in df.columns]
            remaining = [col for col in df.columns if col not in ordered_columns]
            df_display = df[ordered_columns + remaining]
            st.dataframe(df_display, use_container_width=True)
            st.markdown(f"""<style>
                div[data-testid="stDataFrame"] div[data-testid="stTable"] {{ font-size: {font_scale}% !important; }}
                div[data-testid="stDataFrame"] table {{ background-color: {bg_color} !important; }}
            </style>""", unsafe_allow_html=True)
            export_df = df.drop(columns=['مرفق'], errors='ignore')
            export_df = export_df[[col for col in ordered_columns if col in export_df.columns]]
            export_buttons(export_df, "حركات", "تقرير الحركات")
        else:
            st.info("لا توجد حركات")
    with tab2:
        st.subheader("تقرير الأرصدة")
        items = conn.execute("SELECT i.item_code, i.name, i.current_balance, u.unit_symbol FROM items i LEFT JOIN units u ON i.unit_id=u.id WHERE i.is_active=1").fetchall()
        if items:
            df = pd.DataFrame(items, columns=['كود','الصنف','الرصيد','الوحدة'])
            st.dataframe(df, use_container_width=True)
            export_buttons(df, "ارصدة", "تقرير الأرصدة")
        else:
            st.info("لا توجد أصناف نشطة")
    conn.close()

elif choice == "🗑️ إدارة الحركات (حذف)":
    if not has_role('super_admin'): st.error("فقط المدير العام"); st.stop()
    st.header("حذف حركة")
    conn = get_db()
    trans = conn.execute("""SELECT t.id, t.transaction_type, i.name, COALESCE(h.name,'-'), t.qty, t.transaction_date, t.notes
                           FROM transactions t JOIN items i ON t.item_id=i.id LEFT JOIN hotels h ON t.hotel_id=h.id
                           ORDER BY t.id DESC LIMIT 50""").fetchall()
    if trans:
        df = pd.DataFrame(trans, columns=['رقم','النوع','الصنف','الفندق','الكمية','التاريخ','ملاحظات'])
        st.dataframe(df)
        trans_id = st.number_input("أدخل رقم الحركة للحذف", min_value=1, step=1)
        if st.button("حذف الحركة واسترجاع تأثيرها"):
            ok, msg = delete_transaction(trans_id)
            if ok: st.success(msg); st.rerun()
            else: st.error(msg)
    else:
        st.info("لا توجد حركات")
    conn.close()

elif choice == "💾 النسخ الاحتياطي":
    st.header("النسخ الاحتياطي")
    notes = st.text_input("ملاحظات")
    if st.button("إنشاء نسخة"):
        ok, path, msg = create_backup("يدوي", notes)
        if ok:
            st.success(msg)
            with open(path,"rb") as f: st.download_button("تحميل النسخة", f, file_name=os.path.basename(path))
    st.subheader("استعادة نسخة")
    up = st.file_uploader("اختر ملف zip", type="zip")
    if up:
        tmp = f"tmp_{datetime.now().timestamp()}.zip"
        with open(tmp,"wb") as f: f.write(up.read())
        if st.button("استعادة"):
            ok, msg = restore_backup(tmp)
            if ok: st.success(msg); st.rerun()
            else: st.error(msg)

    st.divider()
    st.subheader("☁️ مزامنة مع Google Drive")
    if st.button("📤 رفع قاعدة البيانات الآن إلى Drive"):
        if upload_db_to_drive():
            st.success("✅ تم رفع قاعدة البيانات بنجاح إلى Google Drive")
        else:
            st.error("❌ فشل الرفع. تأكد من إعدادات secrets.")

elif choice == "👥 المستخدمين":
    if not has_role('super_admin'): st.error("غير مصرح"); st.stop()
    st.header("المستخدمين")
    conn = get_db()
    users = conn.execute("SELECT username, role, full_name FROM users").fetchall()
    st.dataframe(pd.DataFrame(users, columns=['مستخدم','دور','اسم']))
    with st.form("add_user"):
        un = st.text_input("اسم المستخدم")
        pw = st.text_input("كلمة المرور", type="password")
        fn = st.text_input("الاسم الكامل")
        role = st.selectbox("الدور", ['super_admin','purchasing','disbursement','supervisor'])
        if st.form_submit_button("إضافة"):
            try:
                conn.execute("INSERT INTO users (username,password,role,full_name) VALUES (?,?,?,?)",(un, hash_password(pw), role, fn))
                conn.commit()
                st.success("تم الحفظ بنجاح")
                st.rerun()
            except: st.error("مستخدم موجود")
    conn.close()