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

# ======================== إعدادات الصفحة ========================
st.set_page_config(page_title="مخزن النظافة", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap');
*{font-family:'Tajawal',sans-serif}
html,body,[class*="css"]{direction:rtl;text-align:right}
.stock-critical{background-color:#ff4444;color:white;padding:5px 10px;border-radius:5px}
.stock-warning{background-color:#ffbb33;color:black;padding:5px 10px;border-radius:5px}
.stock-good{background-color:#00C851;color:white;padding:5px 10px;border-radius:5px}
</style>""", unsafe_allow_html=True)

DB_NAME = 'cleaning_inventory.db'
BACKUP_FOLDER = 'backups'
CONFIG_FILE = 'backup_config.json'
if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)

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
    # جداول أساسية
    c.execute('''CREATE TABLE IF NOT EXISTS units (id INTEGER PRIMARY KEY AUTOINCREMENT, unit_name TEXT UNIQUE, unit_symbol TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, category_name TEXT UNIQUE, description TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS storage_locations (id INTEGER PRIMARY KEY AUTOINCREMENT, location_name TEXT UNIQUE, description TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_name TEXT UNIQUE, contact_info TEXT, notes TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, item_code TEXT UNIQUE, name TEXT, category_id INTEGER, unit_id INTEGER,
                 min_qty REAL DEFAULT 0, max_qty REAL DEFAULT 100, current_balance REAL DEFAULT 0, storage_location_id INTEGER, primary_supplier_id INTEGER,
                 shelf_life_days INTEGER, notes TEXT, is_active BOOLEAN DEFAULT 1, created_date TEXT, last_updated TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS hotels (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, contact_person TEXT, phone TEXT, notes TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_type TEXT, item_id INTEGER, hotel_id INTEGER,
                 qty REAL, unit_id INTEGER, batch_number TEXT, expiry_date TEXT, transaction_date TEXT, notes TEXT, created_by TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory_counts (id INTEGER PRIMARY KEY AUTOINCREMENT, count_date TEXT, item_id INTEGER, expected_qty REAL,
                 actual_qty REAL, difference REAL, notes TEXT, counted_by TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS expiry_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, batch_number TEXT, expiry_date TEXT,
                 qty_remaining REAL, is_consumed BOOLEAN DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT,
                 full_name TEXT, is_active BOOLEAN DEFAULT 1)''')
    # بيانات أولية
    for u_name, u_sym in [('قطعة','قطعة'),('لتر','لتر'),('كيلو','كجم'),('متر','متر'),('كرتونة','كرتونة'),('رول','رول'),('زجاجة','زجاجة')]:
        c.execute("INSERT OR IGNORE INTO units (unit_name, unit_symbol) VALUES (?,?)",(u_name,u_sym))
    for cat_name,desc in [('منظفات سائلة',''),('منظفات بودرة',''),('أدوات تنظيف',''),('معدات',''),('مستهلكات ورقية',''),('أكياس ومفارش',''),('مواد تعقيم',''),('أدوات سلامة','')]:
        c.execute("INSERT OR IGNORE INTO categories (category_name, description) VALUES (?,?)",(cat_name,desc))
    for loc_name,desc in [('المخزن الرئيسي',''),('رف السوائل',''),('رف المعدات',''),('رف الورقيات',''),('خزانة المواد الخطرة','')]:
        c.execute("INSERT OR IGNORE INTO storage_locations (location_name, description) VALUES (?,?)",(loc_name,desc))
    # المستخدمين الافتراضيين
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
    user = conn.execute("SELECT * FROM users WHERE username=? AND password=? AND is_active=1",(username,hash_password(password))).fetchone()
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

# ======================== دوال PDF بالعربية ========================
def get_arabic_font():
    path = "Amiri-Regular.ttf"
    if not os.path.exists(path):
        try:
            urllib.request.urlretrieve("https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf", path)
        except: pass
    return path if os.path.exists(path) else None

def reverse_arabic(text):
    if not re.search('[\u0600-\u06FF]', str(text)): return text
    parts = re.split('([\u0600-\u06FF]+)', str(text))
    res = []
    for p in parts:
        if re.search('[\u0600-\u06FF]', p): res.append(p[::-1])
        else: res.append(p)
    return ''.join(res)

def generate_pdf(title, df, cols_map=None):
    font = get_arabic_font()
    pdf = FPDF()
    pdf.add_page()
    if font: pdf.add_font("Amiri","",font); pdf.set_font("Amiri", size=14)
    else: pdf.set_font("Helvetica", size=14)
    pdf.cell(0,10,reverse_arabic(title),ln=True,align='C')
    pdf.ln(10)
    if df.empty:
        pdf.cell(0,10,"لا توجد بيانات",ln=True)
        return bytes(pdf.output())
    if cols_map: df = df.rename(columns=cols_map)
    cols = list(df.columns)
    widths = []
    for col in cols:
        m = pdf.get_string_width(reverse_arabic(str(col)))
        for _,r in df.iterrows():
            v = str(r[col]) if pd.notnull(r[col]) else '-'
            m = max(m, pdf.get_string_width(reverse_arabic(v)))
        widths.append(m+10)
    total = sum(widths)
    if total > pdf.w-20:
        scale = (pdf.w-20)/total
        widths = [w*scale for w in widths]
    pdf.set_fill_color(0,168,107); pdf.set_text_color(255,255,255)
    for i,col in enumerate(cols):
        pdf.cell(widths[i],10,reverse_arabic(str(col)), border=1, fill=True, align='C')
    pdf.ln()
    pdf.set_text_color(0,0,0)
    pdf.set_font("Amiri","",10) if font else pdf.set_font("Helvetica","",10)
    for _,row in df.iterrows():
        for i,col in enumerate(cols):
            v = str(row[col]) if pd.notnull(row[col]) else '-'
            pdf.cell(widths[i],8,reverse_arabic(v), border=1, align='C')
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
            else: st.error("خطأ")
    st.stop()

# -------------------- الشريط الجانبي --------------------
st.sidebar.title("🧹 مخزن النظافة")
st.sidebar.write(f"مرحباً {st.session_state.user['full_name']} ({st.session_state.user['role']})")
if st.sidebar.button("تسجيل الخروج"): logout()
st.sidebar.divider()

# بناء القائمة حسب الدور
menu = []
if check_perm():
    menu = ["📊 لوحة التحكم","📦 إدارة الأصناف","📂 التصنيفات والوحدات","🏨 الفنادق","🏢 الموردين",
            "📍 أماكن التخزين","📥 الوارد","📤 الصادر","📝 الجرد","⚠️ الصلاحيات","📈 التقارير",
            "💾 النسخ الاحتياطي","👥 المستخدمين"]
elif has_role('purchasing'):
    menu = ["📊 لوحة التحكم","📥 الوارد","📈 التقارير","⚠️ الصلاحيات"]
elif has_role('disbursement'):
    menu = ["📊 لوحة التحكم","📤 الصادر","📈 التقارير"]
elif has_role('supervisor'):
    menu = ["📊 لوحة التحكم","📝 الجرد","⚠️ الصلاحيات","📈 التقارير"]

choice = st.sidebar.radio("القائمة", menu)

# -------------------- تنفيذ الصفحات --------------------
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
    st.header("إدارة الأصناف")
    conn = get_db()
    tab1, tab2 = st.tabs(["إضافة صنف","قائمة الأصناف"])
    with tab1:
        cats = conn.execute("SELECT id, category_name FROM categories").fetchall()
        units = conn.execute("SELECT id, unit_name, unit_symbol FROM units").fetchall()
        locs = conn.execute("SELECT id, location_name FROM storage_locations").fetchall()
        with st.form("add_item"):
            name = st.text_input("اسم الصنف")
            cat = st.selectbox("التصنيف", [c['category_name'] for c in cats])
            unit = st.selectbox("الوحدة", [f"{u['unit_name']} ({u['unit_symbol']})" for u in units])
            loc = st.selectbox("مكان التخزين", [l['location_name'] for l in locs])
            min_q = st.number_input("الحد الأدنى",0.0,1000.0,10.0)
            max_q = st.number_input("الحد الأقصى",0.0,1000.0,100.0)
            init_bal = st.number_input("الرصيد الافتتاحي",0.0,1000.0,0.0)
            if st.form_submit_button("حفظ"):
                cat_id = [c['id'] for c in cats if c['category_name']==cat][0]
                unit_id = [u['id'] for u in units if f"{u['unit_name']} ({u['unit_symbol']})"==unit][0]
                loc_id = [l['id'] for l in locs if l['location_name']==loc][0]
                code = f"CLN-{cat_id:03d}-{conn.execute('SELECT COUNT(*) FROM items WHERE category_id=?',(cat_id,)).fetchone()[0]+1:04d}"
                conn.execute("INSERT INTO items (item_code,name,category_id,unit_id,min_qty,max_qty,current_balance,storage_location_id,created_date,last_updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (code,name,cat_id,unit_id,min_q,max_q,init_bal,loc_id,date.today().isoformat(),date.today().isoformat()))
                conn.commit()
                st.success(f"تمت الإضافة، الكود: {code}")
                st.rerun()
    with tab2:
        items = conn.execute("SELECT i.item_code,i.name,c.category_name,i.current_balance,u.unit_symbol,i.min_qty,i.max_qty,sl.location_name FROM items i LEFT JOIN categories c ON i.category_id=c.id LEFT JOIN units u ON i.unit_id=u.id LEFT JOIN storage_locations sl ON i.storage_location_id=sl.id WHERE i.is_active=1").fetchall()
        if items:
            df = pd.DataFrame(items, columns=['كود','الصنف','التصنيف','الرصيد','الوحدة','الحد الأدنى','الحد الأقصى','مكان التخزين'])
            st.dataframe(df)
            export_buttons(df, "قائمة_الأصناف", "تقرير الأصناف")
    conn.close()

elif choice == "📂 التصنيفات والوحدات":
    st.header("التصنيفات والوحدات")
    conn = get_db()
    tab1,tab2,tab3 = st.tabs(["تصنيفات","وحدات","أماكن تخزين"])
    with tab1:
        with st.form("add_cat"):
            cname = st.text_input("اسم التصنيف")
            if st.form_submit_button("إضافة"):
                conn.execute("INSERT OR IGNORE INTO categories (category_name) VALUES (?)",(cname,))
                conn.commit(); st.success("تم"); st.rerun()
        cats = conn.execute("SELECT * FROM categories").fetchall()
        st.dataframe(pd.DataFrame(cats, columns=['م','التصنيف','وصف']))
    with tab2:
        with st.form("add_unit"):
            uname = st.text_input("اسم الوحدة"); usym = st.text_input("الرمز")
            if st.form_submit_button("إضافة"):
                conn.execute("INSERT OR IGNORE INTO units (unit_name,unit_symbol) VALUES (?,?)",(uname,usym))
                conn.commit(); st.success("تم"); st.rerun()
        units = conn.execute("SELECT * FROM units").fetchall()
        st.dataframe(pd.DataFrame(units, columns=['م','الوحدة','الرمز']))
    with tab3:
        with st.form("add_loc"):
            lname = st.text_input("اسم المكان")
            if st.form_submit_button("إضافة"):
                conn.execute("INSERT OR IGNORE INTO storage_locations (location_name) VALUES (?)",(lname,))
                conn.commit(); st.success("تم"); st.rerun()
        locs = conn.execute("SELECT * FROM storage_locations").fetchall()
        st.dataframe(pd.DataFrame(locs, columns=['م','المكان','وصف']))
    conn.close()

elif choice == "🏨 الفنادق":
    st.header("الفنادق")
    conn = get_db()
    with st.form("add_hotel"):
        name = st.text_input("اسم الفندق")
        contact = st.text_input("الشخص المسؤول")
        phone = st.text_input("الهاتف")
        if st.form_submit_button("إضافة"):
            conn.execute("INSERT OR IGNORE INTO hotels (name,contact_person,phone) VALUES (?,?,?)",(name,contact,phone))
            conn.commit(); st.success("تم"); st.rerun()
    hotels = conn.execute("SELECT * FROM hotels").fetchall()
    if hotels:
        st.dataframe(pd.DataFrame(hotels, columns=['م','الفندق','المسؤول','الهاتف','ملاحظات']))
    conn.close()

elif choice == "🏢 الموردين":
    st.header("الموردين")
    conn = get_db()
    with st.form("add_sup"):
        name = st.text_input("اسم المورد")
        info = st.text_input("معلومات الاتصال")
        if st.form_submit_button("إضافة"):
            conn.execute("INSERT OR IGNORE INTO suppliers (supplier_name,contact_info) VALUES (?,?)",(name,info))
            conn.commit(); st.success("تم"); st.rerun()
    supps = conn.execute("SELECT * FROM suppliers").fetchall()
    if supps:
        st.dataframe(pd.DataFrame(supps, columns=['م','المورد','الاتصال','ملاحظات']))
    conn.close()

elif choice == "📍 أماكن التخزين":
    st.header("أماكن التخزين")
    conn = get_db()
    with st.form("add_loc2"):
        name = st.text_input("اسم المكان")
        if st.form_submit_button("إضافة"):
            conn.execute("INSERT OR IGNORE INTO storage_locations (location_name) VALUES (?)",(name,))
            conn.commit(); st.success("تم"); st.rerun()
    locs = conn.execute("SELECT * FROM storage_locations").fetchall()
    if locs:
        st.dataframe(pd.DataFrame(locs, columns=['م','المكان','وصف']))
    conn.close()

elif choice == "📥 الوارد":
    st.header("المشتريات (وارد)")
    conn = get_db()
    items = conn.execute("SELECT id,name,unit_id FROM items WHERE is_active=1").fetchall()
    if items:
        with st.form("inward"):
            item = st.selectbox("الصنف", [i['name'] for i in items])
            qty = st.number_input("الكمية",0.1,10000.0,1.0)
            batch = st.text_input("رقم التشغيلة")
            exp_date = st.date_input("تاريخ انتهاء الصلاحية", date.today()+timedelta(days=365))
            notes = st.text_input("ملاحظات")
            if st.form_submit_button("تسجيل"):
                it = [i for i in items if i['name']==item][0]
                conn.execute("INSERT INTO transactions (transaction_type,item_id,qty,unit_id,batch_number,expiry_date,transaction_date,notes,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                             ('وارد',it['id'],qty,it['unit_id'],batch,exp_date.isoformat(),date.today().isoformat(),notes,st.session_state.user['full_name']))
                conn.execute("UPDATE items SET current_balance=current_balance+?, last_updated=? WHERE id=?",(qty,date.today().isoformat(),it['id']))
                if exp_date:
                    conn.execute("INSERT INTO expiry_alerts (item_id,batch_number,expiry_date,qty_remaining) VALUES (?,?,?,?)",(it['id'],batch,exp_date.isoformat(),qty))
                conn.commit()
                st.success("تم التسجيل")
                st.rerun()
    conn.close()

elif choice == "📤 الصادر":
    st.header("الصرف (صادر)")
    conn = get_db()
    items = conn.execute("SELECT id,name,current_balance,unit_id FROM items WHERE is_active=1").fetchall()
    hotels = conn.execute("SELECT id,name FROM hotels").fetchall()
    if items and hotels:
        with st.form("outward"):
            item = st.selectbox("الصنف", [f"{i['name']} (الرصيد: {i['current_balance']})" for i in items])
            hotel = st.selectbox("الفندق", [h['name'] for h in hotels])
            qty = st.number_input("الكمية",0.1,10000.0,1.0)
            notes = st.text_input("ملاحظات")
            if st.form_submit_button("صرف"):
                it_name = item.split(" (")[0]
                it = [i for i in items if i['name']==it_name][0]
                if qty > it['current_balance']:
                    st.error("الرصيد غير كاف")
                else:
                    conn.execute("INSERT INTO transactions (transaction_type,item_id,hotel_id,qty,unit_id,transaction_date,notes,created_by) VALUES (?,?,?,?,?,?,?,?)",
                                 ('صادر',it['id'],[h['id'] for h in hotels if h['name']==hotel][0],qty,it['unit_id'],date.today().isoformat(),notes,st.session_state.user['full_name']))
                    conn.execute("UPDATE items SET current_balance=current_balance-?, last_updated=? WHERE id=?",(qty,date.today().isoformat(),it['id']))
                    conn.commit()
                    st.success("تم الصرف")
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
            st.success("تم الجرد")
            st.rerun()
    conn.close()

elif choice == "⚠️ الصلاحيات":
    st.header("متابعة الصلاحية")
    conn = get_db()
    days = st.selectbox("تنتهي خلال", [30,60,90,180])
    today = date.today()
    exp = conn.execute("SELECT i.name, ea.batch_number, ea.expiry_date, ea.qty_remaining, u.unit_symbol FROM expiry_alerts ea JOIN items i ON ea.item_id=i.id LEFT JOIN units u ON i.unit_id=u.id WHERE ea.is_consumed=0 AND ea.expiry_date<=? ORDER BY ea.expiry_date", ((today+timedelta(days=days)).isoformat(),)).fetchall()
    if exp:
        df = pd.DataFrame(exp, columns=['الصنف','تشغيلة','تاريخ الانتهاء','الكمية','الوحدة'])
        st.dataframe(df)
        export_buttons(df, "تقرير_الصلاحيات", "تقرير الصلاحيات")
    else:
        st.success("لا توجد صلاحيات قريبة")
    conn.close()

elif choice == "📈 التقارير":
    st.header("التقارير")
    conn = get_db()
    tab1,tab2 = st.tabs(["حركات","أرصدة"])
    with tab1:
        d1 = st.date_input("من", date.today()-timedelta(days=30))
        d2 = st.date_input("إلى", date.today())
        typ = st.selectbox("النوع",["الكل","وارد","صادر","تسوية إضافة","تسوية عجز"])
        q = "SELECT t.id, t.transaction_type, i.name, COALESCE(h.name,'-'), t.qty, u.unit_symbol, t.transaction_date, t.notes FROM transactions t JOIN items i ON t.item_id=i.id LEFT JOIN hotels h ON t.hotel_id=h.id LEFT JOIN units u ON t.unit_id=u.id WHERE t.transaction_date BETWEEN ? AND ?"
        params = [d1.isoformat(), d2.isoformat()]
        if typ!="الكل": q+=" AND t.transaction_type=?"; params.append(typ)
        q+=" ORDER BY t.id DESC"
        data = conn.execute(q, params).fetchall()
        if data:
            df = pd.DataFrame(data, columns=['رقم','النوع','الصنف','الفندق','الكمية','الوحدة','التاريخ','ملاحظات'])
            st.dataframe(df)
            export_buttons(df, "حركات", "تقرير الحركات")
    with tab2:
        items = conn.execute("SELECT i.item_code,i.name,i.current_balance,u.unit_symbol FROM items i LEFT JOIN units u ON i.unit_id=u.id WHERE i.is_active=1").fetchall()
        if items:
            df = pd.DataFrame(items, columns=['كود','الصنف','الرصيد','الوحدة'])
            st.dataframe(df)
            export_buttons(df, "ارصدة", "تقرير الأرصدة")
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

elif choice == "👥 المستخدمين":
    if not has_role('super_admin'): st.error("غير مصرح")
    else:
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
                    conn.commit(); st.success("تم"); st.rerun()
                except: st.error("مستخدم موجود مسبقاً")
        conn.close()