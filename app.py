from typing import Any, Dict, List
import streamlit as st

from db import fetch_table, insert_record, write_audit_log
from ui_components import (
    get_pending_approvals_count,
    render_admin_and_staff,
    render_approvals_inbox,
    render_attendance_and_payroll,
    render_dashboard,
    render_kpis_and_reports,
    render_leaves_module,
    render_penalties_rewards_advances,
)

st.set_page_config(
    page_title="نظام HR — معامل ا.د أسامة عبد المتعال منصور",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

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
            padding-top: 1.1rem;
            padding-bottom: 2rem;
            max-width: 1360px;
        }

        .top-banner {
            background: linear-gradient(135deg, #0f4c5c 0%, #1c7285 100%);
            color: #ffffff;
            padding: 16px 22px;
            border-radius: 12px;
            margin-bottom: 14px;
        }

        .top-banner h3 {
            margin: 0 0 4px 0;
            font-size: 20px;
            font-weight: 700;
            color: #ffffff;
        }

        .top-banner p {
            margin: 0;
            font-size: 13.5px;
            opacity: 0.92;
        }

        .stButton > button {
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            padding: 0.35rem 0.85rem;
        }

        @media (max-width: 768px) {
            .top-banner h3 {
                font-size: 17px;
            }
            .main .block-container {
                padding-left: 0.7rem;
                padding-right: 0.7rem;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def _ensure_bootstrap_gm(employees: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """إنشاء حساب مدير عام أولي إذا كان جدول الموظفين فارغاً تماماً."""
    if employees:
        return employees
    created = insert_record(
        "employees",
        {
            "employee_no": "1",
            "name_ar": "محمد النصيري عبده النصيري",
            "job_title": "مدير عام المعامل",
            "role": "general_manager",
            "hire_date": "2020-01-01",
            "base_salary": 15000,
            "pin_code": "1234",
            "status": "active",
        },
    )
    write_audit_log("employees", "bootstrap_gm", "إضافة", "إنشاء حساب المدير العام الأولي")
    return [created] if created else fetch_table("employees", limit=1500)


def _refresh_current_user(session_user: Dict[str, Any], employees: List[Dict[str, Any]]) -> Dict[str, Any]:
    """مزامنة بيانات المستخدم المسجل حالياً مع أحدث نسخة في جدول الموظفين."""
    uid = str(session_user.get("id") or "")
    eno = str(session_user.get("employee_no") or "")
    for emp in employees:
        if (uid and str(emp.get("id") or "") == uid) or (eno and str(emp.get("employee_no") or "") == eno):
            return emp
    return session_user


def main() -> None:
    try:
        employees = _ensure_bootstrap_gm(fetch_table("employees", order_by="employee_no", limit=1500))
        departments = fetch_table("departments", order_by="code", limit=200)
        shifts = fetch_table("shifts", order_by="name", limit=200)
        leave_types = fetch_table("leave_types", order_by="id", limit=100)
        leave_balances = fetch_table("leave_balances", limit=1500)
        leave_requests = fetch_table("leave_requests", order_by="created_at", desc=True, limit=3000)
        penalties_rewards = fetch_table("penalties_rewards", order_by="created_at", desc=True, limit=3000)
        advances = fetch_table("advances", order_by="created_at", desc=True, limit=2000)
        attendance_logs = fetch_table("attendance_logs", order_by="work_date", desc=True, limit=10000)
        official_holidays = fetch_table("official_holidays", order_by="holiday_date", limit=200)
        payroll_runs = fetch_table("payroll_runs", order_by="payroll_month", desc=True, limit=5000)
        kpi_evaluations = fetch_table("kpi_evaluations", order_by="created_at", desc=True, limit=2000)
        org_rows = fetch_table("org_settings", limit=5)
        org_settings: Dict[str, Any] = org_rows[0] if org_rows else {"org_name": "معامل ا.د أسامة عبد المتعال منصور"}
    except Exception as exc:
        st.error("تعذر تحميل البيانات من Supabase.")
        st.code(str(exc), language="text")
        return

    if "current_user" not in st.session_state:
        st.markdown(
            """
            <div class="top-banner">
                <h3>معامل ا.د أسامة عبد المتعال منصور</h3>
                <p>نظام الموارد البشرية (HR) والمرتبات — تسجيل الدخول</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        active_emps = [e for e in employees if (e.get("status") or "active") == "active"]
        labels = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in active_emps}
        if not labels:
            st.warning("لا يوجد موظفون نشطون.")
            return

        with st.form("login_form"):
            chosen_lbl = st.selectbox("اختر الحساب", list(labels.keys()))
            pin_inp = st.text_input("رمز الدخول (PIN)", type="password", placeholder="الافتراضي 1234")
            if st.form_submit_button("دخول", type="primary"):
                emp_sel = labels[chosen_lbl]
                saved_pin = str(emp_sel.get("pin_code") or "").strip()
                if not saved_pin or pin_inp.strip() == saved_pin:
                    st.session_state["current_user"] = emp_sel
                    st.session_state["active_page"] = "الداش بورد"
                    st.rerun()
                else:
                    st.error("رمز الدخول غير صحيح.")
        return

    current_user = _refresh_current_user(st.session_state["current_user"], employees)
    st.session_state["current_user"] = current_user

    if "active_page" not in st.session_state:
        st.session_state["active_page"] = "الداش بورد"

    pending_cnt = get_pending_approvals_count(
        current_user,
        leave_requests,
        penalties_rewards,
        advances,
        attendance_logs,
        employees=employees,
    )
    approvals_btn_label = f"🔔 الموافقات ({pending_cnt})" if pending_cnt > 0 else "🔔 الموافقات"

    with st.sidebar:
        st.markdown(f"### 👤 {current_user.get('name_ar')}")
        st.caption(f"الدور: `{current_user.get('role')}` | رقم: `{current_user.get('employee_no')}`")

        main_menu = [
            "الداش بورد",
            "الإجازات والأرصدة",
            "الجزاءات والمكافآت والسلف",
            "البصمة والـ Payroll",
            "التقييم (KPIs) والتقارير",
            "الموظفون والفروع والصلاحيات",
        ]
        current_radio_val = st.session_state["active_page"] if st.session_state["active_page"] in main_menu else "الداش بورد"
        picked_menu = st.radio("القائمة الرئيسية", main_menu, index=main_menu.index(current_radio_val))
        if picked_menu != current_radio_val:
            st.session_state["active_page"] = picked_menu
            st.rerun()

        if st.button(approvals_btn_label, key="nav_approvals_btn", use_container_width=True):
            st.session_state["active_page"] = "الموافقات"
            st.rerun()

        st.divider()
        if st.button("🔄 تحديث البيانات", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        if st.button("تسجيل الخروج", use_container_width=True):
            del st.session_state["current_user"]
            st.session_state.pop("active_page", None)
            st.rerun()

    page = st.session_state["active_page"]

    st.markdown(
        f"""
        <div class="top-banner">
            <h3>{org_settings.get('org_name') or 'معامل ا.د أسامة عبد المتعال منصور'}</h3>
            <p>نظام HR متكامل (الإجازات، الجزاءات والمكافآت، السلف، البصمة والمرتبات، الـ KPIs والتقارير)</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if page == "الداش بورد":
        render_dashboard(employees, departments, shifts, leave_requests, attendance_logs, advances)
    elif page == "الإجازات والأرصدة":
        render_leaves_module(current_user, employees, departments, leave_types, leave_balances, leave_requests, org_settings)
    elif page == "الموافقات":
        render_approvals_inbox(
            current_user,
            employees,
            leave_balances,
            leave_requests,
            penalties_rewards,
            advances,
            attendance_logs,
        )
    elif page == "الجزاءات والمكافآت والسلف":
        render_penalties_rewards_advances(current_user, employees, penalties_rewards, advances)
    elif page == "البصمة والـ Payroll":
        render_attendance_and_payroll(
            current_user,
            employees,
            shifts,
            attendance_logs,
            penalties_rewards,
            advances,
            official_holidays,
            payroll_runs,
            org_settings,
            leave_requests=leave_requests,
        )
    elif page == "التقييم (KPIs) والتقارير":
        render_kpis_and_reports(
            current_user,
            employees,
            leave_requests,
            attendance_logs,
            penalties_rewards,
            advances,
            kpi_evaluations,
            org_settings,
        )
    elif page == "الموظفون والفروع والصلاحيات":
        render_admin_and_staff(
            current_user,
            employees,
            departments,
            shifts,
            org_settings,
            official_holidays=official_holidays,
        )


if __name__ == "__main__":
    main()
