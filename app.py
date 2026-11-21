import streamlit as st
from db import fetch_table, get_storage_bucket, get_supabase_client

# إعداد الصفحة لتكون متجاوبة على الكمبيوتر والموبايل وباتجاه RTL
st.set_page_config(
    page_title="نظام HR — معامل ا.د أسامة عبد المتعال منصور",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# تنسيق عربي متجاوب (كمبيوتر وموبايل) مع أزرار مدمجة وخطوط واضحة
st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap');

        html, body, [class*="css"] {
            font-family: 'Cairo', sans-serif;
            font-size: 14px;
            direction: rtl;
            text-align: right;
        }

        .main .block-container {
            padding-top: 1.2rem;
            padding-bottom: 2rem;
            max-width: 1280px;
        }

        .hero-card {
            background: linear-gradient(135deg, #0f4c5c 0%, #1b6b7a 100%);
            color: #ffffff;
            padding: 20px 24px;
            border-radius: 14px;
            margin-bottom: 18px;
            box-shadow: 0 4px 14px rgba(15, 76, 92, 0.14);
        }

        .hero-card h2 {
            margin: 0 0 6px 0;
            font-size: 22px;
            font-weight: 700;
            color: #ffffff;
        }

        .hero-card p {
            margin: 0;
            font-size: 14px;
            opacity: 0.92;
        }

        .info-box {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-right: 4px solid #0f4c5c;
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 14px;
        }

        .stButton > button {
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            padding: 0.4rem 0.9rem;
        }

        @media (max-width: 768px) {
            .hero-card h2 {
                font-size: 18px;
            }
            .main .block-container {
                padding-left: 0.8rem;
                padding-right: 0.8rem;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# الترويسة الترحيبية
st.markdown(
    """
    <div class="hero-card">
        <h2>أهلاً بك يا دكتور محمد — نظام الموارد البشرية (HR)</h2>
        <p>معامل ا.د أسامة عبد المتعال منصور | بيئة تشغيل متجاوبة (كمبيوتر وموبايل) مرتبطة بـ Supabase</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# زر إعادة اختبار الاتصال وتحديث البيانات
col_btn1, col_btn2, _ = st.columns([1.4, 1.6, 5])
with col_btn1:
    if st.button("🔄 اختبار الاتصال الآن", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

with col_btn2:
    st.caption("الوضع الحالي: فحص الاتصال والتهيئة الأولية")

# اختبار الاتصال بـ Supabase وقراءة الإعدادات وأنواع الإجازات
try:
    _client = get_supabase_client()
    bucket_name = get_storage_bucket()
    org_rows = fetch_table("org_settings", limit=10)
    leave_types_rows = fetch_table("leave_types", limit=50)
    emp_rows = fetch_table("employees", limit=1000)
    dept_rows = fetch_table("departments", limit=100)
    shift_rows = fetch_table("shifts", limit=100)

    st.success("✅ تم الاتصال بقاعدة بيانات Supabase (osama-lab-hr) بنجاح!")

    org_name = org_rows[0].get("org_name", "معامل ا.د أسامة عبد المتعال منصور") if org_rows else "معامل ا.د أسامة عبد المتعال منصور"
    admin_year = org_rows[0].get("admin_year", "2026") if org_rows else "2026"
    grace_late = org_rows[0].get("grace_late_minutes", 15) if org_rows else 15
    grace_early = org_rows[0].get("grace_early_minutes", 5) if org_rows else 5

    st.markdown(
        f"""
        <div class="info-box">
            <strong>المؤسسة:</strong> {org_name} &nbsp;|&nbsp;
            <strong>السنة الإدارية:</strong> {admin_year} &nbsp;|&nbsp;
            <strong>سماح الحضور:</strong> {grace_late} د &nbsp;|&nbsp;
            <strong>سماح الانصراف:</strong> {grace_early} د &nbsp;|&nbsp;
            <strong>حاوية المرفقات:</strong> <code>{bucket_name}</code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("الموظفون المسجلون", len(emp_rows))
    m2.metric("الفروع والأقسام", len(dept_rows))
    m3.metric("الورديات (صباحي/مسائي)", len(shift_rows))
    m4.metric("أنواع الإجازات المفعّلة", len(leave_types_rows))

    if leave_types_rows:
        st.subheader("قواعد وأنواع الإجازات المحمّلة في قاعدة البيانات")
        st.dataframe(
            [
                {
                    "النوع": row.get("name_ar"),
                    "الكود": row.get("code"),
                    "خصم من رصيد": "نعم" if row.get("deducts") else "لا",
                    "المجال": row.get("balance_field") or "—",
                    "تنبيه مسبق (أيام)": row.get("notice_days", 0),
                    "حد أقصى للطلب": row.get("max_days_per_request") or "مفتوح",
                    "حد أقصى شهري": row.get("max_days_per_month") or "مفتوح",
                    "تفعيل بعد (يوم تعيين)": row.get("min_tenure_days", 0),
                }
                for row in leave_types_rows
            ],
            use_container_width=True,
            hide_index=True,
        )

except Exception as exc:
    st.error("❌ تعذر إتمام الاتصال بقاعدة بيانات Supabase.")
    st.code(str(exc), language="text")
    st.info(
        "تأكد من إضافة SUPABASE_URL و SUPABASE_KEY داخل Secrets في Streamlit Cloud، "
        "ومن تشغيل سكربت SQL الخاص بالجداول في مشروع osama-lab-hr."
    )
