import streamlit as st
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
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
from contextlib import contextmanager

# ======================== إعدادات الصفحة ========================
st.set_page_config(page_title="مخزن النظافة", layout="wide", initial_sidebar_state="collapsed")

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

def apply_theme():
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap');
    *{{font-family:'Tajawal',sans-serif}}
    html,body,[class*="css"]{{direction:rtl;text-align:right;font-size:{st.session_state.font_size}% !important}}
    .stApp {{
        background-color: {st.session_state.theme_color} !important;
        background-image: linear-gradient(135deg, {st.session_state.theme_color} 0%, #ffffff 100%) !important;
    }}
    .stock-critical{{background-color:#ff4444;color:white;padding:5px 10px;border-radius:5px}}
    .stock-warning{{background-color:#ffbb33;color:black;padding:5px 10px;border-radius:5px}}
    .stock-good{{background-color:#00C851;color:white;padding:5px 10px;border-radius:5px}}
    </style>""", unsafe_allow_html=True)

apply_theme()

# ======================== الاتصال بقاعدة بيانات Supabase (PostgreSQL) ========================
DEFAULT_DB_URL = "postgresql://postgres:Ameen_Ali_1919@db.krrbpyleyvcmshcqcdog.supabase.co:5432/postgres"

def get_db_url():
    if "postgres" in st.secrets and "db_url" in st.secrets["postgres"]:
        return st.secrets["postgres"]["db_url"]
    return DEFAULT_DB_URL

DB_URL = get_db_url()

@st.cache_resource
def init_connection_pool():
    return SimpleConnectionPool(1, 10, dsn=DB_URL)

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
CONFIG_FILE = 'backup_config.json'
LOGO_FILE = 'logo.png'

if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)
if not os.path.exists(ATTACHMENTS_FOLDER):
    os.makedirs(ATTACHMENTS_FOLDER)

# ======================== دوال مساعدة ========================
def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

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
                role TEXT NOT NULL CHECK (role IN ('super_admin', 'purchasing', 'disbursement', 'supervisor')),
                full_name TEXT,
                is_active BOOLEAN DEFAULT TRUE
            )''')

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
                c.execute("INSERT INTO users (username,password,role,full_name) VALUES (%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING",(uname,pwd,role,fname))

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
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            today_str = date.today().strftime("%Y%m%d")
            c.execute("SELECT order_number FROM outward_orders WHERE order_number LIKE %s ORDER BY id DESC LIMIT 1",
                      (f"OUT-{today_str}-%",))
            last = c.fetchone()
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
        
        with get_db() as conn:
            with pd.ExcelWriter(os.path.join(path,'preview.xlsx'), engine='xlsxwriter') as w:
                for t in ['items','hotels','transactions','units','suppliers','users']:
                    try: pd.read_sql_query(f"SELECT * FROM {t}", conn).to_excel(w, sheet_name=t, index=False)
                    except: pass
                    
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

def delete_transaction(trans_id):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM transactions WHERE id=%s", (trans_id,))
            trans = c.fetchone()
            if not trans:
                return False, "الحركة غير موجودة"
            item_id = trans['item_id']
            qty = trans['qty']
            typ = trans['transaction_type']
            if typ == 'وارد' or typ == 'تسوية إضافة':
                c.execute("UPDATE items SET current_balance = current_balance - %s, last_updated=%s WHERE id=%s", (qty, date.today().isoformat(), item_id))
            elif typ == 'صادر' or typ == 'تسوية عجز':
                c.execute("UPDATE items SET current_balance = current_balance + %s, last_updated=%s WHERE id=%s", (qty, date.today().isoformat(), item_id))
            c.execute("DELETE FROM transactions WHERE id=%s", (trans_id,))
    return True, "تم حذف الحركة بنجاح"

def delete_outward_order(order_id):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT item_id, qty FROM transactions WHERE order_id=%s AND transaction_type='صادر'", (order_id,))
            trans_items = c.fetchall()
            for t in trans_items:
                c.execute("UPDATE items SET current_balance = current_balance + %s, last_updated=%s WHERE id=%s", (t['qty'], date.today().isoformat(), t['item_id']))
            c.execute("DELETE FROM transactions WHERE order_id=%s", (order_id,))
            c.execute("DELETE FROM outward_orders WHERE id=%s", (order_id,))
    return True, "تم حذف الإذن وإعادة الكميات إلى المخزون"

def save_attachment(uploaded_file, transaction_id):
    if uploaded_file is None: return None
    file_ext = os.path.splitext(uploaded_file.name)[1]
    safe_name = f"trans_{transaction_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}{file_ext}"
    file_path = os.path.join(ATTACHMENTS_FOLDER, safe_name)
    with open(file_path, "wb") as f: f.write(uploaded_file.getbuffer())
    return safe_name

# ======================== دالة إعادة حساب الأرصدة ========================
def recalculate_all_balances():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id FROM items")
            items = c.fetchall()
            for item in items:
                c.execute("SELECT COALESCE(SUM(qty),0) FROM transactions WHERE item_id=%s AND transaction_type IN ('وارد','تسوية إضافة')", (item['id'],))
                total_in = c.fetchone()['coalesce']
                c.execute("SELECT COALESCE(SUM(qty),0) FROM transactions WHERE item_id=%s AND transaction_type IN ('صادر','تسوية عجز')", (item['id'],))
                total_out = c.fetchone()['coalesce']
                new_balance = total_in - total_out
                c.execute("UPDATE items SET current_balance = %s, last_updated = %s WHERE id = %s", (new_balance, date.today().isoformat(), item['id']))
    return True

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
        if st.form_submit_button("دخول"):
            if login(uname, pwd):
                st.success("تم الدخول"); st.rerun()
            else: st.error("خطأ في بيانات الدخول")
    st.stop()

# ======================== الواجهة الرئيسية ========================
st.title(f"🧹 {st.session_state.store_name}")
if st.session_state.logo_path and os.path.exists(st.session_state.logo_path):
    st.image(st.session_state.logo_path, width=150)
st.write(f"مرحباً {st.session_state.user['full_name']} ({st.session_state.user['role']})")
if st.button("تسجيل الخروج"):
    logout()

with st.expander("⚙️ الإعدادات", expanded=False):
    new_font_size = st.slider("حجم الخط (%)", 50, 200, st.session_state.font_size, step=10, key="global_font")
    theme_color = st.color_picker("لون البرنامج", st.session_state.theme_color, key="global_theme")
    new_store_name = st.text_input("اسم المستودع", value=st.session_state.store_name, key="store_name_input")
    if st.button("تحديث الاسم", key="update_name"):
        if new_store_name.strip():
            st.session_state.store_name = new_store_name.strip()
            st.success("✅ تم تحديث اسم المستودع")
            save_app_config({
                'font_size': st.session_state.font_size,
                'theme_color': st.session_state.theme_color,
                'logo_path': st.session_state.logo_path,
                'store_name': st.session_state.store_name
            })
            st.rerun()
        else:
            st.error("الاسم لا يمكن أن يكون فارغاً")
    uploaded_logo = st.file_uploader("📷 رفع شعار", type=["png","jpg","jpeg"], key="logo_uploader")
    if uploaded_logo is not None:
        with open(LOGO_FILE, "wb") as f:
            f.write(uploaded_logo.getbuffer())
        st.session_state.logo_path = LOGO_FILE
        st.success("✅ تم رفع الشعار بنجاح")
        save_app_config({
            'font_size': st.session_state.font_size,
            'theme_color': st.session_state.theme_color,
            'logo_path': st.session_state.logo_path,
            'store_name': st.session_state.store_name
        })
        st.rerun()
    if st.session_state.logo_path and os.path.exists(st.session_state.logo_path):
        if st.button("🗑️ مسح الشعار"):
            os.remove(st.session_state.logo_path)
            st.session_state.logo_path = None
            st.success("تم مسح الشعار")
            save_app_config({
                'font_size': st.session_state.font_size,
                'theme_color': st.session_state.theme_color,
                'logo_path': st.session_state.logo_path,
                'store_name': st.session_state.store_name
            })
            st.rerun()
    if new_font_size != st.session_state.font_size or theme_color != st.session_state.theme_color:
        st.session_state.font_size = new_font_size
        st.session_state.theme_color = theme_color
        save_app_config({
            'font_size': st.session_state.font_size,
            'theme_color': st.session_state.theme_color,
            'logo_path': st.session_state.logo_path,
            'store_name': st.session_state.store_name
        })
        st.rerun()

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

choice = st.selectbox("القائمة", menu, index=0)

# ======================== دوال مساعدة للجداول القابلة للتخصيص ========================
def apply_table_styling(font_scale, bg_color):
    return f"""<style>
        div[data-testid="stDataFrame"] div[data-testid="stTable"] {{ font-size: {font_scale}% !important; }}
        div[data-testid="stDataFrame"] table {{ background-color: {bg_color} !important; }}
    </style>"""

def column_selector(label, all_columns, default_order, key):
    if key not in st.session_state:
        st.session_state[key] = default_order
    new_order = st.multiselect(label, options=all_columns, default=st.session_state[key], key=key+"_multiselect")
    if new_order != st.session_state[key]:
        st.session_state[key] = new_order
        st.rerun()
    return st.session_state[key]

# ======================== الصفحات ========================
if choice == "📊 لوحة التحكم":
    st.header("لوحة التحكم")
    today = date.today()
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT COUNT(*) FROM items WHERE is_active=TRUE")
            total = c.fetchone()['count']
            c.execute("SELECT COUNT(*) FROM items WHERE current_balance<=min_qty AND is_active=TRUE")
            low = c.fetchone()['count']
            c.execute("SELECT COUNT(*) FROM expiry_alerts WHERE is_consumed=FALSE AND expiry_date<%s",(today.isoformat(),))
            exp = c.fetchone()['count']
            
            c1,c2,c3 = st.columns(3)
            c1.metric("الأصناف", total); c2.metric("تحت الحد", low); c3.metric("منتهية الصلاحية", exp)
            st.divider()
            
            c.execute("SELECT i.item_code, i.name, i.current_balance, i.min_qty, u.unit_symbol FROM items i LEFT JOIN units u ON i.unit_id=u.id WHERE i.current_balance<=i.min_qty AND i.is_active=TRUE")
            low_items = c.fetchall()
            
    if low_items:
        df = pd.DataFrame(low_items)
        df.columns = ['كود','الصنف','الرصيد','الحد الأدنى','الوحدة']
        with st.expander("🎨 تنسيق جدول التنبيهات"):
            font_scale = st.slider("حجم الخط (%)", 50,200,100,10, key="dash_font")
            color_option = st.selectbox("لون الجدول", ["افتراضي","أخضر","أزرق","رمادي","برتقالي"], key="dash_color")
            color_map = {"افتراضي":"#f0f2f6","أخضر":"#e6ffe6","أزرق":"#e6f0ff","رمادي":"#f5f5f5","برتقالي":"#fff3e6"}
            bg = color_map.get(color_option,"#f0f2f6")
            cols = column_selector("اختر الأعمدة ورتبها", list(df.columns), list(df.columns), "dash_cols")
        df_disp = df[cols]
        st.dataframe(df_disp, use_container_width=True)
        st.markdown(apply_table_styling(font_scale, bg), unsafe_allow_html=True)
        export_buttons(df_disp, "اصناف_منخفضة", "تقرير الأصناف أقل من الحد الأدنى")

elif choice == "📦 إدارة الأصناف":
    if not check_perm(): st.error("غير مصرح"); st.stop()
    st.header("إدارة الأصناف")

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT id, unit_name, unit_symbol FROM units")
            units = c.fetchall()
            unit_options = [f"{u['unit_name']} ({u['unit_symbol']})" for u in units]
            unit_dict = {opt: u['id'] for opt, u in zip(unit_options, units)}
            unit_id_to_text = {u['id']: f"{u['unit_name']} ({u['unit_symbol']})" for u in units}

            show_inactive = st.checkbox("إظهار الأصناف غير النشطة", value=False)
            condition = "" if show_inactive else "WHERE is_active = TRUE"

            c.execute(f"SELECT id, item_code, name, unit_id, current_balance, min_qty, max_qty, is_active, notes FROM items {condition} ORDER BY name")
            items = c.fetchall()

    data = []
    for it in items:
        data.append({
            "id": it["id"],
            "item_code": it["item_code"],
            "name": it["name"],
            "unit_text": unit_id_to_text.get(it["unit_id"], unit_options[0] if unit_options else ""),
            "current_balance": it["current_balance"],
            "min_qty": it["min_qty"],
            "max_qty": it["max_qty"],
            "is_active": bool(it["is_active"]),
            "notes": it["notes"],
            "delete": False
        })

    df = pd.DataFrame(data)

    if 'edited_df' not in st.session_state:
        st.session_state.edited_df = df.copy()

    edited_df = st.data_editor(
        df,
        column_config={
            "id": st.column_config.NumberColumn("المعرف", disabled=True),
            "item_code": st.column_config.TextColumn("الكود", disabled=True),
            "name": st.column_config.TextColumn("اسم الصنف", required=True),
            "unit_text": st.column_config.SelectboxColumn("الوحدة", options=unit_options),
            "current_balance": st.column_config.NumberColumn("الرصيد الحالي", disabled=True),
            "min_qty": st.column_config.NumberColumn("الحد الأدنى", min_value=0.0, step=0.1),
            "max_qty": st.column_config.NumberColumn("الحد الأقصى", min_value=0.0, step=0.1),
            "is_active": st.column_config.CheckboxColumn("نشط"),
            "notes": st.column_config.TextColumn("ملاحظات"),
            "delete": st.column_config.CheckboxColumn("حذف")
        },
        disabled=["id", "item_code", "current_balance"],
        hide_index=True,
        num_rows="dynamic",
        key="items_editor"
    )

    st.session_state.edited_df = edited_df.copy()

    col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)
    with col_btn1:
        if st.button("💾 حفظ جميع التعديلات", type="primary"):
            with get_db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as c:
                    ids_to_delete = edited_df[edited_df["delete"] == True]["id"].dropna().astype(int).tolist()
                    for item_id in ids_to_delete:
                        c.execute("SELECT COUNT(*) FROM transactions WHERE item_id=%s", (item_id,))
                        trans_count = c.fetchone()['count']
                        if trans_count > 0:
                            st.warning(f"الصنف رقم {item_id} لا يمكن حذفه لوجود حركات مرتبطة.")
                        else:
                            c.execute("DELETE FROM expiry_alerts WHERE item_id=%s", (item_id,))
                            c.execute("DELETE FROM inventory_counts WHERE item_id=%s", (item_id,))
                            c.execute("DELETE FROM items WHERE id=%s", (item_id,))

                    for _, row in edited_df.iterrows():
                        if pd.isna(row["id"]):
                            if pd.isna(row["name"]) or str(row["name"]).strip() == "":
                                continue
                            c.execute("SELECT id FROM items WHERE name=%s AND is_active=TRUE", (row["name"].strip(),))
                            if c.fetchone():
                                st.warning(f"الصنف '{row['name']}' موجود مسبقاً، تم تخطيه.")
                                continue
                            unit_id = unit_dict.get(row["unit_text"], units[0]["id"])
                            code = f"ITM-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                            c.execute("INSERT INTO items (item_code, name, unit_id, min_qty, max_qty, current_balance, is_active, notes, created_date, last_updated) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                      (code, row["name"].strip(), unit_id, row["min_qty"], row["max_qty"],
                                       0.0, bool(row["is_active"]), row.get("notes", ""),
                                       date.today().isoformat(), date.today().isoformat()))
                        else:
                            item_id = int(row["id"])
                            unit_id = unit_dict.get(row["unit_text"], units[0]["id"])
                            new_name = row["name"].strip() if pd.notna(row["name"]) else ""
                            if new_name == "":
                                continue
                            c.execute("SELECT id FROM items WHERE name=%s AND id!=%s AND is_active=TRUE", (new_name, item_id))
                            if c.fetchone():
                                st.warning(f"الاسم '{new_name}' موجود بالفعل لصنف آخر، لم يتم تحديث الصنف {item_id}.")
                                continue
                            c.execute("""UPDATE items SET name=%s, unit_id=%s, min_qty=%s, max_qty=%s, is_active=%s, notes=%s, last_updated=%s
                                          WHERE id=%s""",
                                      (new_name, unit_id, row["min_qty"], row["max_qty"], bool(row["is_active"]), row.get("notes", ""), date.today().isoformat(), item_id))
            st.success("✅ تم حفظ التعديلات بنجاح")
            st.rerun()