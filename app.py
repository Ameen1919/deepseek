import streamlit as st
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager
import pandas as pd
from datetime import datetime
import io

# ==========================================
# 1. إعدادات الصفحة والاتصال بقاعدة البيانات
# ==========================================
st.set_page_config(page_title="نظام إدارة المخزن", layout="wide", initial_sidebar_state="expanded")

# رابط الاتصال المباشر عبر Supabase Connection Pooler
DB_URL = "postgresql://postgres.krrbpyleyvcmshcqcdog:[Ameen_Ali_1919]@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres"

@st.cache_resource
def get_connection_pool():
    return SimpleConnectionPool(1, 20, dsn=DB_URL, connect_timeout=10)

try:
    pool = get_connection_pool()
except Exception as e:
    st.error(f"خطأ في الاتصال بقاعدة البيانات السحابية: {e}")
    st.stop()

@contextmanager
def get_db():
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)

# ==========================================
# 2. تهيئة الجداول في PostgreSQL
# ==========================================
def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            # جدول المستخدمين
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password VARCHAR(50) NOT NULL,
                    role VARCHAR(20) NOT NULL
                );
            """)
            # جدول الاصناف
            cur.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) UNIQUE NOT NULL,
                    min_limit INT DEFAULT 5
                );
            """)
            # جدول الحركات (وارد / منصرف)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    item_id INT REFERENCES items(id) ON DELETE CASCADE,
                    type VARCHAR(10) NOT NULL,
                    quantity INT NOT NULL,
                    date VARCHAR(20) NOT NULL,
                    notes TEXT,
                    created_by VARCHAR(50)
                );
            """)
            # إضافة حساب المسؤول الافتراضي إن لم يكن موجوداً
            cur.execute("SELECT * FROM users WHERE username = %s;", ('admin',))
            if not cur.fetchone():
                cur.execute("INSERT INTO users (username, password, role) VALUES (%s, %s, %s);",
                            ('admin', 'admin123', 'مدير'))

init_db()

# ==========================================
# 3. إدارة الجلسة والدخول
# ==========================================
if 'user' not in st.session_state:
    st.session_state.user = None

def login(username, password):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username, role FROM users WHERE username = %s AND password = %s;", (username, password))
            user = cur.fetchone()
            if user:
                st.session_state.user = {"username": user[0], "role": user[1]}
                return True
            return False

if not st.session_state.user:
    st.title("🔒 تسجيل الدخول - نظام إدارة المخزن")
    with st.form("login_form"):
        user_input = st.text_input("اسم المستخدم")
        pass_input = st.text_input("كلمة المرور", type="password")
        submit = st.form_submit_button("دخول")
        if submit:
            if login(user_input, pass_input):
                st.success("تم تسجيل الدخول بنجاح!")
                st.rerun()
            else:
                st.error("اسم المستخدم أو كلمة المرور غير صحيحة")
    st.stop()

# ==========================================
# 4. الشريط الجانبي والتنقل
# ==========================================
st.sidebar.title(f"مرحباً، {st.session_state.user['username']}")
st.sidebar.write(f"الصلاحية: **{st.session_state.user['role']}**")

menu = ["عرض المخزون", "تسجيل حركة (وارد/منصرف)", "إدارة الأصناف", "التقارير وسجل الحركات"]
if st.session_state.user['role'] == "مدير":
    menu.append("إدارة المستخدمين")

choice = st.sidebar.radio("القائمة الرئيسية", menu)

if st.sidebar.button("تسجيل الخروج"):
    st.session_state.user = None
    st.rerun()

# ==========================================
# 5. صفحات التطبيق
# ==========================================

# --- صفحة عرض المخزون ---
if choice == "عرض المخزون":
    st.header("📦 حالة المخزون الحالية")
    
    with get_db() as conn:
        query = """
            SELECT 
                i.id,
                i.name AS "اسم الصنف",
                COALESCE(SUM(CASE WHEN t.type = 'وارد' THEN t.quantity ELSE 0 END), 0) -
                COALESCE(SUM(CASE WHEN t.type = 'منصرف' THEN t.quantity ELSE 0 END), 0) AS "الرصيد الحالي",
                i.min_limit AS "حد الأمان"
            FROM items i
            LEFT JOIN transactions t ON i.id = t.item_id
            GROUP BY i.id, i.name, i.min_limit
            ORDER BY i.name;
        """
        df = pd.read_sql(query, conn)
    
    if not df.empty:
        # التنبيه عند نقص المخزون عن حد الأمان
        low_stock = df[df["الرصيد الحالي"] <= df["حد الأمان"]]
        if not low_stock.empty:
            st.warning("⚠️ تنبيه: هناك أصناف وصلت أو أقل من حد الأمان!")
            st.dataframe(low_stock, use_container_width=True)
        
        st.subheader("جميع الأصناف")
        st.dataframe(df, use_container_width=True)
    else:
        st.info("لا توجد أصناف مسجلة حالياً.")

# --- صفحة تسجيل حركة ---
elif choice == "تسجيل حركة (وارد/منصرف)":
    st.header("📝 تسجيل إذن (وارد / منصرف)")
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM items ORDER BY name;")
            items = cur.fetchall()
    
    if not items:
        st.warning("يرجى إضافة أصناف أولاً من صفحة 'إدارة الأصناف'.")
    else:
        item_dict = {name: item_id for item_id, name in items}
        
        with st.form("transaction_form"):
            selected_item_name = st.selectbox("اختر الصنف", list(item_dict.keys()))
            t_type = st.radio("نوع الحركة", ["وارد", "منصرف"], horizontal=True)
            quantity = st.number_input("الكمية", min_value=1, step=1)
            date_str = st.date_input("التاريخ", datetime.now()).strftime("%Y-%m-%d")
            notes = st.text_area("ملاحظات")
            
            submit = st.form_submit_button("حفظ الحركة")
            
            if submit:
                item_id = item_dict[selected_item_name]
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO transactions (item_id, type, quantity, date, notes, created_by)
                            VALUES (%s, %s, %s, %s, %s, %s);
                        """, (item_id, t_type, quantity, date_str, notes, st.session_state.user['username']))
                st.success(f"تم تسجيل {t_type} بكمية {quantity} للصنف ({selected_item_name}) بنجاح!")

# --- صفحة إدارة الأصناف ---
elif choice == "إدارة الأصناف":
    st.header("⚙️ إدارة الأصناف")
    
    tab1, tab2 = st.tabs(["إضافة صنف جديد", "الأصناف الحالية"])
    
    with tab1:
        with st.form("add_item_form"):
            item_name = st.text_input("اسم الصنف")
            min_limit = st.number_input("حد الأمان (التنبيه)", min_value=0, value=5)
            submit = st.form_submit_button("إضافة")
            
            if submit and item_name:
                try:
                    with get_db() as conn:
                        with conn.cursor() as cur:
                            cur.execute("INSERT INTO items (name, min_limit) VALUES (%s, %s);", (item_name, min_limit))
                    st.success(f"تمت إضافة الصنف ({item_name}) بنجاح!")
                    st.rerun()
                except Exception as e:
                    st.error("الصنف موجود بالفعل أو حدث خطأ أثناء الإضافة.")
    
    with tab2:
        with get_db() as conn:
            df_items = pd.read_sql("SELECT id, name AS \"اسم الصنف\", min_limit AS \"حد الأمان\" FROM items ORDER BY id;", conn)
        st.dataframe(df_items, use_container_width=True)

# --- صفحة التقارير ---
elif choice == "التقارير وسجل الحركات":
    st.header("📊 سجل الحركات والتقارير")
    
    with get_db() as conn:
        query = """
            SELECT 
                t.id AS "رقم الحركة",
                t.date AS "التاريخ",
                i.name AS "اسم الصنف",
                t.type AS "نوع الحركة",
                t.quantity AS "الكمية",
                t.notes AS "ملاحظات",
                t.created_by AS "المستخدم"
            FROM transactions t
            JOIN items i ON t.item_id = i.id
            ORDER BY t.id DESC;
        """
        df_trans = pd.read_sql(query, conn)
    
    st.dataframe(df_trans, use_container_width=True)
    
    if not df_trans.empty:
        # تصدير إلى Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_trans.to_excel(writer, index=False, sheet_name='الحركات')
        
        st.download_button(
            label="📥 تنزيل التقرير كملف Excel",
            data=buffer.getvalue(),
            file_name=f"inventory_report_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# --- صفحة إدارة المستخدمين (للمدير فقط) ---
elif choice == "إدارة المستخدمين" and st.session_state.user['role'] == "مدير":
    st.header("👥 إدارة المستخدمين")
    
    with st.form("add_user_form"):
        new_username = st.text_input("اسم المستخدم الجديد")
        new_password = st.text_input("كلمة المرور", type="password")
        new_role = st.selectbox("الصلاحية", ["مستخدم", "مدير"])
        submit_user = st.form_submit_button("إضافة مستخدم")
        
        if submit_user and new_username and new_password:
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO users (username, password, role) VALUES (%s, %s, %s);",
                                    (new_username, new_password, new_role))
                st.success(f"تم إضافة المستخدم ({new_username}) بنجاح!")
            except Exception:
                st.error("اسم المستخدم موجود بالفعل.")
    
    with get_db() as conn:
        users_df = pd.read_sql("SELECT id, username AS \"اسم المستخدم\", role AS \"الصلاحية\" FROM users;", conn)
    st.dataframe(users_df, use_container_width=True)
