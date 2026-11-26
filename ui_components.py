from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
import io
import pandas as pd
import streamlit as st

from db import (
    delete_record,
    fetch_table,
    insert_record,
    update_record,
    upsert_record,
    upload_attachment,
    write_audit_log,
)
from payroll_engine import (
    build_employee_period_summary,
    calculate_advance_schedule,
    calculate_penalty_or_reward_amount,
    compute_employee_monthly_payroll,
    evaluate_attendance_record,
    extract_active_fixed_allowances,
    get_employee_monthly_advances_summary,
)
from rules_engine import (
    calculate_prorated_leave_entitlement,
    determine_initial_approval_route,
    get_effective_permissions,
    parse_date_safe,
    round_half_up_2,
    validate_leave_request,
)

EGYPT_OFFICIAL_HOLIDAYS: List[str] = [
    "عيد الميلاد المجيد",
    "عيد الشرطة / ثورة 25 يناير",
    "عيد الفطر المبارك",
    "شم النسيم",
    "عيد تحرير سيناء",
    "عيد العمال",
    "عيد الأضحى المبارك",
    "رأس السنة الهجرية",
    "ثورة 30 يونيو",
    "عيد الثورة (23 يوليو)",
    "المولد النبوي الشريف",
    "عيد القوات المسلحة (6 أكتوبر)",
    "إجازة رسمية أخرى (مخصصة)",
]

DEFAULT_FIXED_ALLOWANCE_TYPES: List[str] = [
    "مكافأة ثابتة تحفيزية",
    "مكافأة ثابتة للسينيور",
    "مكافأة ثابتة للمدير المباشر",
    "مواصلات",
]

EMPLOYEE_JOB_TITLES: List[str] = [
    "موظف استقبال",
    "موظف استقبال وسحب عينات",
    "موظف خدمة عملاء",
    "كيميائي",
    "senior",
    "مدير مباشر",
    "مدير عام",
]

KPI_CONFIG_TABS: List[str] = [
    "استقبال",
    "استقبال وسحب عينات",
    "خدمة عملاء",
    "كيميائيين",
    "مدير مباشر",
    "مدير عام",
]

KPI_SELF_TABS: List[str] = [
    "استقبال",
    "استقبال وسحب عينات",
    "خدمة عملاء",
    "كيميائيين",
    "مدير مباشر",
]

DEFAULT_SELF_QUESTIONS_BY_TAB: Dict[str, List[Dict[str, Any]]] = {
    "استقبال": [
        {"question": "سرعة ودقة استقبال المرضى وتسجيل البيانات", "points": 25.0},
        {"question": "وضوح شرح التعليمات والنتائج للمراجعين", "points": 25.0},
        {"question": "الالتزام بالمواعيد والتعليمات الإدارية", "points": 25.0},
        {"question": "حسن التعامل وحل الشكاوى بهدوء ومهنية", "points": 25.0},
    ],
    "استقبال وسحب عينات": [
        {"question": "دقة تسجيل العينات وسلامة تطبيق معايير سحبها", "points": 30.0},
        {"question": "الالتزام بالوقاية والسلامة المهنية أثناء السحب", "points": 25.0},
        {"question": "السرعة والدقة في التعامل مع ضغط العمل", "points": 25.0},
        {"question": "الالتزام بالمواعيد والتعاون مع فريق المعمل", "points": 20.0},
    ],
    "خدمة عملاء": [
        {"question": "سرعة الرد واللباقة في التعامل مع العملاء", "points": 30.0},
        {"question": "دقة تسجيل الاستفسارات والحجوزات ومتابعتها", "points": 25.0},
        {"question": "الالتزام بالمواعيد ونسب الإنجاز اليومية", "points": 25.0},
        {"question": "الالتزام باللوائح والتعاون الجماعي", "points": 20.0},
    ],
    "كيميائيين": [
        {"question": "دقة التحاليل ومراجعة النتائج وفق المعايير الفنية", "points": 30.0},
        {"question": "الالتزام بجودة التشغيل ومراقبة الجودة (QC)", "points": 25.0},
        {"question": "الالتزام بالمواعيد والانضباط داخل المعمل", "points": 25.0},
        {"question": "التعاون وحل المشكلات الفنية والالتزام بالسياسات", "points": 20.0},
    ],
    "مدير مباشر": [
        {"question": "تحقيق مستهدفات الأداء وجودة الخدمات بالفرع", "points": 30.0},
        {"question": "إدارة وتطوير فريق العمل وتوزيع المهام بكفاءة", "points": 25.0},
        {"question": "متابعة الحضور والانصراف وسير العمليات اليومية", "points": 25.0},
        {"question": "الالتزام بالسياسات الإدارية واتخاذ القرارات بفاعلية", "points": 20.0},
    ],
}

DEFAULT_PEER_QUESTIONS_BY_TARGET: Dict[str, List[Dict[str, Any]]] = {
    "senior": [
        {"question": "وضوح التوجيه والمتابعة اليومية من السينيور", "points": 40.0},
        {"question": "سرعة الاستجابة والدعم الفني والإداري عند الحاجة", "points": 30.0},
        {"question": "العدالة والموضوعية في التعامل مع الفريق", "points": 30.0},
    ],
    "manager": [
        {"question": "وضوح التعليمات والأهداف من المدير المباشر", "points": 40.0},
        {"question": "السرعة والفاعلية في حل المشكلات ومتابعة العمل", "points": 30.0},
        {"question": "العدالة والمهنية في إدارة الفريق وتقييم الأداء", "points": 30.0},
    ],
    "gm": [
        {"question": "وضوح الرؤية والتوجيهات الإدارية للمدير العام", "points": 40.0},
        {"question": "دعم بيئة العمل وتوفير الموارد اللازمة للأداء", "points": 30.0},
        {"question": "الشفافية والحزم في تطبيق السياسات واللوائح", "points": 30.0},
    ],
}


def _save_org_settings_patch(patch: Dict[str, Any]) -> None:
    """حفظ تحديثات إعدادات المنظمة في قاعدة البيانات وفي session_state."""
    for k, v in patch.items():
        st.session_state[f"org_cache_{k}"] = v
    try:
        existing_rows = fetch_table("org_settings", limit=5)
        if existing_rows and existing_rows[0].get("id") is not None:
            rec_id = existing_rows[0]["id"]
            update_record("org_settings", rec_id, patch)
        else:
            upsert_record("org_settings", {"id": "main", **patch}, on_conflict="id")
    except Exception:
        pass


def _normalize_q_list(raw_list: Any, fallback_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(raw_list, list):
        return [dict(x) for x in fallback_list]
    cleaned: List[Dict[str, Any]] = []
    for item in raw_list:
        if isinstance(item, dict):
            q_str = str(item.get("question") or item.get("text") or "").strip()
            try:
                pts = float(item.get("points") if item.get("points") is not None else 10.0)
            except (ValueError, TypeError):
                pts = 10.0
            if q_str:
                cleaned.append({"question": q_str, "points": max(0.5, round_half_up_2(pts))})
        elif isinstance(item, str) and item.strip():
            cleaned.append({"question": item.strip(), "points": 10.0})
    return cleaned if cleaned else [dict(x) for x in fallback_list]


def _get_all_self_questions(org_settings: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    if "org_cache_kpi_self_questions" in st.session_state and isinstance(st.session_state["org_cache_kpi_self_questions"], dict):
        saved = st.session_state["org_cache_kpi_self_questions"]
    else:
        saved = org_settings.get("kpi_self_questions")
        if isinstance(saved, dict):
            st.session_state["org_cache_kpi_self_questions"] = saved

    out: Dict[str, List[Dict[str, Any]]] = {}
    for tab_k in KPI_SELF_TABS:
        fb = DEFAULT_SELF_QUESTIONS_BY_TAB.get(tab_k, [])
        if isinstance(saved, dict) and tab_k in saved:
            out[tab_k] = _normalize_q_list(saved.get(tab_k), fb)
        else:
            out[tab_k] = [dict(x) for x in fb]
    return out


def _get_all_peer_questions(org_settings: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    if "org_cache_kpi_peer_questions_v2" in st.session_state and isinstance(st.session_state["org_cache_kpi_peer_questions_v2"], dict):
        saved = st.session_state["org_cache_kpi_peer_questions_v2"]
    else:
        saved = org_settings.get("kpi_peer_questions_v2")
        if isinstance(saved, dict):
            st.session_state["org_cache_kpi_peer_questions_v2"] = saved

    out: Dict[str, List[Dict[str, Any]]] = {}
    for key_p in ("senior", "manager", "gm"):
        fb = DEFAULT_PEER_QUESTIONS_BY_TARGET.get(key_p, [])
        if isinstance(saved, dict) and key_p in saved:
            out[key_p] = _normalize_q_list(saved.get(key_p), fb)
        else:
            out[key_p] = [dict(x) for x in fb]
    return out


def _render_q_editor_block(
    section_title: str,
    q_list: List[Dict[str, Any]],
    form_key: str,
    del_prefix: str,
    on_change_callback: Any,
) -> None:
    """عرض قائمة الأسئلة مع درجتها وإمكانية الحذف أو إضافة سؤال جديد بدرجته."""
    st.markdown(f"##### {section_title}")
    tot_pts = round_half_up_2(sum(float(x.get("points") or 0) for x in q_list))
    st.caption(f"عدد الأسئلة: **{len(q_list)}** | إجمالي الدرجات: **{tot_pts:g}**")

    if q_list:
        for idx_q, item in enumerate(q_list):
            c_txt, c_pts, c_del = st.columns([4.2, 1.2, 0.8])
            c_txt.write(f"**{idx_q + 1}.** {item.get('question')}")
            c_pts.write(f"`{float(item.get('points') or 0):g} درجة`")
            if c_del.button("حذف", key=f"{del_prefix}_{idx_q}"):
                updated = [dict(x) for j, x in enumerate(q_list) if j != idx_q]
                on_change_callback(updated)
                st.rerun()
    else:
        st.info("لا توجد أسئلة مضافة في هذا القسم حالياً.")

    with st.form(form_key, clear_on_submit=True):
        f1, f2, f3 = st.columns([3.5, 1.3, 1.2])
        with f1:
            q_txt_inp = st.text_input("نص السؤال", placeholder="اكتب السؤال هنا")
        with f2:
            q_pts_inp = st.number_input("درجة السؤال", min_value=0.5, max_value=100.0, value=10.0, step=0.5)
        with f3:
            st.write("")
            st.write("")
            sub_btn = st.form_submit_button("➕ إضافة سؤال", type="primary")

        if sub_btn:
            clean_q = q_txt_inp.strip()
            if clean_q:
                updated = [dict(x) for x in q_list] + [{"question": clean_q, "points": float(q_pts_inp)}]
                on_change_callback(updated)
                st.success(f"✅ تمت إضافة السؤال ({clean_q}) بدرجة ({q_pts_inp:g}).")
                st.rerun()
            else:
                st.warning("يرجى كتابة نص السؤال أولاً.")


def _map_job_title_to_self_tab(emp: Dict[str, Any]) -> str:
    jt = str(emp.get("job_title") or "").strip().lower()
    role = str(emp.get("role") or "").strip().lower()

    if "مدير مباشر" in jt or role == "manager":
        return "مدير مباشر"
    if "كيميائي" in jt or "كيميائ" in jt or role == "senior":
        return "كيميائيين"
    if "خدمة عملاء" in jt or "كول" in jt:
        return "خدمة عملاء"
    if "سحب" in jt:
        return "استقبال وسحب عينات"
    if "استقبال" in jt:
        return "استقبال"
    return "استقبال"


def _get_fixed_allowance_types(org_settings: Dict[str, Any]) -> List[str]:
    """إرجاع قائمة أنواع المكافآت الثابتة المتاحة (الافتراضية + المضافة من المستخدم)."""
    merged: List[str] = list(DEFAULT_FIXED_ALLOWANCE_TYPES)
    custom_list = st.session_state.get("org_cache_fixed_allowance_types")
    if custom_list is None:
        custom_list = org_settings.get("fixed_allowance_types")
    if isinstance(custom_list, list):
        for item in custom_list:
            lbl = str(item or "").strip()
            if lbl and lbl not in merged:
                merged.append(lbl)
    return merged


def _dept_map(departments: List[Dict[str, Any]]) -> Dict[str, str]:
    return {str(d["id"]): d.get("name", "") for d in departments if d.get("id")}


def _emp_identifiers(emp: Dict[str, Any]) -> set:
    ids = set()
    if emp.get("id"):
        ids.add(str(emp["id"]))
    if emp.get("team_user_id"):
        ids.add(str(emp["team_user_id"]))
    return ids


def _calculate_work_hours(start_t: time, end_t: time) -> float:
    """حساب عدد ساعات العمل تلقائياً بين وقت البداية ووقت النهاية."""
    start_mins = start_t.hour * 60 + start_t.minute
    end_mins = end_t.hour * 60 + end_t.minute
    if end_mins < start_mins:
        end_mins += 24 * 60
    diff_mins = end_mins - start_mins
    return round_half_up_2(diff_mins / 60.0)


def _parse_time_safe(val: Any, default_t: time) -> time:
    if isinstance(val, time):
        return val
    if isinstance(val, datetime):
        return val.time()
    if isinstance(val, str):
        txt = val.strip()[:5]
        try:
            return datetime.strptime(txt, "%H:%M").time()
        except ValueError:
            pass
        try:
            return datetime.strptime(val.strip()[:8], "%H:%M:%S").time()
        except ValueError:
            pass
    return default_t


def _to_minutes_from_any_time(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        t = val.time()
        return t.hour * 60 + t.minute
    if isinstance(val, time):
        return val.hour * 60 + val.minute
    if isinstance(val, str):
        txt = val.strip()
        if not txt or txt.lower() in ("none", "nan", "—", "-"):
            return None
        parts = txt.split(":")
        if len(parts) >= 2:
            try:
                hh = int(parts[0])
                mm = int(parts[1])
                if 0 <= hh <= 23 and 0 <= mm <= 59:
                    return hh * 60 + mm
            except ValueError:
                return None
    return None


def _compute_attendance_breakdown(
    rec: Dict[str, Any],
    emp: Dict[str, Any],
    grace_late_minutes: int = 15,
    grace_early_minutes: int = 5,
) -> Dict[str, int]:
    """
    تفصيل الحضور والانصراف بعد خصم السماح المقرر من الإعدادات:
    - تأخير دخول: صافي دقائق التأخير بعد خصم سماح الحضور.
    - إضافي دخول: دقائق الحضور المبكر قبل بداية الدوام.
    - انصراف مبكر: صافي دقائق الانصراف المبكر بعد خصم سماح الانصراف.
    - إضافي الحضور والانصراف: دقائق العمل بعد نهاية الدوام.
    """
    s_start_m = _to_minutes_from_any_time(rec.get("scheduled_start") or emp.get("work_start_time") or "09:00:00")
    if s_start_m is None:
        s_start_m = 9 * 60

    s_end_m = _to_minutes_from_any_time(rec.get("scheduled_end") or emp.get("work_end_time") or "17:00:00")
    if s_end_m is None:
        s_end_m = 17 * 60
    if s_end_m <= s_start_m:
        s_end_m += 24 * 60

    cin_m = _to_minutes_from_any_time(rec.get("check_in"))
    cout_m = _to_minutes_from_any_time(rec.get("check_out"))

    late_grace = max(0, int(grace_late_minutes or 0))
    early_grace = max(0, int(grace_early_minutes or 0))

    late_in = 0
    early_in = 0
    if cin_m is not None:
        diff_in = cin_m - s_start_m
        if diff_in > late_grace:
            late_in = int(diff_in - late_grace)
        elif diff_in < 0:
            early_in = int(abs(diff_in))
    else:
        raw_l = int(rec.get("late_minutes") or 0)
        late_in = max(0, raw_l - late_grace) if raw_l > late_grace else 0

    early_out = 0
    overtime_out = 0
    if cout_m is not None:
        w_cout_m = cout_m + (24 * 60 if cout_m < s_start_m else 0)
        if w_cout_m < s_end_m:
            raw_e = int(s_end_m - w_cout_m)
            if raw_e > early_grace:
                early_out = int(raw_e - early_grace)
        elif w_cout_m > s_end_m:
            overtime_out = int(w_cout_m - s_end_m)
    else:
        raw_e = int(rec.get("early_leave_minutes") or 0)
        early_out = max(0, raw_e - early_grace) if raw_e > early_grace else 0
        if int(rec.get("overtime_minutes") or 0) > 0:
            overtime_out = int(rec.get("overtime_minutes") or 0)

    return {
        "late_in": late_in,
        "early_in": early_in,
        "early_out": early_out,
        "overtime_out": overtime_out,
    }


def _normalize_time_text(raw_val: Any) -> Optional[str]:
    """تنظيف وتحويل أي صيغة وقت مقبولة (مثل 9:00 أو 09:00 أو 09:00:00) إلى HH:MM:SS."""
    if raw_val is None:
        return None
    s = str(raw_val).strip()
    if not s or s.lower() in ("none", "nan", "—", "-"):
        return None
    parts = s.split(":")
    if len(parts) < 2:
        return None
    try:
        hh = int(parts[0])
        mm = int(parts[1])
        ss = int(parts[2]) if len(parts) >= 3 else 0
        if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
            return None
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    except ValueError:
        return None


def _get_holiday_period_bounds(hol: Dict[str, Any]) -> tuple:
    """إرجاع (تاريخ البداية، تاريخ النهاية، عدد الأيام) لسجل إجازة رسمية."""
    s_dt = parse_date_safe(hol.get("start_date")) or parse_date_safe(hol.get("holiday_date"))
    e_dt = parse_date_safe(hol.get("end_date")) or s_dt
    if s_dt and e_dt and e_dt >= s_dt:
        cnt = int((e_dt - s_dt).days + 1)
        return s_dt, e_dt, cnt
    cnt_saved = int(hol.get("days_count") or 1)
    return s_dt, e_dt, max(1, cnt_saved)


def _get_non_payroll_holiday_map(official_holidays: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    خريطة {YYYY-MM-DD: 'إجازة رسمية — ...'} للإجازات الرسمية التي لا تُضاف للمرتب (add_day_to_payroll == False).
    """
    out: Dict[str, str] = {}
    for hol in official_holidays:
        if hol.get("active", True) is False:
            continue
        if bool(hol.get("add_day_to_payroll", False)):
            continue
        s_dt, e_dt, _ = _get_holiday_period_bounds(hol)
        if not s_dt or not e_dt or e_dt < s_dt:
            continue
        h_name = (hol.get("name_ar") or "إجازة رسمية").strip()
        cur = s_dt
        while cur <= e_dt:
            out[cur.isoformat()] = f"إجازة رسمية ({h_name})"
            cur += timedelta(days=1)
    return out


def _sync_leave_days_state() -> None:
    """تحديث عدد أيام الإجازة في session_state فوراً عند تغيير تاريخ البداية أو النهاية."""
    s = parse_date_safe(st.session_state.get("lr_start_date_input"))
    e = parse_date_safe(st.session_state.get("lr_end_date_input"))
    if not s or not e or e < s:
        st.session_state["calculated_leave_days"] = 0
    else:
        st.session_state["calculated_leave_days"] = int((e - s).days + 1)


def _add_months_ym(ym_str: str, offset_months: int) -> str:
    """إضافة عدد من الشهور إلى شهر بصيغة YYYY-MM وإرجاع النتيجة بصيغة YYYY-MM."""
    try:
        parts = str(ym_str or "").strip()[:7].split("-")
        yr = int(parts[0])
        mo = int(parts[1])
    except Exception:
        today_dt = date.today()
        yr = today_dt.year
        mo = today_dt.month

    total_months = (yr * 12 + (mo - 1)) + int(offset_months)
    new_yr = total_months // 12
    new_mo = (total_months % 12) + 1
    return f"{new_yr:04d}-{new_mo:02d}"


def _get_cycle_dates_for_month(ym_str: str) -> List[date]:
    """إرجاع قائمة التواريخ الكاملة للشهر الإداري (من 21 الشهر السابق إلى 20 الشهر المختار)."""
    try:
        parts = str(ym_str or "").strip()[:7].split("-")
        yr = int(parts[0])
        mo = int(parts[1])
        end_dt = date(yr, mo, 20)
    except Exception:
        today_dt = date.today()
        end_dt = date(today_dt.year, today_dt.month, 20)

    prev_yr = end_dt.year - 1 if end_dt.month == 1 else end_dt.year
    prev_mo = 12 if end_dt.month == 1 else end_dt.month - 1
    start_dt = date(prev_yr, prev_mo, 21)

    days_list: List[date] = []
    cur = start_dt
    while cur <= end_dt:
        days_list.append(cur)
        cur += timedelta(days=1)
    return days_list


def _get_leave_map_for_employee(
    emp: Dict[str, Any],
    leave_requests: List[Dict[str, Any]],
) -> Dict[str, str]:
    """بناء خريطة {YYYY-MM-DD: وصف الإجازة} للموظف من طلبات الإجازة غير المرفوضة/الملغاة."""
    emp_ids = _emp_identifiers(emp)
    leave_by_day: Dict[str, str] = {}

    for lr in leave_requests:
        req_emp = str(lr.get("employee_id") or "")
        req_user = str(lr.get("employee_user_id") or "")
        if req_emp not in emp_ids and (not req_user or req_user not in emp_ids):
            continue

        status = (lr.get("status") or "").strip().lower()
        if status.startswith("rejected") or status.startswith("cancelled"):
            continue

        s_dt = parse_date_safe(lr.get("start_date"))
        e_dt = parse_date_safe(lr.get("end_date"))
        if not s_dt or not e_dt or e_dt < s_dt:
            continue

        t_name = (lr.get("type_name") or "").strip()
        t_code = (lr.get("type_code") or "").strip().lower()
        if not t_name:
            t_name = {
                "casual": "عارضة",
                "regular": "اعتيادي",
                "annual": "سنوي",
                "sick": "مرضي",
                "unpaid": "إجازة بالخصم",
                "deduction": "إجازة بالخصم",
            }.get(t_code, t_code or "إجازة")

        lbl = f"إجازة — {t_name}" + (" (قيد الاعتماد)" if status != "approved" else "")
        cur = s_dt
        while cur <= e_dt:
            leave_by_day[cur.isoformat()] = lbl
            cur += timedelta(days=1)

    return leave_by_day


def _build_advance_installments_preview(
    total_amount: float,
    start_ym: str,
    installments_count: int,
    monthly_installment: float,
    last_installment_amount: float,
) -> List[Dict[str, Any]]:
    """إنشاء جدول معاينة مرتب بالشهور لأقساط السلفة."""
    rows: List[Dict[str, Any]] = []
    for idx in range(1, max(1, int(installments_count)) + 1):
        amt = last_installment_amount if idx == int(installments_count) else monthly_installment
        rows.append({
            "رقم القسط": idx,
            "الشهر": _add_months_ym(start_ym, idx - 1),
            "قيمة القسط (ج)": amt,
        })
    return rows


def _build_remaining_schedule_for_employee(
    emp_id: str,
    target_ym: str,
    advances: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """حساب السلف المتبقية على مدار الشهور لموظف معين بدءاً من الشهر المستهدف."""
    monthly_totals: Dict[str, float] = {}
    total_remaining_all = 0.0

    for adv in advances:
        if str(adv.get("employee_id") or "") != str(emp_id):
            continue
        st_val = (adv.get("status") or "").lower()
        if st_val in ("rejected", "cancelled", "settled"):
            continue

        rem = round_half_up_2(float(adv.get("remaining_amount") or 0))
        if rem <= 0:
            continue

        total_remaining_all = round_half_up_2(total_remaining_all + rem)
        cnt = max(1, int(adv.get("installments_count") or 1))
        m_inst = round_half_up_2(float(adv.get("monthly_installment") or rem))
        l_inst = round_half_up_2(float(adv.get("last_installment_amount") or m_inst))
        start_m = str(adv.get("start_payroll_month") or target_ym)[:7]

        base_m = max(start_m, target_ym[:7])
        rem_left = rem
        for idx in range(cnt):
            if rem_left <= 0:
                break
            inst_amt = l_inst if (idx == cnt - 1 and l_inst > 0) else m_inst
            inst_amt = min(inst_amt, rem_left)
            ym_slot = _add_months_ym(base_m, idx)
            monthly_totals[ym_slot] = round_half_up_2(monthly_totals.get(ym_slot, 0.0) + inst_amt)
            rem_left = round_half_up_2(rem_left - inst_amt)

    sorted_months = sorted(monthly_totals.keys())
    schedule_rows = [
        {
            "الشهر": ym,
            "قيمة القسط / السلفة (ج)": monthly_totals[ym],
        }
        for ym in sorted_months
    ]
    end_ym = sorted_months[-1] if sorted_months else "—"
    return {
        "total_remaining": total_remaining_all,
        "schedule_rows": schedule_rows,
        "end_month": end_ym,
    }


def _get_subordinate_employees(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """إرجاع الموظفين الذين هم تحت سلطة المستخدم الحالي فقط."""
    user_role = (current_user.get("role") or "employee").strip().lower()
    my_ids = _emp_identifiers(current_user)
    my_dept = str(current_user.get("department_id") or "")

    if user_role == "general_manager":
        return [
            e for e in employees
            if (e.get("status") or "active") == "active"
            and not (_emp_identifiers(e) & my_ids)
        ]

    subordinates: List[Dict[str, Any]] = []
    for e in employees:
        if (e.get("status") or "active") != "active":
            continue
        e_ids = _emp_identifiers(e)
        if e_ids & my_ids:
            continue

        e_role = (e.get("role") or "employee").strip().lower()
        if user_role == "senior":
            if str(e.get("senior_id") or "") in my_ids:
                subordinates.append(e)
        elif user_role == "manager":
            if e_role in ("general_manager", "chairman", "vice_chairman"):
                continue
            is_direct = str(e.get("direct_manager_id") or "") in my_ids
            same_dept = bool(my_dept) and str(e.get("department_id") or "") == my_dept
            if is_direct or same_dept:
                subordinates.append(e)

    return subordinates


def _get_advance_eligible_targets(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """تحديد الموظفين المسموح بتقديم طلب سلفة لهم."""
    user_role = (current_user.get("role") or "employee").strip().lower()
    my_ids = _emp_identifiers(current_user)

    if user_role == "general_manager":
        return [e for e in employees if (e.get("status") or "active") == "active"]

    targets: List[Dict[str, Any]] = []
    seen_ids = set()

    if current_user.get("id"):
        targets.append(current_user)
        seen_ids.update(my_ids)

    if user_role == "manager":
        for se in _get_subordinate_employees(current_user, employees):
            se_ids = _emp_identifiers(se)
            if not (se_ids & seen_ids):
                targets.append(se)
                seen_ids.update(se_ids)

    return targets


def _filter_visible_advances(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """تصفية سجل السلف حسب الدور والتبعية."""
    user_role = (current_user.get("role") or "employee").strip().lower()
    if user_role in ("general_manager", "chairman", "vice_chairman", "hr"):
        return advances

    my_ids = _emp_identifiers(current_user)
    if user_role == "manager":
        allowed_ids = set(my_ids)
        for se in _get_subordinate_employees(current_user, employees):
            allowed_ids.update(_emp_identifiers(se))
        return [
            adv for adv in advances
            if str(adv.get("employee_id") or "") in allowed_ids
        ]

    return [
        adv for adv in advances
        if str(adv.get("employee_id") or "") in my_ids
    ]


def _filter_visible_penalties_rewards(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """تصفية سجل الجزاءات والمكافآت حسب الدور والتبعية."""
    user_role = (current_user.get("role") or "employee").strip().lower()
    if user_role in ("general_manager", "chairman", "vice_chairman", "hr"):
        return penalties_rewards

    my_ids = _emp_identifiers(current_user)
    if user_role == "employee":
        return [
            pr for pr in penalties_rewards
            if str(pr.get("employee_id") or "") in my_ids
        ]

    allowed_emp_ids = set(my_ids)
    for se in _get_subordinate_employees(current_user, employees):
        allowed_emp_ids.update(_emp_identifiers(se))

    return [
        pr for pr in penalties_rewards
        if str(pr.get("employee_id") or "") in allowed_emp_ids
    ]


# =====================================================================
# تجميع كافة الموافقات المعلقة في مكان واحد
# =====================================================================
def get_pending_items_bundle(
    current_user: Dict[str, Any],
    leave_requests: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    employees: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    perms = get_effective_permissions(current_user)
    if perms.get("is_monitor_only"):
        return {"leaves": [], "pr": [], "advances": [], "overtime": []}

    user_ids = _emp_identifiers(current_user)
    user_role = (current_user.get("role") or "employee").strip().lower()
    all_emps = employees or []
    emp_by_any_id: Dict[str, Dict[str, Any]] = {}
    for e in all_emps:
        for k in _emp_identifiers(e):
            emp_by_any_id[k] = e

    pending_leaves: List[Dict[str, Any]] = []
    for lr in leave_requests:
        st_val = (lr.get("status") or "").lower()
        backup_id = str(lr.get("backup_employee_id") or "")

        if st_val == "pending_backup":
            if backup_id and backup_id in user_ids:
                pending_leaves.append(lr)
            elif not backup_id and user_role == "general_manager":
                pending_leaves.append(lr)
        elif perms.get("approve_leaves"):
            if user_role == "general_manager" and st_val in ("pending_senior", "pending_manager", "pending_gm", "pending"):
                pending_leaves.append(lr)
            elif st_val == "pending_senior" and str(lr.get("senior_id") or "") in user_ids:
                pending_leaves.append(lr)
            elif st_val == "pending_manager" and str(lr.get("direct_manager_id") or "") in user_ids:
                pending_leaves.append(lr)

    pending_pr: List[Dict[str, Any]] = []
    if perms.get("approve_penalties") or perms.get("approve_rewards"):
        for pr in penalties_rewards:
            if (pr.get("status") or "").lower() != "pending":
                continue
            etype = (pr.get("entry_type") or "").lower()
            if user_role == "general_manager":
                pending_pr.append(pr)
            elif etype == "reward" and perms.get("approve_rewards"):
                pending_pr.append(pr)
            elif etype == "penalty" and perms.get("approve_penalties"):
                pending_pr.append(pr)

    pending_adv: List[Dict[str, Any]] = []
    if perms.get("approve_advances") and user_role in ("manager", "general_manager"):
        for adv in advances:
            st_val = (adv.get("status") or "").lower()
            req_emp_id = str(adv.get("employee_id") or "")
            req_emp_obj = emp_by_any_id.get(req_emp_id, {})
            req_mgr_id = str(req_emp_obj.get("direct_manager_id") or "")
            same_dept = (
                bool(current_user.get("department_id"))
                and bool(req_emp_obj.get("department_id"))
                and str(req_emp_obj.get("department_id")) == str(current_user.get("department_id"))
            )
            is_my_subordinate = (req_mgr_id in user_ids) or same_dept

            if user_role == "general_manager" and st_val in ("pending", "manager_approved"):
                pending_adv.append(adv)
            elif user_role == "manager" and st_val == "pending" and is_my_subordinate:
                pending_adv.append(adv)

    pending_ot: List[Dict[str, Any]] = []
    for att in attendance_logs:
        if int(att.get("overtime_minutes") or 0) <= 0:
            continue
        st_ot = (att.get("overtime_status") or "").strip().lower()
        if st_ot in ("approved", "rejected", "none", ""):
            continue

        req_emp_id = str(att.get("employee_id") or "")
        req_emp_obj = emp_by_any_id.get(req_emp_id, {})
        req_senior_id = str(req_emp_obj.get("senior_id") or "").strip()
        req_mgr_id = str(req_emp_obj.get("direct_manager_id") or "").strip()

        if st_ot == "pending_senior":
            is_my_senior = bool(req_senior_id and req_senior_id in user_ids)
            if is_my_senior or (user_role == "general_manager" and not req_senior_id):
                pending_ot.append(att)
        elif st_ot == "pending_manager":
            is_my_mgr = bool(req_mgr_id and req_mgr_id in user_ids)
            if is_my_mgr or (user_role == "manager" and not req_mgr_id):
                pending_ot.append(att)
        elif st_ot == "pending_gm":
            if user_role == "general_manager":
                pending_ot.append(att)

    return {
        "leaves": pending_leaves,
        "pr": pending_pr,
        "advances": pending_adv,
        "overtime": pending_ot,
    }


def get_pending_approvals_count(
    current_user: Dict[str, Any],
    leave_requests: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    employees: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """إجمالي عدد الإشعارات المعلقة على زر الموافقات الرئيسي."""
    bundle = get_pending_items_bundle(
        current_user, leave_requests, penalties_rewards, advances, attendance_logs, employees=employees
    )
    return (
        len(bundle["leaves"])
        + len(bundle["pr"])
        + len(bundle["advances"])
        + len(bundle["overtime"])
    )


def _filter_visible_leave_requests(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    leave_requests: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    user_role = (current_user.get("role") or "employee").lower()
    if user_role in ("general_manager", "chairman", "vice_chairman", "hr"):
        return leave_requests

    my_ids = _emp_identifiers(current_user)
    subordinate_ids = set(my_ids)

    for e in employees:
        e_ids = _emp_identifiers(e)
        if user_role == "senior":
            if str(e.get("senior_id") or "") in my_ids:
                subordinate_ids.update(e_ids)
        elif user_role == "manager":
            same_dept = (
                bool(current_user.get("department_id"))
                and str(e.get("department_id") or "") == str(current_user.get("department_id") or "")
            )
            is_direct = str(e.get("direct_manager_id") or "") in my_ids
            if is_direct or same_dept:
                subordinate_ids.update(e_ids)

    visible = []
    for lr in leave_requests:
        req_emp_id = str(lr.get("employee_id") or "")
        req_user_id = str(lr.get("employee_user_id") or "")
        req_senior_id = str(lr.get("senior_id") or "")
        req_mgr_id = str(lr.get("direct_manager_id") or "")
        req_backup_id = str(lr.get("backup_employee_id") or "")

        if (
            req_emp_id in subordinate_ids
            or (req_user_id and req_user_id in subordinate_ids)
            or (req_senior_id and req_senior_id in my_ids)
            or (req_mgr_id and req_mgr_id in my_ids)
            or (req_backup_id and req_backup_id in my_ids)
        ):
            visible.append(lr)
    return visible


# =====================================================================
# 1. الداش بورد التنفيذية
# =====================================================================
def render_dashboard(
    employees: List[Dict[str, Any]],
    departments: List[Dict[str, Any]],
    shifts: List[Dict[str, Any]],
    leave_requests: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
) -> None:
    st.markdown("### 📊 الداش بورد التنفيذية للمعمل")

    active_emps = [e for e in employees if (e.get("status") or "active") == "active"]
    dept_names = _dept_map(departments)
    today = date.today()
    today_iso = today.isoformat()
    current_ym = today.strftime("%Y-%m")

    on_leave_today = []
    for lr in leave_requests:
        if (lr.get("status") or "").lower() != "approved":
            continue
        s_dt = str(lr.get("start_date") or "")[:10]
        e_dt = str(lr.get("end_date") or "")[:10]
        if s_dt <= today_iso <= e_dt:
            on_leave_today.append(lr)

    total_adv_due = sum(
        get_employee_monthly_advances_summary(str(e["id"]), current_ym, advances)["total_due"]
        for e in active_emps
        if e.get("id")
    )

    late_by_emp: Dict[str, Dict[str, Any]] = {}
    emp_by_id = {str(e["id"]): e for e in employees if e.get("id")}
    for att in attendance_logs:
        if str(att.get("work_date") or "")[:7] != current_ym:
            continue
        lm = int(att.get("late_minutes") or 0)
        if lm > 0:
            eid = str(att.get("employee_id"))
            emp_info = emp_by_id.get(eid, {})
            if eid not in late_by_emp:
                late_by_emp[eid] = {
                    "الموظف": emp_info.get("name_ar", "غير معروف"),
                    "الرقم الوظيفي": emp_info.get("employee_no", ""),
                    "الفرع": dept_names.get(str(emp_info.get("department_id") or ""), "—"),
                    "مرات التأخير": 0,
                    "إجمالي دقائق التأخير": 0,
                }
            late_by_emp[eid]["مرات التأخير"] += 1
            late_by_emp[eid]["إجمالي دقائق التأخير"] += lm

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("إجمالي الموظفين النشطين", len(active_emps))
    c2.metric("في إجازة اليوم", len(on_leave_today))
    c3.metric("سلف مستحقة للشهر", f"{round_half_up_2(total_adv_due):,.2f} ج")
    c4.metric("موظفون لديهم تأخيرات الشهر", len(late_by_emp))

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("#### 🌴 الموظفون في إجازة اليوم ومواقعهم")
        if on_leave_today:
            leave_today_table = []
            for lr in on_leave_today:
                emp_obj = emp_by_id.get(str(lr.get("employee_id") or ""), {})
                leave_today_table.append({
                    "الموظف": lr.get("employee_name") or emp_obj.get("name_ar", ""),
                    "نوع الإجازة": lr.get("type_name") or lr.get("type_code", ""),
                    "الفرع": dept_names.get(str(lr.get("department_id") or emp_obj.get("department_id") or ""), "—"),
                    "إلى تاريخ": str(lr.get("end_date") or "")[:10],
                })
            st.dataframe(leave_today_table, use_container_width=True, hide_index=True)
        else:
            st.success("لا يوجد موظفون في إجازة معتمدة اليوم.")

    with col_right:
        st.markdown("#### ⏱️ رادار أكثر الموظفين تأخيراً (الشهر الحالي)")
        if late_by_emp:
            sorted_late = sorted(
                late_by_emp.values(),
                key=lambda x: x["إجمالي دقائق التأخير"],
                reverse=True,
            )[:10]
            st.dataframe(sorted_late, use_container_width=True, hide_index=True)
        else:
            st.success("لا توجد تأخيرات مسجلة خلال الشهر الحالي.")


# =====================================================================
# 2. الشاشة الموحدة الوحيدة للموافقات
# =====================================================================
def render_approvals_inbox(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    leave_balances: List[Dict[str, Any]],
    leave_requests: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
) -> None:
    st.markdown("### 🔔 صندوق الموافقات الموحّد")
    perms = get_effective_permissions(current_user)
    user_id = str(current_user.get("id"))
    user_role = (current_user.get("role") or "employee").lower()
    emp_by_id = {str(e["id"]): e for e in employees if e.get("id")}

    if perms.get("is_monitor_only"):
        st.info("حساب رئيس مجلس الإدارة / نائب الرئيس مخصص للمتابعة والاطلاع فقط بدون صلاحية موافقات.")
        return

    bundle = get_pending_items_bundle(
        current_user, leave_requests, penalties_rewards, advances, attendance_logs, employees=employees
    )
    total_pending = (
        len(bundle["leaves"])
        + len(bundle["pr"])
        + len(bundle["advances"])
        + len(bundle["overtime"])
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("إجمالي المنتظر", total_pending)
    m2.metric("الإجازات", len(bundle["leaves"]))
    m3.metric("الجزاءات والمكافآت", len(bundle["pr"]))
    m4.metric("السلف", len(bundle["advances"]))
    m5.metric("الوقت الإضافي", len(bundle["overtime"]))

    if total_pending == 0:
        st.success("لا توجد أي طلبات موافقة معلقة لحسابك حالياً.")
        return

    bal_by_emp: Dict[str, Dict[str, Any]] = {}
    for b in leave_balances:
        if b.get("employee_id"):
            bal_by_emp[str(b["employee_id"])] = b
        if b.get("team_user_id"):
            bal_by_emp[str(b["team_user_id"])] = b

    if bundle["leaves"]:
        st.markdown("#### 🗓️ موافقات الإجازات")
        for lr in bundle["leaves"]:
            st_val = (lr.get("status") or "").lower()
            with st.container(border=True):
                backup_txt = f" | البديل: **{lr.get('backup_employee_name')}**" if lr.get("backup_employee_name") else ""
                stage_badge = "موافقة الموظف البديل" if st_val == "pending_backup" else st_val
                st.write(
                    f"**{lr.get('employee_name')}** — {lr.get('type_name')} | "
                    f"من `{str(lr.get('start_date'))[:10]}` إلى `{str(lr.get('end_date'))[:10]}` "
                    f"(**{lr.get('days')} يوم**){backup_txt} | المرحلة: `{stage_badge}`"
                )
                if lr.get("reason"):
                    st.caption(f"السبب: {lr.get('reason')}")
                if lr.get("attachment_url"):
                    st.markdown(f"[📎 عرض التقرير المرفق]({lr.get('attachment_url')})")

                note_inp = st.text_input("ملاحظة الاعتماد/الرفض", key=f"unif_note_lr_{lr['id']}")
                col_a, col_r, _ = st.columns([1.4, 1.2, 4])

                with col_a:
                    app_btn_lbl = "✅ موافقة كبديل" if st_val == "pending_backup" else "✅ موافقة"
                    if st.button(app_btn_lbl, key=f"unif_app_lr_{lr['id']}", type="primary"):
                        approvals = list(lr.get("approvals") or [])
                        if st_val == "pending_backup":
                            stage = "backup"
                            if lr.get("senior_id"):
                                next_status = "pending_senior"
                            elif lr.get("direct_manager_id"):
                                next_status = "pending_manager"
                            else:
                                next_status = "pending_gm"
                        elif st_val == "pending_senior":
                            stage = "senior"
                            next_status = "pending_manager" if lr.get("direct_manager_id") else "pending_gm"
                        elif st_val == "pending_manager":
                            stage = "manager"
                            next_status = "pending_gm"
                        else:
                            stage = "gm"
                            next_status = "approved"

                        approvals.append({
                            "at": datetime.utcnow().isoformat() + "Z",
                            "name": current_user.get("name_ar"),
                            "stage": stage,
                            "decision": "approved",
                            "notes": note_inp,
                        })
                        update_record("leave_requests", lr["id"], {
                            "status": next_status,
                            "approvals": approvals,
                        })

                        if next_status == "approved" and lr.get("deducts"):
                            emp_key = str(lr.get("employee_id"))
                            bal_rec = bal_by_emp.get(emp_key) or bal_by_emp.get(str(lr.get("employee_user_id") or ""))
                            if bal_rec and bal_rec.get("id"):
                                bf = (lr.get("balance_field") or "annual").lower()
                                d_amt = float(lr.get("days") or 0)
                                if bf == "casual":
                                    new_used = round_half_up_2(float(bal_rec.get("used_casual") or 0) + d_amt)
                                    update_record("leave_balances", bal_rec["id"], {"used_casual": new_used})
                                else:
                                    new_used = round_half_up_2(float(bal_rec.get("used_annual") or 0) + d_amt)
                                    update_record("leave_balances", bal_rec["id"], {"used_annual": new_used})

                        write_audit_log(
                            "leave_requests",
                            f"{stage}_approved",
                            "اعتماد",
                            f"اعتماد طلب إجازة {lr.get('employee_name')} ({lr.get('type_name')}) -> {next_status}",
                            by_name=current_user.get("name_ar", ""),
                            by_user_id=user_id,
                        )
                        st.rerun()

                with col_r:
                    if st.button("❌ رفض", key=f"unif_rej_lr_{lr['id']}"):
                        approvals = list(lr.get("approvals") or [])
                        stage = (
                            "backup"
                            if st_val == "pending_backup"
                            else ("senior" if st_val == "pending_senior" else ("manager" if st_val == "pending_manager" else "gm"))
                        )
                        rej_status = f"rejected_{stage}"
                        approvals.append({
                            "at": datetime.utcnow().isoformat() + "Z",
                            "name": current_user.get("name_ar"),
                            "stage": stage,
                            "decision": "rejected",
                            "notes": note_inp,
                        })
                        update_record("leave_requests", lr["id"], {
                            "status": rej_status,
                            "approvals": approvals,
                        })
                        write_audit_log(
                            "leave_requests",
                            rej_status,
                            "رفض",
                            f"رفض طلب إجازة {lr.get('employee_name')} ({lr.get('type_name')})",
                            by_name=current_user.get("name_ar", ""),
                            by_user_id=user_id,
                        )
                        st.rerun()

    if bundle["pr"]:
        st.markdown("#### ⚖️ موافقات الجزاءات والمكافآت")
        for pr in bundle["pr"]:
            emp_o = emp_by_id.get(str(pr.get("employee_id") or ""), {})
            kind_ar = "🔴 جزاء" if pr.get("entry_type") == "penalty" else "🟢 مكافأة"
            with st.container(border=True):
                st.write(
                    f"**{kind_ar}** — **{emp_o.get('name_ar', '')}** | "
                    f"القيمة: `{pr.get('value')}` ({'أيام' if pr.get('calc_mode') == 'days' else 'جنيه'}) "
                    f"= **{pr.get('calculated_amount')} ج** | شهر: `{pr.get('payroll_month')}`"
                )
                if pr.get("title") or pr.get("reason"):
                    st.caption(f"{pr.get('title') or ''} — {pr.get('reason') or ''}")
                ca, cr, _ = st.columns([1.2, 1.2, 4])
                with ca:
                    if st.button("✅ اعتماد", key=f"unif_app_pr_{pr['id']}", type="primary"):
                        update_record("penalties_rewards", pr["id"], {"status": "approved", "approved_by_gm": user_id})
                        write_audit_log(
                            "penalties_rewards",
                            "approve_pr",
                            "اعتماد",
                            f"اعتماد {kind_ar} للموظف {emp_o.get('name_ar', '')} بقيمة {pr.get('calculated_amount')} ج",
                            by_name=current_user.get("name_ar", ""),
                            by_user_id=user_id,
                        )
                        st.rerun()
                with cr:
                    if st.button("❌ رفض", key=f"unif_rej_pr_{pr['id']}"):
                        update_record("penalties_rewards", pr["id"], {"status": "rejected"})
                        write_audit_log(
                            "penalties_rewards",
                            "reject_pr",
                            "رفض",
                            f"رفض {kind_ar} للموظف {emp_o.get('name_ar', '')}",
                            by_name=current_user.get("name_ar", ""),
                            by_user_id=user_id,
                        )
                        st.rerun()

    if bundle["advances"]:
        st.markdown("#### 💳 موافقات السلف")
        for adv in bundle["advances"]:
            emp_o = emp_by_id.get(str(adv.get("employee_id") or ""), {})
            st_val = (adv.get("status") or "").lower()
            with st.container(border=True):
                mode_ar = (
                    "خصم كامل في نفس الشهر"
                    if adv.get("repayment_mode") == "single_month"
                    else f"تقسيط على {adv.get('installments_count')} شهر"
                )
                st.write(
                    f"**{emp_o.get('name_ar', '')}** — الإجمالي: `{adv.get('total_amount')} ج` | "
                    f"النظام: `{mode_ar}` (قسط `{adv.get('monthly_installment')} ج`) | "
                    f"بداية الخصم: `{adv.get('start_payroll_month')}` | الحالة: `{st_val}`"
                )
                if adv.get("reason"):
                    st.caption(f"السبب: {adv.get('reason')}")
                ca, cr, _ = st.columns([1.4, 1.2, 4.4])
                with ca:
                    if st_val == "pending" and user_role == "manager":
                        if st.button("✅ موافقة مدير مباشر", key=f"unif_mgr_adv_{adv['id']}", type="primary"):
                            update_record("advances", adv["id"], {
                                "status": "manager_approved",
                                "manager_approved_at": datetime.utcnow().isoformat() + "Z",
                            })
                            st.rerun()
                    elif user_role == "general_manager":
                        if st.button("✅ اعتماد مدير عام", key=f"unif_gm_adv_{adv['id']}", type="primary"):
                            update_record("advances", adv["id"], {
                                "status": "gm_approved",
                                "gm_approved_at": datetime.utcnow().isoformat() + "Z",
                            })
                            st.rerun()
                with cr:
                    if st.button("❌ رفض", key=f"unif_rej_adv_{adv['id']}"):
                        update_record("advances", adv["id"], {"status": "rejected"})
                        st.rerun()

    if bundle["overtime"]:
        st.markdown("#### 🕒 موافقات الوقت الإضافي (سينيور -> مدير مباشر -> مدير عام)")
        for rec in bundle["overtime"]:
            emp_o = emp_by_id.get(str(rec.get("employee_id") or ""), {})
            st_ot = (rec.get("overtime_status") or "").strip().lower()
            has_mgr = bool(str(emp_o.get("direct_manager_id") or "").strip())

            stage_label_ar = {
                "pending_senior": "في انتظار موافقة السينيور",
                "pending_manager": "في انتظار موافقة المدير المباشر",
                "pending_gm": "في انتظار اعتماد المدير العام",
            }.get(st_ot, st_ot)

            with st.container(border=True):
                st.write(
                    f"**{emp_o.get('name_ar', '')}** — التاريخ: `{str(rec.get('work_date'))[:10]}` | "
                    f"الانصراف: `{str(rec.get('check_out') or '')[:5]}` (نهاية الدوام: `{str(rec.get('scheduled_end') or '')[:5]}`) | "
                    f"الوقت الإضافي: **`{rec.get('overtime_minutes')} دقيقة`** | المرحلة: `{stage_label_ar}`"
                )
                ca, cr, _ = st.columns([1.6, 1.2, 4.2])
                with ca:
                    if st_ot == "pending_senior":
                        if st.button("✅ موافقة سينيور", key=f"unif_ot_sen_{rec['id']}", type="primary"):
                            next_ot_st = "pending_manager" if has_mgr else "pending_gm"
                            update_record("attendance_logs", rec["id"], {
                                "overtime_status": next_ot_st,
                                "overtime_senior_by": user_id,
                            })
                            write_audit_log(
                                "attendance_logs",
                                "ot_senior_approved",
                                "اعتماد",
                                f"موافقة السينيور على وقت إضافي ({rec.get('overtime_minutes')} د) للموظف {emp_o.get('name_ar', '')} -> {next_ot_st}",
                                by_name=current_user.get("name_ar", ""),
                                by_user_id=user_id,
                            )
                            st.rerun()
                    elif st_ot == "pending_manager":
                        if st.button("✅ موافقة مدير مباشر", key=f"unif_ot_mgr_{rec['id']}", type="primary"):
                            update_record("attendance_logs", rec["id"], {
                                "overtime_status": "pending_gm",
                                "overtime_manager_by": user_id,
                            })
                            write_audit_log(
                                "attendance_logs",
                                "ot_manager_approved",
                                "اعتماد",
                                f"موافقة المدير المباشر على وقت إضافي ({rec.get('overtime_minutes')} د) للموظف {emp_o.get('name_ar', '')} -> pending_gm",
                                by_name=current_user.get("name_ar", ""),
                                by_user_id=user_id,
                            )
                            st.rerun()
                    elif st_ot == "pending_gm" and user_role == "general_manager":
                        if st.button("✅ اعتماد مدير عام", key=f"unif_ot_gm_{rec['id']}", type="primary"):
                            update_record("attendance_logs", rec["id"], {
                                "overtime_status": "approved",
                                "overtime_gm_by": user_id,
                            })
                            write_audit_log(
                                "attendance_logs",
                                "ot_gm_approved",
                                "اعتماد",
                                f"اعتماد المدير العام النهائي لوقت إضافي ({rec.get('overtime_minutes')} د) للموظف {emp_o.get('name_ar', '')}",
                                by_name=current_user.get("name_ar", ""),
                                by_user_id=user_id,
                            )
                            st.rerun()
                with cr:
                    if st.button("❌ رفض الإضافي", key=f"unif_ot_rej_{rec['id']}"):
                        update_record("attendance_logs", rec["id"], {"overtime_status": "rejected"})
                        write_audit_log(
                            "attendance_logs",
                            "ot_rejected",
                            "رفض",
                            f"رفض وقت إضافي ({rec.get('overtime_minutes')} د) للموظف {emp_o.get('name_ar', '')}",
                            by_name=current_user.get("name_ar", ""),
                            by_user_id=user_id,
                        )
                        st.rerun()


# =====================================================================
# 3. منظومة الإجازات والأرصدة
# =====================================================================
def render_leaves_module(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    departments: List[Dict[str, Any]],
    leave_types: List[Dict[str, Any]],
    leave_balances: List[Dict[str, Any]],
    leave_requests: List[Dict[str, Any]],
    org_settings: Dict[str, Any],
) -> None:
    st.markdown("### 🗓️ نظام الإجازات والأرصدة")
    perms = get_effective_permissions(current_user)
    dept_names = _dept_map(departments)
    user_id = str(current_user.get("id"))
    user_team_id = str(current_user.get("team_user_id") or "")
    user_role = (current_user.get("role") or "employee").lower()

    bal_by_emp: Dict[str, Dict[str, Any]] = {}
    for b in leave_balances:
        if b.get("employee_id"):
            bal_by_emp[str(b["employee_id"])] = b
        if b.get("team_user_id"):
            bal_by_emp[str(b["team_user_id"])] = b

    my_bal = bal_by_emp.get(user_id) or bal_by_emp.get(user_team_id) or {}
    ann_total = float(my_bal.get("annual") or 0)
    ann_used = float(my_bal.get("used_annual") or 0)
    annual_rem = ann_total - ann_used

    cas_total = float(my_bal.get("casual") or 0)
    cas_used = float(my_bal.get("used_casual") or 0)
    casual_rem = cas_total - cas_used

    can_manage_all_balances = user_role in ("general_manager", "chairman", "vice_chairman", "hr")
    tab_titles = ["➕ تقديم طلب إجازة", "📋 سجل الإجازات", "⚖️ الرصيد"]
    if can_manage_all_balances:
        tab_titles.append("⚖️ أرصدة جميع الموظفين والتجديد")

    tabs = st.tabs(tab_titles)

    with tabs[0]:
        active_types = [lt for lt in leave_types if lt.get("active", True)]
        if not active_types:
            st.warning("لا توجد أنواع إجازات مفعّلة حالياً.")
        else:
            type_labels = {f"{lt.get('name_ar')} ({lt.get('code')})": lt for lt in active_types}
            selected_label = st.selectbox("نوع الإجازة", list(type_labels.keys()), key="leave_type_select")
            chosen_type = type_labels[selected_label]

            st.caption(
                f"القواعد: تنبيه مسبق {chosen_type.get('notice_days', 0)} يوم / {chosen_type.get('notice_hours', 0)} ساعة | "
                f"حد أقصى للطلب: {chosen_type.get('max_days_per_request') or 'مفتوح'} | "
                f"حد أقصى شهري: {chosen_type.get('max_days_per_month') or 'مفتوح'} | "
                f"تفعيل الرصيد بعد: {chosen_type.get('min_tenure_days', 30)} يوم من التعيين"
            )

            if "lr_start_date_input" not in st.session_state:
                st.session_state["lr_start_date_input"] = date.today()
            if "lr_end_date_input" not in st.session_state:
                st.session_state["lr_end_date_input"] = date.today()
            if "calculated_leave_days" not in st.session_state:
                _sync_leave_days_state()

            d_col1, d_col2, d_col3 = st.columns(3)
            with d_col1:
                start_dt = st.date_input(
                    "تاريخ البداية",
                    key="lr_start_date_input",
                    on_change=_sync_leave_days_state,
                )
            with d_col2:
                end_dt = st.date_input(
                    "تاريخ النهاية",
                    key="lr_end_date_input",
                    on_change=_sync_leave_days_state,
                )

            _sync_leave_days_state()
            days_int = int(st.session_state.get("calculated_leave_days", 0))

            with d_col3:
                st.text_input(
                    "عدد أيام الإجازة",
                    value=str(days_int),
                    disabled=True,
                    key=f"lr_days_box_{days_int}",
                )

            backup_choices: Dict[str, Any] = {"بدون بديل (اختياري)": None}
            for e in employees:
                if str(e.get("id")) == user_id:
                    continue
                if (e.get("status") or "active") != "active":
                    continue
                b_lbl = f"{e.get('name_ar')} (#{e.get('employee_no')})"
                backup_choices[b_lbl] = e

            with st.form("submit_leave_form", clear_on_submit=False):
                backup_lbl = st.selectbox("البديل", list(backup_choices.keys()), key="lr_backup_emp_box")
                reason_val = st.text_area("السبب / ملاحظات", placeholder="اكتب السبب (مطلوب للإجازة المرضية أو التوضيح)")
                uploaded_file = st.file_uploader(
                    "إرفاق تقرير / مستند (مطلوب للتقرير المرضي)",
                    type=["pdf", "jpg", "jpeg", "png"],
                )

                submitted = st.form_submit_button("إرسال طلب الإجازة", type="primary")
                if submitted:
                    _sync_leave_days_state()
                    final_days = float(st.session_state.get("calculated_leave_days", 0))
                    ok, msg = validate_leave_request(
                        employee=current_user,
                        leave_type=chosen_type,
                        start_date=start_dt,
                        end_date=end_dt,
                        days_requested=final_days,
                        existing_requests=leave_requests,
                        leave_balance=my_bal,
                        has_attachment=uploaded_file is not None,
                        reason_text=reason_val,
                    )
                    if not ok:
                        st.error(f"❌ تعذر قبول الطلب: {msg}")
                    else:
                        att_url = ""
                        if uploaded_file is not None:
                            att_url = (
                                upload_attachment(
                                    uploaded_file.getvalue(),
                                    uploaded_file.name,
                                    uploaded_file.type or "application/octet-stream",
                                )
                                or ""
                            )

                        chosen_backup = backup_choices.get(backup_lbl)
                        route = determine_initial_approval_route(current_user, employees, backup_employee=chosen_backup)
                        backup_target_id = str(chosen_backup.get("id")) if chosen_backup else None
                        payload = {
                            "employee_id": current_user.get("id"),
                            "employee_user_id": current_user.get("team_user_id") or current_user.get("id"),
                            "employee_name": current_user.get("name_ar"),
                            "department_id": current_user.get("department_id"),
                            "shift_id": current_user.get("shift_id"),
                            "backup_employee_id": backup_target_id,
                            "backup_employee_name": chosen_backup.get("name_ar") if chosen_backup else "",
                            "type_code": chosen_type.get("code"),
                            "type_name": chosen_type.get("name_ar"),
                            "start_date": start_dt.isoformat(),
                            "end_date": end_dt.isoformat(),
                            "days": final_days,
                            "reason": reason_val.strip(),
                            "attachment_url": att_url,
                            "deducts": bool(chosen_type.get("deducts")),
                            "balance_field": chosen_type.get("balance_field"),
                            "status": route["status"],
                            "senior_id": route["senior_id"],
                            "direct_manager_id": route["direct_manager_id"],
                            "approvals": [],
                            "created_by": current_user.get("id"),
                        }
                        insert_record("leave_requests", payload)
                        write_audit_log(
                            "leave_requests",
                            "create_leave_request",
                            "إضافة",
                            f"تقديم طلب {chosen_type.get('name_ar')} ({int(final_days)} يوم) للموظف {current_user.get('name_ar')}",
                            by_name=current_user.get("name_ar", ""),
                            by_user_id=user_id,
                        )
                        st.success("✅ تم إرسال طلب الإجازة بنجاح وإدراجه في مسار الموافقات.")
                        st.rerun()

    with tabs[1]:
        visible_requests = _filter_visible_leave_requests(current_user, employees, leave_requests)
        if visible_requests:
            enriched_rows = []
            for lr in visible_requests:
                branch_label = dept_names.get(str(lr.get("department_id") or ""), "بدون فرع")
                emp_label = lr.get("employee_name") or "غير محدد"
                type_label = lr.get("type_name") or lr.get("type_code") or "غير محدد"
                status_label = lr.get("status") or "—"
                enriched_rows.append({
                    "الموظف": emp_label,
                    "الفرع": branch_label,
                    "البديل": lr.get("backup_employee_name") or "—",
                    "نوع الإجازة": type_label,
                    "من": str(lr.get("start_date") or "")[:10],
                    "إلى": str(lr.get("end_date") or "")[:10],
                    "الأيام": lr.get("days"),
                    "الحالة": status_label,
                    "السبب": lr.get("reason") or "—",
                })

            f1, f2, f3, f4 = st.columns(4)
            emp_filter_opts = ["الكل"] + sorted({r["الموظف"] for r in enriched_rows})
            branch_filter_opts = ["الكل"] + sorted({r["الفرع"] for r in enriched_rows})
            type_filter_opts = ["الكل"] + sorted({r["نوع الإجازة"] for r in enriched_rows})
            status_filter_opts = ["الكل"] + sorted({r["الحالة"] for r in enriched_rows})

            sel_emp_f = f1.selectbox("فلتر باسم الموظف", emp_filter_opts, key="lf_emp")
            sel_branch_f = f2.selectbox("فلتر باسم الفرع", branch_filter_opts, key="lf_branch")
            sel_type_f = f3.selectbox("فلتر بنوع الإجازة", type_filter_opts, key="lf_type")
            sel_status_f = f4.selectbox("فلتر بالحالة", status_filter_opts, key="lf_status")

            filtered_table = [
                r for r in enriched_rows
                if (sel_emp_f == "الكل" or r["الموظف"] == sel_emp_f)
                and (sel_branch_f == "الكل" or r["الفرع"] == sel_branch_f)
                and (sel_type_f == "الكل" or r["نوع الإجازة"] == sel_type_f)
                and (sel_status_f == "الكل" or r["الحالة"] == sel_status_f)
            ]

            st.caption(f"عدد السجلات المعروضة: {len(filtered_table)} من {len(enriched_rows)}")
            if filtered_table:
                st.dataframe(filtered_table, use_container_width=True, hide_index=True)
            else:
                st.info("لا توجد نتائج مطابقة للفلاتر المختارة.")
        else:
            st.info("لا توجد طلبات إجازات ضمن نطاق صلاحياتك حالياً.")

    with tabs[2]:
        st.markdown("#### رصيد إجازاتي الحالي")
        st.caption(f"تاريخ التعيين: `{str(current_user.get('hire_date') or 'غير محدد')[:10]}`")
        r1, r2, r3 = st.columns(3)
        r1.metric("الاعتيادي / السنوي المستحق", int(round(ann_total)))
        r2.metric("المستخدم من الاعتيادي / السنوي", int(round(ann_used)))
        r3.metric("المتبقي من الاعتيادي / السنوي", int(round(annual_rem)))

        c_r1, c_r2, c_r3 = st.columns(3)
        c_r1.metric("العارضة المستحق", int(round(cas_total)))
        c_r2.metric("المستخدم من العارضة", int(round(cas_used)))
        c_r3.metric("المتبقي من العارضة", int(round(casual_rem)))

    if can_manage_all_balances and len(tabs) > 3:
        with tabs[3]:
            st.markdown("#### أرصدة جميع الموظفين، الاحتساب النسبي، وتجديد بداية السنة")
            admin_yr = str(org_settings.get("admin_year") or date.today().year)
            def_ann = float(org_settings.get("default_annual") or 15)
            def_cas = float(org_settings.get("default_casual") or 6)

            c_r1, c_r2 = st.columns([2, 2])
            with c_r1:
                is_reset_mode = st.checkbox("تفعيل وضع (تصفير/تجديد بداية السنة للجميع بالتساوي)", value=False)
            with c_r2:
                if perms.get("manage_settings") or perms.get("manage_payroll") or user_role in ("general_manager", "chairman", "vice_chairman", "hr"):
                    if st.button("⚡ تحديث/توليد أرصدة جميع الموظفين", type="primary"):
                        for emp in employees:
                            if (emp.get("status") or "active") != "active":
                                continue
                            ann_ent, cas_ent, _ = calculate_prorated_leave_entitlement(
                                emp.get("hire_date"),
                                admin_year=admin_yr,
                                annual_base=def_ann,
                                casual_base=def_cas,
                                is_annual_reset=is_reset_mode,
                            )
                            existing_b = bal_by_emp.get(str(emp["id"])) or bal_by_emp.get(str(emp.get("team_user_id") or ""))
                            if existing_b and existing_b.get("id"):
                                payload_b = {
                                    "id": existing_b["id"],
                                    "employee_id": emp["id"],
                                    "team_user_id": emp.get("team_user_id"),
                                    "admin_year": admin_yr,
                                    "annual": ann_ent,
                                    "casual": cas_ent,
                                    "used_annual": 0 if is_reset_mode else float(existing_b.get("used_annual") or 0),
                                    "used_casual": 0 if is_reset_mode else float(existing_b.get("used_casual") or 0),
                                }
                                upsert_record("leave_balances", payload_b, on_conflict="id")
                            else:
                                insert_record("leave_balances", {
                                    "employee_id": emp["id"],
                                    "team_user_id": emp.get("team_user_id"),
                                    "admin_year": admin_yr,
                                    "annual": ann_ent,
                                    "casual": cas_ent,
                                    "used_annual": 0,
                                    "used_casual": 0,
                                })
                        write_audit_log(
                            "leave_balances",
                            "sync_leave_balances",
                            "تحديث",
                            f"تحديث أرصدة الإجازات للسنة {admin_yr} (وضع التجديد السنوي: {is_reset_mode})",
                            by_name=current_user.get("name_ar", ""),
                            by_user_id=user_id,
                        )
                        st.success("✅ تم تحديث أرصدة الموظفين بنجاح.")
                        st.rerun()

            bal_rows = []
            for emp in employees:
                b = bal_by_emp.get(str(emp.get("id"))) or bal_by_emp.get(str(emp.get("team_user_id") or "")) or {}
                ann_t = int(round(float(b.get("annual") or 0)))
                ann_u = int(round(float(b.get("used_annual") or 0)))
                cas_t = int(round(float(b.get("casual") or 0)))
                cas_u = int(round(float(b.get("used_casual") or 0)))
                bal_rows.append({
                    "الرقم الوظيفي": emp.get("employee_no"),
                    "الاسم": emp.get("name_ar"),
                    "تاريخ التعيين": str(emp.get("hire_date") or "")[:10],
                    "اعتيادي/سنوي مستحق": ann_t,
                    "مستخدم": ann_u,
                    "متبقي": ann_t - ann_u,
                    "عارضة مستحق": cas_t,
                    "مستخدم عارضة": cas_u,
                    "متبقي عارضة": cas_t - cas_u,
                })
            st.dataframe(bal_rows, use_container_width=True, hide_index=True)


# =====================================================================
# 4. الجزاءات والمكافآت والسلف
# =====================================================================
def render_penalties_rewards_advances(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
) -> None:
    st.markdown("### 💰 الجزاءات والمكافآت والسلف")
    perms = get_effective_permissions(current_user)
    user_id = str(current_user.get("id"))
    user_role = (current_user.get("role") or "employee").strip().lower()
    emp_by_id = {str(e["id"]): e for e in employees if e.get("id")}

    can_create_pr = (
        user_role in ("general_manager", "manager", "senior")
        or perms.get("approve_penalties")
        or perms.get("approve_rewards")
    ) and not perms.get("is_monitor_only")

    can_view_hr_settle = user_role in ("hr", "general_manager")

    tab_titles: List[str] = []
    if can_create_pr:
        tab_titles.append("➕ إنشاء الجزاءات والمكافآت")
    tab_titles.append("📊 تتبع الجزاءات والمكافآت")
    tab_titles.append("💳 طلب سلفة")
    tab_titles.append("📌 تتبع السلف")
    if can_view_hr_settle:
        tab_titles.append("🏦 تجميع وسداد السلف (HR)")

    tabs = st.tabs(tab_titles)
    t_idx = 0

    if can_create_pr:
        with tabs[t_idx]:
            subordinate_emps = _get_subordinate_employees(current_user, employees)
            emp_options = {
                f"{e.get('name_ar')} (#{e.get('employee_no')})": e
                for e in subordinate_emps
            }
            st.markdown("#### إنشاء جزاء أو مكافأة جديدة")
            if not emp_options:
                st.info("لا يوجد موظفون تابعون تحت سلطتك لإضافة جزاء أو مكافأة لهم.")
            else:
                with st.form("add_pr_form", clear_on_submit=True):
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        sel_emp_lbl = st.selectbox("الموظف (التابع لسلطتك فقط)", list(emp_options.keys()))
                    with c2:
                        entry_type = st.selectbox(
                            "النوع",
                            ["penalty", "reward"],
                            format_func=lambda x: "جزاء" if x == "penalty" else "مكافأة",
                        )
                    with c3:
                        calc_mode = st.selectbox(
                            "طريقة الحساب",
                            ["amount", "days"],
                            format_func=lambda x: "مبلغ مالي (جنيه)" if x == "amount" else "بعدد الأيام",
                        )

                    c4, c5, c6 = st.columns(3)
                    with c4:
                        val_inp = st.number_input("القيمة (جنيه أو عدد أيام)", min_value=0.5, value=100.0, step=10.0)
                    with c5:
                        issue_dt = st.date_input("تاريخ الصدور", value=date.today())
                    with c6:
                        pay_month = st.text_input("شهر الخصم/الإضافة (YYYY-MM)", value=date.today().strftime("%Y-%m"))

                    title_inp = st.text_input("عنوان مختصر", placeholder="مثال: تأخير متكرر أو تميز في الأداء")
                    reason_inp = st.text_area("السبب", placeholder="اكتب السبب باختصار", height=80)

                    if st.form_submit_button("حفظ وإرسال للموافقات", type="primary"):
                        target_emp = emp_options[sel_emp_lbl]
                        calc_amt = calculate_penalty_or_reward_amount(
                            float(target_emp.get("base_salary") or 0),
                            calc_mode,
                            float(val_inp),
                        )
                        insert_record("penalties_rewards", {
                            "employee_id": target_emp["id"],
                            "entry_type": entry_type,
                            "calc_mode": calc_mode,
                            "issue_date": issue_dt.isoformat(),
                            "payroll_month": pay_month.strip()[:7],
                            "value": float(val_inp),
                            "calculated_amount": calc_amt,
                            "title": title_inp.strip(),
                            "reason": reason_inp.strip(),
                            "status": "pending",
                            "created_by": user_id,
                        })
                        write_audit_log(
                            "penalties_rewards",
                            f"create_{entry_type}",
                            "إضافة",
                            f"إضافة {'جزاء' if entry_type == 'penalty' else 'مكافأة'} ({calc_amt} ج) للموظف {target_emp.get('name_ar')}",
                            by_name=current_user.get("name_ar", ""),
                            by_user_id=user_id,
                        )
                        st.success("✅ تم تسجيل الطلب وإرساله إلى صندوق الموافقات الموحّد.")
                        st.rerun()
        t_idx += 1

    with tabs[t_idx]:
        st.markdown("#### سجل تتبع الجزاءات والمكافآت")
        visible_pr = _filter_visible_penalties_rewards(current_user, employees, penalties_rewards)
        if visible_pr:
            pr_rows = []
            for pr in visible_pr:
                emp_o = emp_by_id.get(str(pr.get("employee_id") or ""), {})
                kind_ar = "جزاء" if (pr.get("entry_type") or "").lower() == "penalty" else "مكافأة"
                month_val = str(pr.get("payroll_month") or pr.get("issue_date") or "")[:7]
                pr_rows.append({
                    "الموظف": emp_o.get("name_ar") or "غير محدد",
                    "النوع": kind_ar,
                    "الشهر": month_val or "—",
                    "القيمة": pr.get("value"),
                    "طريقة الحساب": "أيام" if pr.get("calc_mode") == "days" else "مبلغ مالي",
                    "الإجمالي (ج)": pr.get("calculated_amount"),
                    "العنوان": pr.get("title") or "—",
                    "السبب": pr.get("reason") or "—",
                    "الحالة": pr.get("status"),
                })

            f1, f2, f3 = st.columns(3)
            emp_opts = ["الكل"] + sorted({r["الموظف"] for r in pr_rows})
            type_opts = ["الكل", "جزاء", "مكافأة"]
            month_opts = ["الكل"] + sorted({r["الشهر"] for r in pr_rows if r["الشهر"] != "—"}, reverse=True)

            sel_emp_pr = f1.selectbox("فلتر باسم الموظف", emp_opts, key="pr_f_emp")
            sel_type_pr = f2.selectbox("فلتر بالنوع (مكافأة / جزاء)", type_opts, key="pr_f_type")
            sel_month_pr = f3.selectbox("فلتر بالشهر", month_opts, key="pr_f_month")

            filtered_pr = [
                r for r in pr_rows
                if (sel_emp_pr == "الكل" or r["الموظف"] == sel_emp_pr)
                and (sel_type_pr == "الكل" or r["النوع"] == sel_type_pr)
                and (sel_month_pr == "الكل" or r["الشهر"] == sel_month_pr)
            ]

            st.caption(f"عدد السجلات المعروضة: {len(filtered_pr)} من {len(pr_rows)}")
            if filtered_pr:
                st.dataframe(filtered_pr, use_container_width=True, hide_index=True)
            else:
                st.info("لا توجد سجلات مطابقة للفلاتر المختارة.")
        else:
            st.info("لا توجد سجلات جزاءات أو مكافآت ضمن نطاق صلاحياتك حالياً.")
    t_idx += 1

    with tabs[t_idx]:
        st.markdown("#### تقديم طلب سلفة")
        eligible_targets = _get_advance_eligible_targets(current_user, employees)
        target_map: Dict[str, Dict[str, Any]] = {}
        for te in eligible_targets:
            t_lbl = (
                f"نفسي — {te.get('name_ar')} (#{te.get('employee_no')})"
                if str(te.get("id")) == user_id
                else f"{te.get('name_ar')} (#{te.get('employee_no')})"
            )
            target_map[t_lbl] = te

        chosen_adv_target_lbl = st.selectbox(
            "مقدم السلفة / المستفيد من السلفة",
            list(target_map.keys()),
            key="adv_target_emp_sel",
        )
        chosen_adv_emp = target_map[chosen_adv_target_lbl]

        rep_mode = st.selectbox(
            "نظام السداد",
            ["single_month", "installments_count"],
            format_func=lambda x: {
                "single_month": "خصم كامل في نفس الشهر",
                "installments_count": "تقسيط على عدد الشهور",
            }[x],
            key="adv_repayment_mode_sel",
        )

        if rep_mode == "installments_count":
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                tot_amt = st.number_input("إجمالي السلفة (جنيه)", min_value=50.0, value=1000.0, step=50.0, key="adv_tot_amt_inp")
            with c2:
                start_ym = st.text_input("شهر بداية الخصم (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="adv_start_ym_inp")
            with c3:
                inst_cnt = st.number_input("عدد الشهور", min_value=2, max_value=36, value=2, step=1, key="adv_inst_cnt_inp")
            sched = calculate_advance_schedule(tot_amt, "installments_count", int(inst_cnt), 0.0)
            with c4:
                st.text_input(
                    "قيمة القسط الشهري",
                    value=f"{sched['monthly_installment']:g} ج",
                    disabled=True,
                    key=f"adv_monthly_preview_{sched['monthly_installment']}",
                )
        else:
            c1, c2 = st.columns(2)
            with c1:
                tot_amt = st.number_input("إجمالي السلفة (جنيه)", min_value=50.0, value=1000.0, step=50.0, key="adv_tot_amt_single")
            with c2:
                start_ym = st.text_input("شهر بداية الخصم (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="adv_start_ym_single")
            sched = calculate_advance_schedule(tot_amt, "single_month", 1, 0.0)

        preview_rows = _build_advance_installments_preview(
            total_amount=float(tot_amt),
            start_ym=start_ym,
            installments_count=int(sched["installments_count"]),
            monthly_installment=float(sched["monthly_installment"]),
            last_installment_amount=float(sched["last_installment_amount"]),
        )
        if rep_mode == "installments_count":
            st.caption("جدول تقسيم الأقساط بالترتيب على الشهور:")
            st.dataframe(preview_rows, use_container_width=True, hide_index=True)

        with st.form("add_advance_form", clear_on_submit=True):
            adv_reason = st.text_input("سبب السلفة (اختياري)")

            if st.form_submit_button("إرسال طلب السلفة", type="primary"):
                insert_record("advances", {
                    "employee_id": chosen_adv_emp["id"],
                    "request_date": date.today().isoformat(),
                    "total_amount": float(tot_amt),
                    "repayment_mode": rep_mode,
                    "installments_count": int(sched["installments_count"]),
                    "monthly_installment": float(sched["monthly_installment"]),
                    "last_installment_amount": float(sched["last_installment_amount"]),
                    "remaining_amount": float(tot_amt),
                    "start_payroll_month": start_ym.strip()[:7],
                    "reason": adv_reason.strip(),
                    "status": "pending",
                    "created_by": user_id,
                })
                write_audit_log(
                    "advances",
                    "create_advance",
                    "إضافة",
                    f"طلب سلفة بقيمة {tot_amt} ج ({int(sched['installments_count'])} شهر) للموظف {chosen_adv_emp.get('name_ar')}",
                    by_name=current_user.get("name_ar", ""),
                    by_user_id=user_id,
                )
                st.success("✅ تم إرسال طلب السلفة إلى صندوق الموافقات الموحّد.")
                st.rerun()
    t_idx += 1

    with tabs[t_idx]:
        st.markdown("#### تتبع السلف والأقساط المتبقية")
        track_ym = st.text_input("الشهر المستهدف (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="adv_track_ym")

        visible_advances = _filter_visible_advances(current_user, employees, advances)
        adv_emp_ids_avail = sorted({str(a.get("employee_id") or "") for a in visible_advances if a.get("employee_id")})
        adv_emp_labels_map: Dict[str, str] = {"سلفاتي الشخصية": user_id}
        for eid_a in adv_emp_ids_avail:
            if eid_a == user_id:
                continue
            eo = emp_by_id.get(eid_a)
            if eo:
                adv_emp_labels_map[f"{eo.get('name_ar')} (#{eo.get('employee_no')})"] = eid_a

        if user_role in ("manager", "general_manager", "hr", "chairman", "vice_chairman") and len(adv_emp_labels_map) > 1:
            sel_track_emp_lbl = st.selectbox(
                "اختر الموظف لمتابعة تفاصيل السلف والأقساط المتبقية",
                list(adv_emp_labels_map.keys()),
                key="adv_track_emp_sel",
            )
            inspected_emp_id = adv_emp_labels_map[sel_track_emp_lbl]
        else:
            inspected_emp_id = user_id

        inspected_emp_name = emp_by_id.get(inspected_emp_id, {}).get("name_ar") or current_user.get("name_ar") or ""
        month_summ = get_employee_monthly_advances_summary(inspected_emp_id, track_ym, advances)
        st.metric(f"مستحق السلف لشهر {track_ym} ({inspected_emp_name})", f"{round_half_up_2(month_summ['total_due']):,.2f} ج")

        if month_summ["items"]:
            st.dataframe(
                [
                    {
                        "رقم القسط": f"{sub['installment_no']}/{sub['installments_count']}",
                        "قيمة المستحق في الشهر (ج)": sub["due_amount"],
                    }
                    for sub in month_summ["items"]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(f"لا توجد سلف مستحقة في شهر {track_ym} للمحدد.")

        rem_info = _build_remaining_schedule_for_employee(inspected_emp_id, track_ym, advances)
        st.markdown("#### السلف المتبقية على مدار الشهور")
        rm1, rm2 = st.columns(2)
        rm1.metric("إجمالي السلف المتبقية", f"{rem_info['total_remaining']:,.2f} ج")
        rm2.metric("تاريخ الانتهاء المتوقع من السلف", rem_info["end_month"])

        if rem_info["schedule_rows"]:
            st.dataframe(rem_info["schedule_rows"], use_container_width=True, hide_index=True)
        else:
            st.success("لا توجد أقساط سلف متبقية.")

        st.divider()
        st.markdown("#### سجل السلف وحالاتها")
        if visible_advances:
            st.dataframe(
                [
                    {
                        "الموظف": emp_by_id.get(str(adv.get("employee_id") or ""), {}).get("name_ar", ""),
                        "الإجمالي (ج)": adv.get("total_amount"),
                        "المتبقي (ج)": adv.get("remaining_amount"),
                        "نظام السداد": "خصم كامل في نفس الشهر" if adv.get("repayment_mode") == "single_month" else "تقسيط على عدد الشهور",
                        "عدد الشهور": adv.get("installments_count"),
                        "القسط الشهري": adv.get("monthly_installment"),
                        "بداية الخصم": adv.get("start_payroll_month"),
                        "الحالة": adv.get("status"),
                    }
                    for adv in visible_advances
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("لا توجد طلبات سلف مسجلة ضمن نطاق صلاحياتك حالياً.")
    t_idx += 1

    if can_view_hr_settle and t_idx < len(tabs):
        with tabs[t_idx]:
            st.markdown("#### تجميع سلف الموظف المستحقة للشهر وتأكيد السداد")
            fc1, fc2 = st.columns(2)
            with fc1:
                settle_month = st.text_input("الشهر المستهدف (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="hr_adv_ym")
            with fc2:
                hr_emp_filter_opts = ["الكل"] + [
                    f"{e.get('name_ar')} (#{e.get('employee_no')})"
                    for e in employees
                    if (e.get("status") or "active") == "active"
                ]
                sel_hr_emp = st.selectbox("اسم الموظف", hr_emp_filter_opts, key="hr_adv_emp_filter")

            can_settle = perms.get("manage_payroll") or user_role in ("hr", "general_manager")

            rows_summary = []
            for emp in employees:
                emp_lbl = f"{emp.get('name_ar')} (#{emp.get('employee_no')})"
                if sel_hr_emp != "الكل" and emp_lbl != sel_hr_emp:
                    continue
                summ = get_employee_monthly_advances_summary(str(emp["id"]), settle_month, advances)
                if summ["total_due"] > 0:
                    rows_summary.append({"emp": emp, "summ": summ})

            if not rows_summary:
                st.info(f"لا توجد سلف مستحقة مجمّعة لشهر {settle_month} حسب الفلتر المختار.")
            else:
                for item in rows_summary:
                    emp = item["emp"]
                    summ = item["summ"]
                    with st.container(border=True):
                        st.write(
                            f"**{emp.get('name_ar')} (#{emp.get('employee_no')})** — "
                            f"إجمالي المستحق للشهر: **`{summ['total_due']} ج`** "
                            f"(عدد البنود المجمّعة: {len(summ['items'])})"
                        )
                        for sub in summ["items"]:
                            st.caption(f"• سلفة `{sub['advance_id'][:8]}` — قسط {sub['installment_no']}/{sub['installments_count']}: {sub['due_amount']} ج")

                        if can_settle and st.button("✔️ تم التسديد في السلف", key=f"settle_btn_{emp['id']}_{settle_month}", type="primary"):
                            for sub in summ["items"]:
                                adv_rec = next((a for a in advances if str(a["id"]) == str(sub["advance_id"])), None)
                                if adv_rec:
                                    new_rem = round_half_up_2(max(0.0, float(adv_rec.get("remaining_amount") or 0) - float(sub["due_amount"])))
                                    new_status = "settled" if new_rem <= 0 else adv_rec.get("status")
                                    update_record("advances", adv_rec["id"], {
                                        "remaining_amount": new_rem,
                                        "status": new_status,
                                        "settled_at": datetime.utcnow().isoformat() + "Z" if new_rem <= 0 else adv_rec.get("settled_at"),
                                        "settled_by": user_id if new_rem <= 0 else adv_rec.get("settled_by"),
                                    })
                            write_audit_log(
                                "advances",
                                "settle_monthly_advances",
                                "سداد",
                                f"تأكيد سداد سلف شهر {settle_month} بقيمة {summ['total_due']} ج للموظف {emp.get('name_ar')}",
                                by_name=current_user.get("name_ar", ""),
                                by_user_id=user_id,
                            )
                            st.success(f"✅ تم تسجيل سداد سلف {emp.get('name_ar')} للشهر.")
                            st.rerun()


# =====================================================================
# 5. البصمة والـ Payroll
# =====================================================================
def render_attendance_and_payroll(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    shifts: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    official_holidays: List[Dict[str, Any]],
    payroll_runs: List[Dict[str, Any]],
    org_settings: Dict[str, Any],
    leave_requests: Optional[List[Dict[str, Any]]] = None,
) -> None:
    st.markdown("### ⏱️ البصمة والـ Payroll")
    perms = get_effective_permissions(current_user)
    user_id = str(current_user.get("id"))
    my_ids = _emp_identifiers(current_user)
    user_role = (current_user.get("role") or "employee").strip().lower()
    all_leaves = leave_requests or []
    non_pay_holiday_map = _get_non_payroll_holiday_map(official_holidays)

    emp_by_no = {str(e.get("employee_no") or "").strip(): e for e in employees if e.get("employee_no")}
    emp_by_id = {str(e["id"]): e for e in employees if e.get("id")}
    grace_late = int(org_settings.get("grace_late_minutes") if org_settings.get("grace_late_minutes") is not None else 15)
    grace_early = int(org_settings.get("grace_early_minutes") if org_settings.get("grace_early_minutes") is not None else 5)

    can_upload_att = user_role in ("general_manager", "hr") or perms.get("manage_payroll")
    can_view_ot_log = user_role in ("general_manager", "hr") or perms.get("manage_payroll")
    can_view_all_payroll_sheet = user_role in ("general_manager", "hr", "chairman")

    att_by_emp_date: Dict[tuple, Dict[str, Any]] = {}
    for rec in attendance_logs:
        eid_k = str(rec.get("employee_id") or "")
        dt_k = str(rec.get("work_date") or "")[:10]
        if eid_k and dt_k:
            att_by_emp_date[(eid_k, dt_k)] = rec

    tab_titles: List[str] = ["📋 شيت البصمة"]
    if can_upload_att:
        tab_titles.append("📥 رفع البصمة")
    if can_view_ot_log:
        tab_titles.append("🕒 سجل الوقت الإضافي")
    tab_titles.append("💵 شيت المرتب")
    if can_view_all_payroll_sheet:
        tab_titles.append("📊 شيت المرتبات")

    tabs = st.tabs(tab_titles)
    t_idx = 0

    with tabs[t_idx]:
        st.markdown("#### شيت البصمة (الفترة من يوم 21 الشهر السابق إلى يوم 20 الشهر المختار)")
        sheet_ym = st.text_input("اختر الشهر (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="att_sheet_ym")
        cycle_days = _get_cycle_dates_for_month(sheet_ym)
        st.caption(
            f"الفترة المعروضة: من `{cycle_days[0].isoformat()}` إلى `{cycle_days[-1].isoformat()}` — "
            f"سماح الحضور: {grace_late} دقيقة | سماح الانصراف: {grace_early} دقيقة — عرض فقط بدون تعديل."
        )

        my_leave_map = _get_leave_map_for_employee(current_user, all_leaves)
        sheet_rows = []
        for d_item in cycle_days:
            d_iso = d_item.isoformat()
            is_fri = d_item.weekday() == 4
            non_pay_hol_txt = non_pay_holiday_map.get(d_iso, "")
            leave_txt = my_leave_map.get(d_iso, "")

            rec: Dict[str, Any] = {}
            for candidate_id in my_ids:
                if (candidate_id, d_iso) in att_by_emp_date:
                    rec = att_by_emp_date[(candidate_id, d_iso)]
                    break

            has_att = bool(rec.get("check_in") or rec.get("check_out"))
            bd = _compute_attendance_breakdown(rec, current_user, grace_late, grace_early) if has_att else {
                "late_in": 0,
                "early_in": 0,
                "early_out": 0,
                "overtime_out": 0,
            }

            if is_fri:
                day_state = "جمعة"
            elif non_pay_hol_txt:
                day_state = non_pay_hol_txt
            elif leave_txt:
                day_state = leave_txt
            elif has_att:
                day_state = "حضور / دوام"
            else:
                day_state = "—"

            blocked_day = is_fri or bool(non_pay_hol_txt) or bool(leave_txt)
            cin_disp = str(rec.get("check_in") or "")[:5] if rec.get("check_in") else ("ممنوع البصمة" if blocked_day else "—")
            cout_disp = str(rec.get("check_out") or "")[:5] if rec.get("check_out") else ("ممنوع البصمة" if blocked_day else "—")

            sheet_rows.append({
                "التاريخ (work_date)": d_iso,
                "الحالة / الإجازة": day_state,
                "دخول (check_in)": cin_disp,
                "انصراف (check_out)": cout_disp,
                "تأخير دخول": bd["late_in"],
                "إضافي دخول": bd["early_in"],
                "انصراف مبكر": bd["early_out"],
                "إضافي الحضور والانصراف": bd["overtime_out"],
            })

        df_my_sheet = pd.DataFrame(sheet_rows)

        def _style_my_sheet(row: pd.Series) -> List[str]:
            st_txt = str(row.get("الحالة / الإجازة") or "")
            if st_txt == "جمعة" or st_txt.startswith("إجازة رسمية"):
                bg = "background-color: #fee2e2; color: #991b1b; font-weight: 600;"
            elif st_txt.startswith("إجازة"):
                bg = "background-color: #fef9c3; color: #854d0e; font-weight: 600;"
            else:
                bg = ""
            styles = []
            for col_name in row.index:
                if col_name in ("تأخير دخول", "إضافي دخول", "انصراف مبكر", "إضافي الحضور والانصراف"):
                    styles.append(bg + " text-align: center;")
                else:
                    styles.append(bg)
            return styles

        st.dataframe(df_my_sheet.style.apply(_style_my_sheet, axis=1), use_container_width=True, hide_index=True)
    t_idx += 1

    if can_upload_att:
        with tabs[t_idx]:
            st.markdown("#### رفع وإدخال البصمة (للمدير العام و HR فقط)")
            att_mode = st.radio(
                "طريقة إدخال البصمة",
                ["رفع ملف البصمة (Excel / CSV)", "إدخال يدوي (Manual)"],
                horizontal=True,
                key="att_input_mode_radio",
            )

            if att_mode == "رفع ملف البصمة (Excel / CSV)":
                st.caption(
                    f"الأعمدة المطلوبة: `employee_no`، `work_date` (مثال: `2026-11-05`)، "
                    f"`check_in` (مثال: `09:00`)، `check_out` (مثال: `17:00`) بنظام 24 ساعة — "
                    f"سماح الحضور: {grace_late} دقيقة | سماح الانصراف: {grace_early} دقيقة. "
                    f"(يتم إيقاف بصمة أيام الجمعة والإجازات تلقائياً)."
                )
                att_file = st.file_uploader("رفع ملف البصمة", type=["xlsx", "xls", "csv"], key="att_excel_uploader")
                if att_file is not None:
                    if att_file.name.lower().endswith(".csv"):
                        df_att = pd.read_csv(att_file)
                    else:
                        df_att = pd.read_excel(att_file)

                    st.dataframe(df_att.head(25), use_container_width=True, hide_index=True)

                    if st.button("معالجة وحفظ سجلات البصمة في قاعدة البيانات", type="primary", key="save_uploaded_att_btn"):
                        blocked_upload_dates: List[str] = []
                        for _, r in df_att.iterrows():
                            eno = str(r.get("employee_no") or "").strip()
                            emp = emp_by_no.get(eno)
                            w_dt = parse_date_safe(r.get("work_date"))
                            if emp and w_dt:
                                emp_lv_map = _get_leave_map_for_employee(emp, all_leaves)
                                if w_dt.weekday() == 4 or w_dt.isoformat() in emp_lv_map or w_dt.isoformat() in non_pay_holiday_map:
                                    blocked_upload_dates.append(f"{eno} ({w_dt.isoformat()})")

                        if blocked_upload_dates:
                            st.error(
                                f"🚫 ممنوع تسجيل بصمة في أيام الجمعة أو الإجازات؛ تم إيقاف الحفظ لوجود: {', '.join(blocked_upload_dates)}. يرجى حذف هذه الأيام من الملف ثم إعادة المحاولة."
                            )
                        else:
                            saved_cnt = 0
                            for _, r in df_att.iterrows():
                                eno = str(r.get("employee_no") or "").strip()
                                emp = emp_by_no.get(eno)
                                if not emp:
                                    continue
                                w_dt = parse_date_safe(r.get("work_date"))
                                if not w_dt:
                                    continue
                                s_start = str(emp.get("work_start_time") or "09:00:00")
                                s_end = str(emp.get("work_end_time") or "17:00:00")
                                cin_norm = _normalize_time_text(r.get("check_in"))
                                cout_norm = _normalize_time_text(r.get("check_out"))

                                eval_res = evaluate_attendance_record(
                                    cin_norm or "",
                                    cout_norm or "",
                                    s_start,
                                    s_end,
                                    grace_late_minutes=grace_late,
                                    grace_early_minutes=grace_early,
                                    is_leave_day=False,
                                    employee=emp,
                                )
                                upsert_record(
                                    "attendance_logs",
                                    {
                                        "employee_id": emp["id"],
                                        "work_date": w_dt.isoformat(),
                                        "check_in": cin_norm,
                                        "check_out": cout_norm,
                                        "scheduled_start": s_start,
                                        "scheduled_end": s_end,
                                        "late_minutes": eval_res["late_minutes"],
                                        "early_leave_minutes": eval_res["early_leave_minutes"],
                                        "overtime_minutes": eval_res["overtime_minutes"],
                                        "overtime_status": eval_res["overtime_status"],
                                    },
                                    on_conflict="employee_id,work_date",
                                )
                                saved_cnt += 1

                            write_audit_log(
                                "attendance_logs",
                                "upload_attendance",
                                "استيراد",
                                f"رفع ومعالجة {saved_cnt} سجل بصمة من الملف {att_file.name}",
                                by_name=current_user.get("name_ar", ""),
                                by_user_id=user_id,
                            )
                            st.success(f"✅ تم حفظ ومعالجة {saved_cnt} سجل بصمة بنجاح.")
                            st.rerun()

            else:
                mc1, mc2 = st.columns(2)
                with mc1:
                    man_ym = st.text_input("الشهر (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="man_att_ym")
                with mc2:
                    active_emp_list = [e for e in employees if (e.get("status") or "active") == "active"]
                    man_emp_map = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in active_emp_list}
                    sel_man_emp_lbl = st.selectbox("اختر الموظف", list(man_emp_map.keys()), key="man_att_emp_sel")

                man_emp = man_emp_map[sel_man_emp_lbl]
                man_emp_id = str(man_emp["id"])
                man_emp_all_ids = _emp_identifiers(man_emp)
                man_days = _get_cycle_dates_for_month(man_ym)
                man_leave_map = _get_leave_map_for_employee(man_emp, all_leaves)

                st.caption(
                    f"الموظف: **{man_emp.get('name_ar')}** | رقم: `{man_emp.get('employee_no')}` | "
                    f"مواعيد العمل: `{str(man_emp.get('work_start_time') or '09:00')[:5]}` إلى `{str(man_emp.get('work_end_time') or '17:00')[:5]}` | "
                    f"الفترة: `{man_days[0].isoformat()}` إلى `{man_days[-1].isoformat()}`"
                )
                st.info(
                    "📌 **صيغة إدخال الوقت (نظام 24 ساعة HH:MM):** مثال للدخول: **`09:00`** | مثال للانصراف: **`17:00`** — "
                    "🔴 أيام الجمعة والإجازات الرسمية غير المضافة مظللة بالأحمر وممنوع تسجيل بصمة فيها | "
                    "🟡 أيام الإجازات مظللة بالأصفر وممنوع تسجيل بصمة فيها."
                )

                grid_seed = []
                blocked_reason_by_iso: Dict[str, str] = {}
                for d_obj in man_days:
                    d_iso = d_obj.isoformat()
                    is_fri = d_obj.weekday() == 4
                    non_pay_hol_txt = non_pay_holiday_map.get(d_iso, "")
                    lv_label = man_leave_map.get(d_iso, "")

                    if is_fri:
                        blocked_reason_by_iso[d_iso] = "يوم جمعة"
                    elif non_pay_hol_txt:
                        blocked_reason_by_iso[d_iso] = non_pay_hol_txt
                    elif lv_label:
                        blocked_reason_by_iso[d_iso] = lv_label

                    existing_rec: Dict[str, Any] = {}
                    for cid in man_emp_all_ids:
                        if (cid, d_iso) in att_by_emp_date:
                            existing_rec = att_by_emp_date[(cid, d_iso)]
                            break

                    has_rec = bool(existing_rec.get("check_in") or existing_rec.get("check_out"))
                    bd = _compute_attendance_breakdown(existing_rec, man_emp, grace_late, grace_early) if has_rec else {
                        "late_in": 0,
                        "early_in": 0,
                        "early_out": 0,
                        "overtime_out": 0,
                    }

                    if is_fri:
                        status_col = "جمعة"
                    elif non_pay_hol_txt:
                        status_col = non_pay_hol_txt
                    elif lv_label:
                        status_col = lv_label
                    else:
                        status_col = "—"

                    grid_seed.append({
                        "التاريخ": d_iso,
                        "الحالة / الإجازة": status_col,
                        "دخول": "" if d_iso in blocked_reason_by_iso else str(existing_rec.get("check_in") or "")[:5],
                        "انصراف": "" if d_iso in blocked_reason_by_iso else str(existing_rec.get("check_out") or "")[:5],
                        "تأخير دخول": bd["late_in"],
                        "إضافي دخول": bd["early_in"],
                        "انصراف مبكر": bd["early_out"],
                        "إضافي الحضور والانصراف": bd["overtime_out"],
                    })

                df_man_seed = pd.DataFrame(grid_seed)

                def _style_man_grid(row: pd.Series) -> List[str]:
                    st_val = str(row.get("الحالة / الإجازة") or "")
                    if st_val == "جمعة" or st_val.startswith("إجازة رسمية"):
                        bg = "background-color: #fee2e2; color: #991b1b; font-weight: 600;"
                    elif st_val.startswith("إجازة"):
                        bg = "background-color: #fef9c3; color: #854d0e; font-weight: 600;"
                    else:
                        bg = ""
                    styles = []
                    for col_name in row.index:
                        if col_name in ("دخول", "انصراف", "تأخير دخول", "إضافي دخول", "انصراف مبكر", "إضافي الحضور والانصراف"):
                            styles.append(bg + " text-align: center;")
                        else:
                            styles.append(bg)
                    return styles

                edited_df = st.data_editor(
                    df_man_seed.style.apply(_style_man_grid, axis=1),
                    use_container_width=True,
                    hide_index=True,
                    disabled=["التاريخ", "الحالة / الإجازة", "تأخير دخول", "إضافي دخول", "انصراف مبكر", "إضافي الحضور والانصراف"],
                    column_config={
                        "التاريخ": st.column_config.TextColumn("التاريخ", width="small"),
                        "الحالة / الإجازة": st.column_config.TextColumn("الحالة / الإجازة", width="medium"),
                        "دخول": st.column_config.TextColumn("دخول (09:00)", width="small"),
                        "انصراف": st.column_config.TextColumn("انصراف (17:00)", width="small"),
                        "تأخير دخول": st.column_config.NumberColumn("تأخير دخول", width="small"),
                        "إضافي دخول": st.column_config.NumberColumn("إضافي دخول", width="small"),
                        "انصراف مبكر": st.column_config.NumberColumn("انصراف مبكر", width="small"),
                        "إضافي الحضور والانصراف": st.column_config.NumberColumn("إضافي الحضور والانصراف", width="small"),
                    },
                    key=f"manual_att_editor_{man_emp_id}_{man_ym}",
                )

                if st.button("💾 حفظ بصمات الشهر اليدوية", type="primary", key="save_manual_att_btn"):
                    s_start = str(man_emp.get("work_start_time") or "09:00:00")
                    s_end = str(man_emp.get("work_end_time") or "17:00:00")

                    blocked_hits: List[str] = []
                    invalid_hits: List[str] = []
                    valid_to_save: List[Dict[str, Any]] = []

                    for _, row_item in edited_df.iterrows():
                        d_iso = str(row_item.get("التاريخ") or "").strip()
                        raw_cin = str(row_item.get("دخول") or "").strip()
                        raw_cout = str(row_item.get("انصراف") or "").strip()
                        if not raw_cin and not raw_cout:
                            continue

                        if d_iso in blocked_reason_by_iso:
                            blocked_hits.append(f"{d_iso} ({blocked_reason_by_iso[d_iso]})")
                            continue

                        cin_norm = _normalize_time_text(raw_cin) if raw_cin else None
                        cout_norm = _normalize_time_text(raw_cout) if raw_cout else None

                        if (raw_cin and not cin_norm) or (raw_cout and not cout_norm):
                            invalid_hits.append(d_iso)
                            continue

                        w_dt = parse_date_safe(d_iso)
                        if not w_dt:
                            continue

                        eval_res = evaluate_attendance_record(
                            cin_norm or "",
                            cout_norm or "",
                            s_start,
                            s_end,
                            grace_late_minutes=grace_late,
                            grace_early_minutes=grace_early,
                            is_leave_day=False,
                            employee=man_emp,
                        )
                        valid_to_save.append({
                            "employee_id": man_emp["id"],
                            "work_date": w_dt.isoformat(),
                            "check_in": cin_norm,
                            "check_out": cout_norm,
                            "scheduled_start": s_start,
                            "scheduled_end": s_end,
                            "late_minutes": eval_res["late_minutes"],
                            "early_leave_minutes": eval_res["early_leave_minutes"],
                            "overtime_minutes": eval_res["overtime_minutes"],
                            "overtime_status": eval_res["overtime_status"],
                        })

                    if blocked_hits:
                        st.error(
                            f"🚫 ممنوع تسجيل بصمة في الأيام التالية وتم إيقاف الحفظ بالكامل ولم يسمع شيء في الشيتات:\n"
                            f"{' | '.join(blocked_hits)}\n"
                            f"يرجى مسح القيم المدخلة/الملصوقة في هذه الأيام ثم الضغط على حفظ بصمات الشهر اليدوية."
                        )
                    elif invalid_hits:
                        st.error(
                            f"⚠️ صيغة وقت غير صحيحة وتم إيقاف الحفظ في التواريخ: {', '.join(invalid_hits)} — يرجى الكتابة بصيغة HH:MM مثل 09:00 و 17:00."
                        )
                    else:
                        for rec_payload in valid_to_save:
                            upsert_record(
                                "attendance_logs",
                                rec_payload,
                                on_conflict="employee_id,work_date",
                            )

                        if valid_to_save:
                            write_audit_log(
                                "attendance_logs",
                                "manual_attendance",
                                "تعديل",
                                f"إدخال/تحديث {len(valid_to_save)} سجل بصمة يدوياً للموظف {man_emp.get('name_ar')} لشهر {man_ym}",
                                by_name=current_user.get("name_ar", ""),
                                by_user_id=user_id,
                            )
                            st.success(f"✅ تم حفظ {len(valid_to_save)} سجل بصمة للموظف {man_emp.get('name_ar')} بنجاح.")
                            st.rerun()
                        else:
                            st.info("لم يتم إدخال مواعيد جديدة لحفظها.")
        t_idx += 1

    if can_view_ot_log:
        with tabs[t_idx]:
            st.markdown("#### سجل الوقت الإضافي (للمدير العام و HR فقط)")
            ot_fc1, ot_fc2 = st.columns(2)
            with ot_fc1:
                ot_filter_ym = st.text_input("فلتر بالشهر (YYYY-MM أو اكتب الكل)", value=date.today().strftime("%Y-%m"), key="ot_f_ym")
            with ot_fc2:
                ot_emp_opts = ["الكل"] + [
                    f"{e.get('name_ar')} (#{e.get('employee_no')})"
                    for e in employees
                    if (e.get("status") or "active") == "active"
                ]
                sel_ot_emp = st.selectbox("فلتر باسم الموظف", ot_emp_opts, key="ot_f_emp")

            ot_records = [a for a in attendance_logs if int(a.get("overtime_minutes") or 0) > 0]
            ot_rows = []
            ym_clean = ot_filter_ym.strip()[:7]
            for rec in ot_records:
                emp_o = emp_by_id.get(str(rec.get("employee_id") or ""), {})
                emp_lbl = f"{emp_o.get('name_ar', '')} (#{emp_o.get('employee_no', '')})"
                rec_ym = str(rec.get("work_date") or "")[:7]
                if ym_clean and ym_clean.lower() not in ("الكل", "all") and rec_ym != ym_clean:
                    continue
                if sel_ot_emp != "الكل" and emp_lbl != sel_ot_emp:
                    continue
                ot_rows.append({
                    "الموظف": emp_o.get("name_ar", ""),
                    "الشهر": rec_ym,
                    "التاريخ": str(rec.get("work_date") or "")[:10],
                    "الانصراف": str(rec.get("check_out") or "")[:5],
                    "نهاية الدوام": str(rec.get("scheduled_end") or "")[:5],
                    "الوقت الإضافي (دقيقة)": rec.get("overtime_minutes"),
                    "حالة الاعتماد": rec.get("overtime_status"),
                })

            if ot_rows:
                st.dataframe(ot_rows, use_container_width=True, hide_index=True)
            else:
                st.info("لا توجد سجلات وقت إضافي مطابقة للفلاتر المختارة.")
        t_idx += 1

    with tabs[t_idx]:
        st.markdown("#### 💵 شيت المرتب الشهري الخاص بي")
        my_pay_ym = st.text_input("شهر المرتب (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="my_pay_sheet_ym")

        my_res = compute_employee_monthly_payroll(
            employee=current_user,
            payroll_month=my_pay_ym,
            shift=None,
            attendance_logs=attendance_logs,
            penalties_rewards=penalties_rewards,
            advances=advances,
            official_holidays=official_holidays,
            grace_late_minutes=grace_late,
            grace_early_minutes=grace_early,
        )

        emp_name_txt = my_res.get("name_ar") or current_user.get("name_ar") or "—"
        emp_job_txt = my_res.get("job_title") or current_user.get("job_title") or "موظف"
        emp_no_txt = my_res.get("employee_no") or current_user.get("employee_no") or "—"

        active_fa_my: Dict[str, float] = my_res.get("fixed_allowances") or {}
        fixed_rows_html = "".join(
            f"<tr><td>{fa_name}</td><td class='pay-plus'>+ {fa_amt:,.2f} ج</td></tr>"
            for fa_name, fa_amt in active_fa_my.items()
            if float(fa_amt or 0) > 0
        )

        pay_card_html = (
            "<style>"
            ".pay-card-box{background:#ffffff;border:1.5px solid #cbd5e1;border-radius:14px;padding:20px;margin-top:10px;box-shadow:0 4px 14px rgba(15,76,92,0.08);direction:rtl;font-family:'Cairo',sans-serif;}"
            ".pay-header-strip{background:linear-gradient(135deg,#0f4c5c 0%,#1c7285 100%);color:#ffffff;border-radius:10px;padding:12px 18px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:14px;}"
            ".pay-header-item{font-size:14.5px;font-weight:700;}"
            ".pay-base-row{background:#eff6ff;border:1.5px solid #93c5fd;border-radius:10px;padding:12px 16px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;font-size:15.5px;font-weight:700;color:#1e3a8a;}"
            ".pay-items-table{width:100%;border-collapse:collapse;margin-bottom:16px;}"
            ".pay-items-table th{background:#f1f5f9;color:#0f172a;font-weight:700;font-size:14px;padding:10px;border:1px solid #cbd5e1;text-align:center;}"
            ".pay-items-table td{padding:10px;border:1px solid #e2e8f0;font-size:14.5px;font-weight:600;text-align:center;color:#1e293b;}"
            ".pay-plus{color:#15803d;font-weight:700;}"
            ".pay-minus{color:#b91c1c;font-weight:700;}"
            ".pay-net-box{background:linear-gradient(135deg,#14532d 0%,#166534 100%);color:#ffffff;border-radius:11px;padding:14px 20px;text-align:center;font-size:18px;font-weight:700;}"
            "</style>"
            "<div class='pay-card-box'>"
            "<div class='pay-header-strip'>"
            f"<div class='pay-header-item'>👤 الاسم: {emp_name_txt}</div>"
            f"<div class='pay-header-item'>💼 الوظيفة: {emp_job_txt}</div>"
            f"<div class='pay-header-item'>🔢 الرقم الوظيفي: {emp_no_txt}</div>"
            "</div>"
            "<div class='pay-base-row'>"
            "<span>💰 المرتب الأساسي</span>"
            f"<span>{my_res['base_salary']:,.2f} جنيه</span>"
            "</div>"
            "<table class='pay-items-table'>"
            "<thead><tr><th>البند</th><th>القيمة (جنيه)</th></tr></thead>"
            "<tbody>"
            f"<tr><td>إجازات رسمية (+يوم)</td><td class='pay-plus'>+ {my_res['holiday_bonus']:,.2f} ج</td></tr>"
            f"<tr><td>المكافآت</td><td class='pay-plus'>+ {my_res['variable_rewards_total']:,.2f} ج</td></tr>"
            f"{fixed_rows_html}"
            f"<tr><td>إضافي الحضور والانصراف</td><td class='pay-plus'>+ {my_res['overtime_pay']:,.2f} ج</td></tr>"
            f"<tr><td>خصم السلف</td><td class='pay-minus'>- {my_res['advances_deduction']:,.2f} ج</td></tr>"
            f"<tr><td>خصم الجزاءات</td><td class='pay-minus'>- {my_res['penalties_deduction']:,.2f} ج</td></tr>"
            f"<tr><td>خصم التأخير / الانصراف</td><td class='pay-minus'>- {my_res['late_deduction']:,.2f} ج</td></tr>"
            "</tbody>"
            "</table>"
            f"<div class='pay-net-box'>✅ المرتب الفعلي: {my_res['net_salary']:,.2f} جنيه</div>"
            "</div>"
        )
        st.markdown(pay_card_html, unsafe_allow_html=True)
    t_idx += 1

    if can_view_all_payroll_sheet and t_idx < len(tabs):
        with tabs[t_idx]:
            st.markdown("#### 📊 شيت المرتبات الشهري والأرشيف")
            pay_ym = st.text_input("شهر المرتب (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="pay_sheet_ym")

            target_payroll_emps = [e for e in employees if (e.get("status") or "active") == "active"]

            computed_list = []
            for emp in target_payroll_emps:
                res = compute_employee_monthly_payroll(
                    employee=emp,
                    payroll_month=pay_ym,
                    shift=None,
                    attendance_logs=attendance_logs,
                    penalties_rewards=penalties_rewards,
                    advances=advances,
                    official_holidays=official_holidays,
                    grace_late_minutes=grace_late,
                    grace_early_minutes=grace_early,
                )
                computed_list.append(res)

            if computed_list:
                sheet_rows = [
                    {
                        "الرقم الوظيفي": r["employee_no"],
                        "الاسم": r["name_ar"],
                        "المسمى": r["job_title"],
                        "المرتب الأساسي": r["base_salary"],
                        "إجازات رسمية (+يوم)": r["holiday_bonus"],
                        "المكافآت": r["variable_rewards_total"],
                        "مكافأة ثابتة": r["fixed_allowances_total"],
                        "إضافي الحضور والانصراف": r["overtime_pay"],
                        "خصم السلف": r["advances_deduction"],
                        "خصم الجزاءات": r["penalties_deduction"],
                        "خصم التأخير/الانصراف": r["late_deduction"],
                        "المرتب الفعلي": r["net_salary"],
                    }
                    for r in computed_list
                ]

                tot_base = round_half_up_2(sum(float(r["base_salary"] or 0) for r in computed_list))
                tot_hol = round_half_up_2(sum(float(r["holiday_bonus"] or 0) for r in computed_list))
                tot_var_rew = round_half_up_2(sum(float(r["variable_rewards_total"] or 0) for r in computed_list))
                tot_fixed_all = round_half_up_2(sum(float(r["fixed_allowances_total"] or 0) for r in computed_list))
                tot_rew = round_half_up_2(sum(float(r["rewards_total"] or 0) for r in computed_list))
                tot_ot = round_half_up_2(sum(float(r["overtime_pay"] or 0) for r in computed_list))
                tot_adv = round_half_up_2(sum(float(r["advances_deduction"] or 0) for r in computed_list))
                tot_pen = round_half_up_2(sum(float(r["penalties_deduction"] or 0) for r in computed_list))
                tot_late = round_half_up_2(sum(float(r["late_deduction"] or 0) for r in computed_list))
                tot_all_ded = round_half_up_2(tot_adv + tot_pen + tot_late)
                tot_net = round_half_up_2(sum(float(r["net_salary"] or 0) for r in computed_list))

                total_row_dict = {
                    "الرقم الوظيفي": "—",
                    "الاسم": "الإجمالي العام",
                    "المسمى": f"عدد: {len(computed_list)}",
                    "المرتب الأساسي": tot_base,
                    "إجازات رسمية (+يوم)": tot_hol,
                    "المكافآت": tot_var_rew,
                    "مكافأة ثابتة": tot_fixed_all,
                    "إضافي الحضور والانصراف": tot_ot,
                    "خصم السلف": tot_adv,
                    "خصم الجزاءات": tot_pen,
                    "خصم التأخير/الانصراف": tot_late,
                    "المرتب الفعلي": tot_net,
                }

                st.dataframe(sheet_rows + [total_row_dict], use_container_width=True, hide_index=True)

                st.markdown("#### 📌 ملخص الإجماليات النهائية للشيت")
                g1, g2, g3, g4, g5, g6 = st.columns(6)
                g1.metric("إجمالي المرتبات الأساسية", f"{tot_base:,.2f} ج")
                g2.metric("إجمالي المكافآت", f"{tot_rew:,.2f} ج")
                g3.metric("إضافي الحضور والانصراف", f"{tot_ot:,.2f} ج")
                g4.metric("إجمالي الخصومات", f"{tot_all_ded:,.2f} ج")
                g5.metric("إجمالي الجزاءات", f"{tot_pen:,.2f} ج")
                g6.metric("إجمالي المرتبات الفعلية", f"{tot_net:,.2f} ج")

                excel_buf = io.BytesIO()
                with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
                    pd.DataFrame(sheet_rows + [total_row_dict]).to_excel(writer, index=False, sheet_name="Payroll")
                st.download_button(
                    "📥 تحميل شيت المرتبات Excel",
                    data=excel_buf.getvalue(),
                    file_name=f"payroll_{pay_ym}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

                if (perms.get("manage_payroll") or user_role in ("hr", "general_manager")) and st.button(
                    "🔒 حفظ وأرشفة مسير الشهر في الأرشيف", type="primary"
                ):
                    for r in computed_list:
                        upsert_record(
                            "payroll_runs",
                            {
                                "payroll_month": pay_ym[:7],
                                "employee_id": r["employee_id"],
                                "base_salary": r["base_salary"],
                                "holiday_bonus": r["holiday_bonus"],
                                "rewards_total": r["rewards_total"],
                                "overtime_pay": r["overtime_pay"],
                                "advances_deduction": r["advances_deduction"],
                                "penalties_deduction": r["penalties_deduction"],
                                "late_deduction": r["late_deduction"],
                                "net_salary": r["net_salary"],
                                "is_closed": True,
                                "closed_by": user_id,
                                "closed_at": datetime.utcnow().isoformat() + "Z",
                            },
                            on_conflict="payroll_month,employee_id",
                        )
                    write_audit_log(
                        "payroll_runs",
                        "close_monthly_payroll",
                        "أرشفة",
                        f"حفظ وأرشفة مسير مرتبات شهر {pay_ym}",
                        by_name=current_user.get("name_ar", ""),
                        by_user_id=user_id,
                    )
                    st.success(f"✅ تم حفظ وأرشفة مرتبات شهر {pay_ym} بنجاح.")
                    st.rerun()

            st.markdown("#### أرشيف الشهور السابقة المحفوظة")
            if payroll_runs:
                months_avail = sorted({str(p.get("payroll_month")) for p in payroll_runs if p.get("payroll_month")}, reverse=True)
                sel_arch_ym = st.selectbox("اختر شهراً من الأرشيف", months_avail, key="arch_ym_sel")
                arch_filtered = [p for p in payroll_runs if str(p.get("payroll_month")) == sel_arch_ym]
                st.dataframe(
                    [
                        {
                            "الموظف": emp_by_id.get(str(p.get("employee_id") or ""), {}).get("name_ar", p.get("employee_id")),
                            "الأساسي": p.get("base_salary"),
                            "إجازات رسمية": p.get("holiday_bonus"),
                            "مكافآت": p.get("rewards_total"),
                            "إضافي الحضور والانصراف": p.get("overtime_pay"),
                            "سلف": p.get("advances_deduction"),
                            "جزاءات": p.get("penalties_deduction"),
                            "تأخيرات": p.get("late_deduction"),
                            "المرتب الفعلي": p.get("net_salary"),
                        }
                        for p in arch_filtered
                    ],
                    use_container_width=True,
                    hide_index=True,
                )


# =====================================================================
# 6. التقارير المستقلة
# =====================================================================
def render_reports_module(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    leave_requests: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    kpi_evaluations: List[Dict[str, Any]],
) -> None:
    st.markdown("### 📑 التقارير التفصيلية")
    emp_by_id = {str(e["id"]): e for e in employees if e.get("id")}

    rep_type = st.selectbox(
        "اختر نوع التقرير",
        ["تقرير الجزاءات الشهري", "تقرير المكافآت الشهري", "تقرير الإجازات الشهري", "تقرير شامل لموظف محدد"],
        key="main_rep_type_sel",
    )
    rep_ym = st.text_input("الشهر (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="main_rep_ym_inp")

    if rep_type == "تقرير الجزاءات الشهري":
        rows = [
            {
                "الموظف": emp_by_id.get(str(p.get("employee_id") or ""), {}).get("name_ar", ""),
                "التاريخ": str(p.get("issue_date") or "")[:10],
                "القيمة": p.get("calculated_amount"),
                "العنوان": p.get("title"),
                "السبب": p.get("reason"),
                "الحالة": p.get("status"),
            }
            for p in penalties_rewards
            if p.get("entry_type") == "penalty" and str(p.get("payroll_month") or "")[:7] == rep_ym[:7]
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    elif rep_type == "تقرير المكافآت الشهري":
        rows = [
            {
                "الموظف": emp_by_id.get(str(p.get("employee_id") or ""), {}).get("name_ar", ""),
                "التاريخ": str(p.get("issue_date") or "")[:10],
                "القيمة": p.get("calculated_amount"),
                "العنوان": p.get("title"),
                "السبب": p.get("reason"),
                "الحالة": p.get("status"),
            }
            for p in penalties_rewards
            if p.get("entry_type") == "reward" and str(p.get("payroll_month") or "")[:7] == rep_ym[:7]
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    elif rep_type == "تقرير الإجازات الشهري":
        rows = [
            {
                "الموظف": lr.get("employee_name"),
                "النوع": lr.get("type_name"),
                "من": str(lr.get("start_date"))[:10],
                "إلى": str(lr.get("end_date"))[:10],
                "الأيام": lr.get("days"),
                "الحالة": lr.get("status"),
            }
            for lr in leave_requests
            if str(lr.get("start_date") or "")[:7] == rep_ym[:7]
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    else:
        emp_map = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in employees}
        if emp_map:
            sel_e = st.selectbox("اختر الموظف", list(emp_map.keys()), key="main_rep_emp_sel")
            e_obj = emp_map[sel_e]
            eid_str = str(e_obj["id"])
            st.write("#### الإجازات")
            st.dataframe([lr for lr in leave_requests if str(lr.get("employee_id")) == eid_str], use_container_width=True, hide_index=True)
            st.write("#### الجزاءات والمكافآت")
            st.dataframe([pr for pr in penalties_rewards if str(pr.get("employee_id")) == eid_str], use_container_width=True, hide_index=True)
            st.write("#### السلف")
            st.dataframe([adv for adv in advances if str(adv.get("employee_id")) == eid_str], use_container_width=True, hide_index=True)


# =====================================================================
# 7. شاشة الـ KPIs
# =====================================================================
def render_kpis_module(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    leave_requests: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    kpi_evaluations: List[Dict[str, Any]],
    org_settings: Dict[str, Any],
) -> None:
    st.markdown("### 🎯 تقييم الأداء (KPIs)")
    user_id = str(current_user.get("id"))
    user_role = (current_user.get("role") or "employee").strip().lower()
    emp_by_any_id: Dict[str, Dict[str, Any]] = {}
    for e in employees:
        for k in _emp_identifiers(e):
            emp_by_any_id[k] = e

    kpi_active = bool(st.session_state.get("org_cache_kpi_evaluation_active", org_settings.get("kpi_evaluation_active", False)))
    eval_cycle_type = str(st.session_state.get("org_cache_kpi_cycle_type") or org_settings.get("kpi_cycle_type") or "نصف سنوي")
    kpi_start_dt_str = str(st.session_state.get("org_cache_kpi_start_date") or org_settings.get("kpi_start_date") or date.today().isoformat())[:10]
    period_lbl = f"{eval_cycle_type} — يبدأ من {kpi_start_dt_str}"

    if not kpi_active and user_role not in ("general_manager", "hr"):
        st.warning("فترة تقييم الأداء (KPIs) غير مفعّلة حالياً.")
        return

    st.caption(f"فترة التقييم الحالية: **{eval_cycle_type}** | تاريخ البداية: **`{kpi_start_dt_str}`**")

    self_q_by_tab = _get_all_self_questions(org_settings)
    peer_q_by_target = _get_all_peer_questions(org_settings)

    mapped_tab = _map_job_title_to_self_tab(current_user)
    sel_self_tab = st.selectbox(
        "فئة الأسئلة للتقييم الذاتي (المرتبطة بالمسمى الوظيفي)",
        KPI_SELF_TABS,
        index=KPI_SELF_TABS.index(mapped_tab) if mapped_tab in KPI_SELF_TABS else 0,
        key="kpi_user_self_tab_sel",
    )

    p_col1, p_col2 = st.columns(2)
    p_start = p_col1.date_input(
        "من تاريخ (لفترة السجل الآلي)",
        value=parse_date_safe(kpi_start_dt_str) or date(date.today().year, 1, 1),
        key="kpi_auto_from",
    )
    p_end = p_col2.date_input("إلى تاريخ", value=date.today(), key="kpi_auto_to")

    sub_tabs = st.tabs([
        "🙋 تقييم نفسي",
        "👨‍🔬 تقييم السينيور",
        "👔 تقييم المدير المباشر",
        "🏢 تقييم المدير العام",
    ])

    with sub_tabs[0]:
        st.markdown(f"#### التقييم الذاتي — **{sel_self_tab}**")
        q_items = self_q_by_tab.get(sel_self_tab) or []
        max_possible = round_half_up_2(sum(float(item.get("points") or 0) for item in q_items))
        self_scores: Dict[str, float] = {}

        for idx_q, item in enumerate(q_items):
            q_label = str(item.get("question") or "")
            q_pts = max(0.5, float(item.get("points") or 10.0))
            val_q = st.slider(
                f"{q_label} (الدرجة القصوى: {q_pts:g})",
                min_value=0.0,
                max_value=float(q_pts),
                value=float(min(q_pts, round_half_up_2(q_pts * 0.8))),
                step=0.5,
                key=f"kpi_self_sl_{user_id}_{sel_self_tab}_{idx_q}",
            )
            self_scores[q_label] = float(val_q)

        self_total = round_half_up_2(sum(self_scores.values()))
        self_pct = round_half_up_2((self_total / max_possible) * 100.0) if max_possible > 0 else 0.0

        m1, m2 = st.columns(2)
        m1.metric("مجموع الدرجات", f"{self_total:g} / {max_possible:g}")
        m2.metric("النسبة المئوية", f"{self_pct:g}%")

        st.divider()
        st.markdown("##### 📊 ملخص الأداء الآلي للفترة")
        auto_sum = build_employee_period_summary(
            employee=current_user,
            start_date=p_start,
            end_date=p_end,
            leave_requests=leave_requests,
            attendance_logs=attendance_logs,
            penalties_rewards=penalties_rewards,
            advances=advances,
        )
        s1, s2, s3, s4, s5, s6 = st.columns(6)
        s1.metric("أيام الإجازات", auto_sum["approved_leaves_days"])
        s2.metric("مرات التأخير", auto_sum["late_days_count"])
        s3.metric("دقائق التأخير", auto_sum["total_late_minutes"])
        s4.metric("إضافي معتمد (د)", auto_sum["approved_overtime_minutes"])
        s5.metric("المكافآت", f"{auto_sum['total_rewards']} ج")
        s6.metric("الجزاءات / السلف", f"{auto_sum['total_penalties']} / {auto_sum['total_advances']} ج")

        if st.button("💾 حفظ التقييم الذاتي", type="primary", key="save_kpi_self_btn"):
            upsert_record(
                "kpi_evaluations",
                {
                    "employee_id": current_user["id"],
                    "manager_id": user_id,
                    "period_label": f"{period_lbl} | ذاتي ({sel_self_tab})",
                    "period_start": p_start.isoformat(),
                    "period_end": p_end.isoformat(),
                    "self_scores": self_scores,
                    "self_percentage": self_pct,
                    "auto_summary": auto_sum,
                    "status": "submitted_by_employee",
                },
                on_conflict="employee_id,period_label",
            )
            write_audit_log(
                "kpi_evaluations",
                "save_kpi_self",
                "تقييم",
                f"حفظ تقييم ذاتي ({self_total:g}/{max_possible:g} = {self_pct:g}%) للموظف {current_user.get('name_ar')}",
                by_name=current_user.get("name_ar", ""),
                by_user_id=user_id,
            )
            st.success("✅ تم حفظ التقييم الذاتي بنجاح.")
            st.rerun()

    with sub_tabs[1]:
        sen_id_str = str(current_user.get("senior_id") or "").strip()
        sen_emp = emp_by_any_id.get(sen_id_str)
        if not sen_emp:
            st.info("لا يوجد سينيور محدد لحسابك حالياً.")
        else:
            st.markdown(f"#### تقييم السينيور: **{sen_emp.get('name_ar')}**")
            sen_qs = peer_q_by_target.get("senior") or []
            max_sen_pts = round_half_up_2(sum(float(item.get("points") or 0) for item in sen_qs))
            sen_scores: Dict[str, float] = {}
            for idx_s, item_s in enumerate(sen_qs):
                q_s = str(item_s.get("question") or "")
                pts_s = max(0.5, float(item_s.get("points") or 10.0))
                sen_scores[q_s] = float(
                    st.slider(
                        f"{q_s} (الدرجة القصوى: {pts_s:g})",
                        min_value=0.0,
                        max_value=float(pts_s),
                        value=float(min(pts_s, round_half_up_2(pts_s * 0.8))),
                        step=0.5,
                        key=f"kpi_rate_sen_{user_id}_{sen_emp['id']}_{idx_s}",
                    )
                )
            sen_tot = round_half_up_2(sum(sen_scores.values()))
            sen_pct = round_half_up_2((sen_tot / max_sen_pts) * 100.0) if max_sen_pts > 0 else 0.0
            sm1, sm2 = st.columns(2)
            sm1.metric("مجموع الدرجات", f"{sen_tot:g} / {max_sen_pts:g}")
            sm2.metric("النسبة المئوية", f"{sen_pct:g}%")
            if st.button("💾 حفظ تقييم السينيور", type="primary", key="save_kpi_rate_sen_btn"):
                upsert_record(
                    "kpi_evaluations",
                    {
                        "employee_id": sen_emp["id"],
                        "manager_id": user_id,
                        "period_label": f"{period_lbl} | تقييم سينيور بواسطة {current_user.get('name_ar')}",
                        "period_start": p_start.isoformat(),
                        "period_end": p_end.isoformat(),
                        "manager_scores": sen_scores,
                        "manager_percentage": sen_pct,
                        "status": "peer_senior_evaluated",
                    },
                    on_conflict="employee_id,period_label",
                )
                st.success("✅ تم حفظ تقييم السينيور بنجاح.")
                st.rerun()

    with sub_tabs[2]:
        mgr_id_str = str(current_user.get("direct_manager_id") or "").strip()
        mgr_emp = emp_by_any_id.get(mgr_id_str)
        if not mgr_emp:
            st.info("لا يوجد مدير مباشر محدد لحسابك حالياً.")
        else:
            st.markdown(f"#### تقييم المدير المباشر: **{mgr_emp.get('name_ar')}**")
            mgr_qs = peer_q_by_target.get("manager") or []
            max_mgr_pts = round_half_up_2(sum(float(item.get("points") or 0) for item in mgr_qs))
            mgr_scores_val: Dict[str, float] = {}
            for idx_m, item_m in enumerate(mgr_qs):
                q_m = str(item_m.get("question") or "")
                pts_m = max(0.5, float(item_m.get("points") or 10.0))
                mgr_scores_val[q_m] = float(
                    st.slider(
                        f"{q_m} (الدرجة القصوى: {pts_m:g})",
                        min_value=0.0,
                        max_value=float(pts_m),
                        value=float(min(pts_m, round_half_up_2(pts_m * 0.8))),
                        step=0.5,
                        key=f"kpi_rate_mgr_{user_id}_{mgr_emp['id']}_{idx_m}",
                    )
                )
            mgr_tot = round_half_up_2(sum(mgr_scores_val.values()))
            mgr_pct = round_half_up_2((mgr_tot / max_mgr_pts) * 100.0) if max_mgr_pts > 0 else 0.0
            mm1, mm2 = st.columns(2)
            mm1.metric("مجموع الدرجات", f"{mgr_tot:g} / {max_mgr_pts:g}")
            mm2.metric("النسبة المئوية", f"{mgr_pct:g}%")
            if st.button("💾 حفظ تقييم المدير المباشر", type="primary", key="save_kpi_rate_mgr_btn"):
                upsert_record(
                    "kpi_evaluations",
                    {
                        "employee_id": mgr_emp["id"],
                        "manager_id": user_id,
                        "period_label": f"{period_lbl} | تقييم مدير مباشر بواسطة {current_user.get('name_ar')}",
                        "period_start": p_start.isoformat(),
                        "period_end": p_end.isoformat(),
                        "manager_scores": mgr_scores_val,
                        "manager_percentage": mgr_pct,
                        "status": "peer_manager_evaluated",
                    },
                    on_conflict="employee_id,period_label",
                )
                st.success("✅ تم حفظ تقييم المدير المباشر بنجاح.")
                st.rerun()

    with sub_tabs[3]:
        gm_emp = next((e for e in employees if str(e.get("role") or "").lower() == "general_manager"), None)
        gm_target_id = gm_emp["id"] if gm_emp else None
        gm_name = gm_emp.get("name_ar") if gm_emp else "المدير العام"

        st.markdown(f"#### تقييم المدير العام: **{gm_name}**")
        gm_qs = peer_q_by_target.get("gm") or []
        max_gm_pts = round_half_up_2(sum(float(item.get("points") or 0) for item in gm_qs))
        gm_scores_val: Dict[str, float] = {}
        for idx_g, item_g in enumerate(gm_qs):
            q_g = str(item_g.get("question") or "")
            pts_g = max(0.5, float(item_g.get("points") or 10.0))
            gm_scores_val[q_g] = float(
                st.slider(
                    f"{q_g} (الدرجة القصوى: {pts_g:g})",
                    min_value=0.0,
                    max_value=float(pts_g),
                    value=float(min(pts_g, round_half_up_2(pts_g * 0.8))),
                    step=0.5,
                    key=f"kpi_rate_gm_{user_id}_{idx_g}",
                )
            )
        gm_tot = round_half_up_2(sum(gm_scores_val.values()))
        gm_pct = round_half_up_2((gm_tot / max_gm_pts) * 100.0) if max_gm_pts > 0 else 0.0
        gm1, gm2 = st.columns(2)
        gm1.metric("مجموع الدرجات", f"{gm_tot:g} / {max_gm_pts:g}")
        gm2.metric("النسبة المئوية", f"{gm_pct:g}%")
        if st.button("💾 حفظ تقييم المدير العام", type="primary", key="save_kpi_rate_gm_btn") and gm_target_id:
            upsert_record(
                "kpi_evaluations",
                {
                    "employee_id": gm_target_id,
                    "manager_id": user_id,
                    "period_label": f"{period_lbl} | تقييم مدير عام بواسطة {current_user.get('name_ar')}",
                    "period_start": p_start.isoformat(),
                    "period_end": p_end.isoformat(),
                    "manager_scores": gm_scores_val,
                    "manager_percentage": gm_pct,
                    "status": "peer_gm_evaluated",
                },
                on_conflict="employee_id,period_label",
            )
            st.success("✅ تم حفظ تقييم المدير العام بنجاح.")
            st.rerun()


# =====================================================================
# 8. الموظفون والفروع والصلاحيات والإعدادات
# =====================================================================
def render_admin_and_staff(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    departments: List[Dict[str, Any]],
    shifts: List[Dict[str, Any]],
    org_settings: Dict[str, Any],
    official_holidays: Optional[List[Dict[str, Any]]] = None,
) -> None:
    st.markdown("### 👥 إدارة الموظفين والفروع والصلاحيات والإعدادات")
    perms = get_effective_permissions(current_user)
    user_id = str(current_user.get("id"))
    user_role = (current_user.get("role") or "employee").lower()
    holidays_list = official_holidays or []
    fa_types_list = _get_fixed_allowance_types(org_settings)

    if not (perms.get("manage_settings") or perms.get("manage_payroll") or user_role in ("general_manager", "hr")):
        st.warning("هذا القسم مخصص للإدارة والموارد البشرية.")
        return

    tab_emp, tab_org, tab_perms_hol, tab_kpi_cfg = st.tabs([
        "👤 الموظفون وتعديل البيانات",
        "🏢 الفروع",
        "🔐 الصلاحيات والإعدادات",
        "🎯 KPIs",
    ])

    with tab_emp:
        st.markdown("#### إضافة موظف جديد")
        dept_choices = {d.get("name", ""): d["id"] for d in departments}
        emp_ref_choices = {"لا يوجد": None}
        for e in employees:
            emp_ref_choices[f"{e.get('name_ar')} ({e.get('role')})"] = e.get("team_user_id") or e.get("id")

        t_col1, t_col2, t_col3 = st.columns(3)
        with t_col1:
            add_w_start = st.time_input("من الساعة", value=time(9, 0), key="add_emp_work_start")
        with t_col2:
            add_w_end = st.time_input("إلى الساعة", value=time(17, 0), key="add_emp_work_end")
        add_hours_val = _calculate_work_hours(add_w_start, add_w_end)
        add_hours_str = f"{add_hours_val:g} ساعات" if add_hours_val != 1.0 else "ساعة واحدة"
        with t_col3:
            st.text_input("عدد ساعات العمل", value=add_hours_str, disabled=True, key=f"add_emp_hours_box_{add_hours_val}")

        sal_fa_col1, sal_fa_col2 = st.columns([1.2, 2.8])
        with sal_fa_col1:
            base_sal = st.number_input("المرتب الأساسي (جنيه)", min_value=0.0, value=5000.0, step=250.0, key="add_emp_base_sal")
        with sal_fa_col2:
            selected_add_fa_names = st.multiselect(
                "مكافآت ثابتة (اختر بنداً أو أكثر)",
                fa_types_list,
                default=[],
                key="add_emp_fa_multiselect",
            )

        add_fa_values: Dict[str, float] = {}
        if selected_add_fa_names:
            st.caption("أدخل قيمة كل مكافأة ثابتة مختارة بالجنيه (تُضاف تلقائياً للمرتب طالما أكبر من صفر):")
            fa_cols = st.columns(min(4, len(selected_add_fa_names)))
            for idx_fa, fa_lbl in enumerate(selected_add_fa_names):
                with fa_cols[idx_fa % len(fa_cols)]:
                    add_fa_values[fa_lbl] = float(
                        st.number_input(
                            f"{fa_lbl} (ج)",
                            min_value=0.0,
                            value=0.0,
                            step=50.0,
                            key=f"add_emp_fa_val_{fa_lbl}",
                        )
                    )

        with st.form("create_emp_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                eno = st.text_input("الرقم الوظيفي", placeholder="مثال: 101")
                name_ar = st.text_input("اسم الموظف", placeholder="الاسم الرباعي")
                job_title = st.selectbox("المسمى الوظيفي", EMPLOYEE_JOB_TITLES)
            with c2:
                hire_dt = st.date_input("تاريخ التعيين", value=date.today())
                role_val = st.selectbox(
                    "الدور الوظيفي",
                    ["employee", "senior", "manager", "hr", "general_manager", "chairman", "vice_chairman"],
                )
                dept_lbl = st.selectbox("الفرع / القسم", ["—"] + list(dept_choices.keys()))
            with c3:
                pin_val = st.text_input("رمز الدخول (PIN)", value="1234")
                sen_lbl = st.selectbox("الـ Senior المسؤول عن الإجازات (إن وُجد)", list(emp_ref_choices.keys()))
                mgr_lbl = st.selectbox("المدير المباشر / مدير الفرع", list(emp_ref_choices.keys()))

            if st.form_submit_button("إضافة الموظف", type="primary"):
                if eno.strip() and name_ar.strip():
                    clean_fa_payload = {
                        k: round_half_up_2(float(v or 0))
                        for k, v in add_fa_values.items()
                        if float(v or 0) > 0
                    }
                    insert_record("employees", {
                        "employee_no": eno.strip(),
                        "name_ar": name_ar.strip(),
                        "job_title": str(job_title).strip(),
                        "role": role_val,
                        "department_id": dept_choices.get(dept_lbl),
                        "work_start_time": add_w_start.strftime("%H:%M:%S"),
                        "work_end_time": add_w_end.strftime("%H:%M:%S"),
                        "work_hours": float(add_hours_val),
                        "senior_id": emp_ref_choices.get(sen_lbl),
                        "direct_manager_id": emp_ref_choices.get(mgr_lbl),
                        "hire_date": hire_dt.isoformat(),
                        "base_salary": float(base_sal),
                        "fixed_allowances": clean_fa_payload,
                        "pin_code": pin_val.strip(),
                        "status": "active",
                        "created_by": user_id,
                    })
                    write_audit_log(
                        "employees",
                        "create_employee",
                        "إضافة",
                        f"إضافة الموظف {name_ar.strip()} ({eno.strip()}) — المسمى: {job_title}",
                        by_name=current_user.get("name_ar", ""),
                        by_user_id=user_id,
                    )
                    st.success("✅ تمت إضافة الموظف بنجاح.")
                    st.rerun()

        st.markdown("#### تعديل بيانات الموظفين (الاسم، المسمى الوظيفي، تاريخ التعيين، المرتب، المكافآت الثابتة، ومواعيد العمل)")
        if employees:
            edit_map = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in employees}
            sel_edit = st.selectbox("اختر موظفاً للتعديل", list(edit_map.keys()), key="edit_emp_picker")
            target_e = edit_map[sel_edit]

            default_start_t = _parse_time_safe(target_e.get("work_start_time"), time(9, 0))
            default_end_t = _parse_time_safe(target_e.get("work_end_time"), time(17, 0))

            et_col1, et_col2, et_col3 = st.columns(3)
            with et_col1:
                upd_w_start = st.time_input("من الساعة (تعديل)", value=default_start_t, key=f"upd_w_start_{target_e['id']}")
            with et_col2:
                upd_w_end = st.time_input("إلى الساعة (تعديل)", value=default_end_t, key=f"upd_w_end_{target_e['id']}")
            upd_hours_val = _calculate_work_hours(upd_w_start, upd_w_end)
            with et_col3:
                st.text_input("عدد ساعات العمل", value=f"{upd_hours_val:g} ساعات", disabled=True, key=f"upd_emp_hours_{target_e['id']}_{upd_hours_val}")

            existing_fa_map = extract_active_fixed_allowances(target_e)
            all_fa_options_edit = list(dict.fromkeys(fa_types_list + list(existing_fa_map.keys())))

            upd_sal_col, upd_fa_sel_col = st.columns([1.2, 2.8])
            with upd_sal_col:
                new_sal = st.number_input(
                    "المرتب الأساسي",
                    min_value=0.0,
                    value=float(target_e.get("base_salary") or 0),
                    step=250.0,
                    key=f"upd_emp_sal_{target_e['id']}",
                )
            with upd_fa_sel_col:
                picked_upd_fa = st.multiselect(
                    "مكافآت ثابتة للموظف",
                    all_fa_options_edit,
                    default=list(existing_fa_map.keys()),
                    key=f"upd_emp_fa_sel_{target_e['id']}",
                )

            upd_fa_values: Dict[str, float] = {}
            if picked_upd_fa:
                u_fa_cols = st.columns(min(4, len(picked_upd_fa)))
                for idx_u, fa_lbl_u in enumerate(picked_upd_fa):
                    with u_fa_cols[idx_u % len(u_fa_cols)]:
                        def_val_u = float(existing_fa_map.get(fa_lbl_u) or 0.0)
                        upd_fa_values[fa_lbl_u] = float(
                            st.number_input(
                                f"{fa_lbl_u} (ج)",
                                min_value=0.0,
                                value=def_val_u,
                                step=50.0,
                                key=f"upd_emp_fa_val_{target_e['id']}_{fa_lbl_u}",
                            )
                        )

            cur_jt = str(target_e.get("job_title") or "").strip()
            jt_options_edit = list(dict.fromkeys(EMPLOYEE_JOB_TITLES + ([cur_jt] if cur_jt else [])))
            def_jt_idx = jt_options_edit.index(cur_jt) if cur_jt in jt_options_edit else 0

            with st.form("edit_emp_form"):
                ec1, ec2, ec3 = st.columns(3)
                new_name = ec1.text_input("الاسم", value=target_e.get("name_ar") or "")
                new_title = ec2.selectbox("المسمى الوظيفي", jt_options_edit, index=def_jt_idx)
                new_hire = ec3.date_input("تاريخ التعيين", value=parse_date_safe(target_e.get("hire_date")) or date.today())

                new_status = st.selectbox("الحالة", ["active", "inactive"], index=0 if (target_e.get("status") or "active") == "active" else 1)

                if st.form_submit_button("حفظ التعديلات", type="primary"):
                    clean_upd_fa = {
                        k: round_half_up_2(float(v or 0))
                        for k, v in upd_fa_values.items()
                        if float(v or 0) > 0
                    }
                    upd_payload: Dict[str, Any] = {
                        "name_ar": new_name.strip(),
                        "job_title": str(new_title).strip(),
                        "hire_date": new_hire.isoformat(),
                        "base_salary": float(new_sal),
                        "fixed_allowances": clean_upd_fa,
                        "status": new_status,
                        "work_start_time": upd_w_start.strftime("%H:%M:%S"),
                        "work_end_time": upd_w_end.strftime("%H:%M:%S"),
                        "work_hours": float(upd_hours_val),
                    }
                    update_record("employees", target_e["id"], upd_payload)
                    write_audit_log(
                        "employees",
                        "update_employee",
                        "تعديل",
                        f"تعديل بيانات الموظف {new_name.strip()}",
                        by_name=current_user.get("name_ar", ""),
                        by_user_id=user_id,
                    )
                    st.success("✅ تم حفظ تعديلات الموظف.")
                    st.rerun()

        st.dataframe(
            [
                {
                    "رقم": e.get("employee_no"),
                    "الاسم": e.get("name_ar"),
                    "المسمى الوظيفي": e.get("job_title"),
                    "الدور": e.get("role"),
                    "من الساعة": str(e.get("work_start_time") or "09:00")[:5],
                    "إلى الساعة": str(e.get("work_end_time") or "17:00")[:5],
                    "ساعات العمل": e.get("work_hours") if e.get("work_hours") is not None else _calculate_work_hours(
                        _parse_time_safe(e.get("work_start_time"), time(9, 0)),
                        _parse_time_safe(e.get("work_end_time"), time(17, 0)),
                    ),
                    "المرتب الأساسي": e.get("base_salary"),
                    "مكافآت ثابتة": "، ".join(f"{k}: {v:g}" for k, v in extract_active_fixed_allowances(e).items()) or "—",
                    "الحالة": e.get("status"),
                }
                for e in employees
            ],
            use_container_width=True,
            hide_index=True,
        )

    with tab_org:
        st.markdown("#### إضافة فرع / قسم")
        with st.form("add_dept_form", clear_on_submit=True):
            dc1, dc2, dc3 = st.columns([1.5, 2.5, 1])
            with dc1:
                d_code = st.text_input("كود الفرع", placeholder="مثال: BR-01")
            with dc2:
                d_name = st.text_input("اسم الفرع / القسم", placeholder="مثال: فرع إمبابة")
            with dc3:
                st.write("")
                st.write("")
                add_dept_btn = st.form_submit_button("إضافة فرع", type="primary")

            if add_dept_btn:
                if d_code.strip() and d_name.strip():
                    insert_record("departments", {"code": d_code.strip(), "name": d_name.strip(), "active": True})
                    st.success("✅ تمت إضافة الفرع.")
                    st.rerun()

        st.dataframe(
            [{"الكود": d.get("code"), "الاسم": d.get("name")} for d in departments],
            use_container_width=True,
            hide_index=True,
        )

    with tab_perms_hol:
        st.markdown("#### تخصيص صلاحيات مستخدم محدد (بما في ذلك منح المكافآت للـ Senior)")
        if employees:
            pmap = {f"{e.get('name_ar')} ({e.get('role')})": e for e in employees}
            sel_p = st.selectbox("اختر المستخدم", list(pmap.keys()), key="perm_emp_sel")
            p_emp = pmap[sel_p]
            eff = get_effective_permissions(p_emp)

            with st.form("edit_perms_form"):
                pc1, pc2, pc3 = st.columns(3)
                p_leaves = pc1.checkbox("اعتماد الإجازات", value=eff["approve_leaves"])
                p_rewards = pc2.checkbox("إضافة/اعتماد المكافآت", value=eff["approve_rewards"])
                p_penalties = pc3.checkbox("إضافة/اعتماد الجزاءات", value=eff["approve_penalties"])

                pc4, pc5, pc6 = st.columns(3)
                p_adv = pc4.checkbox("اعتماد السلف", value=eff["approve_advances"])
                p_ot = pc5.checkbox("اعتماد الوقت الإضافي", value=eff["approve_overtime"])
                p_pay = pc6.checkbox("إدارة المرتبات (Payroll)", value=eff["manage_payroll"])

                pc7, pc8 = st.columns(2)
                p_rep = pc7.checkbox("عرض كل التقارير / المتابعة", value=eff["view_all_reports"])
                p_mon = pc8.checkbox("وضع المتابعة فقط (بدون موافقات)", value=eff["is_monitor_only"])

                if st.form_submit_button("حفظ الصلاحيات", type="primary"):
                    update_record("employees", p_emp["id"], {
                        "permissions": {
                            "approve_leaves": p_leaves,
                            "approve_rewards": p_rewards,
                            "approve_penalties": p_penalties,
                            "approve_advances": p_adv,
                            "approve_overtime": p_ot,
                            "manage_payroll": p_pay,
                            "view_all_reports": p_rep,
                            "manage_settings": eff.get("manage_settings", False),
                            "is_monitor_only": p_mon,
                        }
                    })
                    write_audit_log(
                        "employees",
                        "update_permissions",
                        "صلاحيات",
                        f"تحديث صلاحيات {p_emp.get('name_ar')}",
                        by_name=current_user.get("name_ar", ""),
                        by_user_id=user_id,
                    )
                    st.success("✅ تم تحديث الصلاحيات بنجاح.")
                    st.rerun()

        st.divider()
        st.markdown("#### 💎 إدارة أنواع المكافآت الثابتة (إضافة بنود جديدة للمكافآت الثابتة)")
        st.caption("البنود الحالية المتاحة في خانة المكافآت الثابتة: " + " ، ".join(f"`{t}`" for t in fa_types_list))
        with st.form("add_fixed_allowance_type_form", clear_on_submit=True):
            fa_c1, fa_c2 = st.columns([3, 1.2])
            with fa_c1:
                new_fa_name = st.text_input("اسم بند مكافأة ثابتة جديد", placeholder="مثال: تشجيع أو استكمال دراسة")
            with fa_c2:
                st.write("")
                st.write("")
                add_fa_type_btn = st.form_submit_button("➕ إضافة بند", type="primary")

            if add_fa_type_btn:
                clean_fa = new_fa_name.strip()
                if clean_fa and clean_fa not in fa_types_list:
                    updated_fa_types = fa_types_list + [clean_fa]
                    _save_org_settings_patch({"fixed_allowance_types": updated_fa_types})
                    st.success(f"✅ تمت إضافة بند المكافأة الثابتة ({clean_fa}).")
                    st.rerun()

        st.divider()
        st.markdown("#### 🎉 الإجازات الرسمية المصرية والمرتبات (من تاريخ إلى تاريخ)")
        if perms.get("manage_settings") or perms.get("manage_payroll") or user_role in ("general_manager", "hr"):
            picked_eg_holiday = st.selectbox(
                "اختر إجازة رسمية من إجازات مصر (أو إجازة مخصصة)",
                EGYPT_OFFICIAL_HOLIDAYS,
                key="eg_holiday_preset_sel",
            )
            default_h_name = "" if picked_eg_holiday == "إجازة رسمية أخرى (مخصصة)" else picked_eg_holiday

            h_d1, h_d2, h_d3 = st.columns(3)
            with h_d1:
                h_start = st.date_input("من تاريخ", value=date.today(), key="hol_from_dt_inp")
            with h_d2:
                h_end = st.date_input("إلى تاريخ", value=date.today(), key="hol_to_dt_inp")
            hol_days_cnt = int((h_end - h_start).days + 1) if (h_start and h_end and h_end >= h_start) else 0
            with h_d3:
                st.text_input("عدد الأيام", value=str(hol_days_cnt), disabled=True, key=f"hol_days_calc_{hol_days_cnt}")

            with st.form("add_holiday_form", clear_on_submit=True):
                c1, c2 = st.columns([2.5, 1.5])
                with c1:
                    h_name = st.text_input("اسم الإجازة الرسمية", value=default_h_name, placeholder="اكتب اسم الإجازة")
                with c2:
                    add_day = st.checkbox("تُضاف للمرتبات (+يوم لكل يوم)", value=False)

                if st.form_submit_button("إضافة إجازة رسمية", type="primary"):
                    final_h_name = h_name.strip() or ("" if picked_eg_holiday == "إجازة رسمية أخرى (مخصصة)" else picked_eg_holiday)
                    if not final_h_name:
                        st.error("يرجى إدخال اسم الإجازة الرسمية.")
                    elif hol_days_cnt <= 0:
                        st.error("تاريخ النهاية يجب أن يكون مساوياً أو بعد تاريخ البداية.")
                    else:
                        insert_record("official_holidays", {
                            "holiday_date": h_start.isoformat(),
                            "start_date": h_start.isoformat(),
                            "end_date": h_end.isoformat(),
                            "days_count": int(hol_days_cnt),
                            "holiday_year": str(h_start.year),
                            "name_ar": final_h_name,
                            "add_day_to_payroll": bool(add_day),
                            "active": True,
                        })
                        st.success(f"✅ تمت إضافة ({final_h_name}) لمدة {hol_days_cnt} يوم.")
                        st.rerun()

        if holidays_list:
            for hol in holidays_list:
                s_p, e_p, d_cnt = _get_holiday_period_bounds(hol)
                date_rng_txt = (
                    f"من `{s_p.isoformat()}` إلى `{e_p.isoformat()}` (**{d_cnt} يوم**)"
                    if (s_p and e_p and s_p != e_p)
                    else f"`{str(hol.get('holiday_date') or s_p or '')[:10]}` (**{d_cnt} يوم**)"
                )
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3.5, 1.8, 1])
                    c1.write(f"**{hol.get('name_ar')}** — {date_rng_txt}")
                    flag_val = bool(hol.get("add_day_to_payroll", False))
                    if perms.get("manage_settings") or perms.get("manage_payroll") or user_role in ("general_manager", "hr"):
                        new_flag = c2.checkbox("إضافة يوم للمرتبات", value=flag_val, key=f"hol_chk_{hol['id']}")
                        if new_flag != flag_val:
                            update_record("official_holidays", hol["id"], {"add_day_to_payroll": new_flag})
                            st.rerun()
                        if c3.button("حذف", key=f"hol_del_{hol['id']}"):
                            delete_record("official_holidays", hol["id"])
                            st.rerun()
                    else:
                        c2.write("تُضاف للمرتبات: نعم" if flag_val else "تُضاف للمرتبات: لا")

        st.divider()
        st.markdown("#### إعدادات النظام العامة")
        with st.form("org_settings_form"):
            oc1, oc2, oc3 = st.columns(3)
            org_n = oc1.text_input("اسم المؤسسة", value=org_settings.get("org_name") or "معامل ا.د أسامة عبد المتعال منصور")
            adm_y = oc2.text_input("السنة الإدارية", value=str(org_settings.get("admin_year") or "2026"))
            def_a = oc3.number_input("الرصيد السنوي/الاعتيادي الافتراضي", min_value=1, max_value=90, value=int(org_settings.get("default_annual") or 15))

            oc4, oc5, oc6 = st.columns(3)
            def_c = oc4.number_input("رصيد العارضة الافتراضي", min_value=0, max_value=30, value=int(org_settings.get("default_casual") or 6))
            g_late = oc5.number_input("سماح الحضور (دقائق)", min_value=0, max_value=60, value=int(org_settings.get("grace_late_minutes") or 15))
            g_early = oc6.number_input("سماح الانصراف (دقائق)", min_value=0, max_value=60, value=int(org_settings.get("grace_early_minutes") or 5))

            if st.form_submit_button("حفظ الإعدادات", type="primary"):
                _save_org_settings_patch({
                    "org_name": org_n.strip(),
                    "admin_year": adm_y.strip(),
                    "default_annual": int(def_a),
                    "default_casual": int(def_c),
                    "grace_late_minutes": int(g_late),
                    "grace_early_minutes": int(g_early),
                })
                st.success("✅ تم حفظ إعدادات النظام.")
                st.rerun()

    # --- تبويب KPIs داخل الصلاحيات والإعدادات ---
    with tab_kpi_cfg:
        st.markdown("#### ⚙️ إعدادات الـ KPIs")

        cur_cycle_val = str(st.session_state.get("org_cache_kpi_cycle_type") or org_settings.get("kpi_cycle_type") or "نصف سنوي")
        cur_start_dt = parse_date_safe(st.session_state.get("org_cache_kpi_start_date") or org_settings.get("kpi_start_date")) or date.today()
        cur_active_val = bool(st.session_state.get("org_cache_kpi_evaluation_active", org_settings.get("kpi_evaluation_active", False)))

        kc1, kc2, kc3 = st.columns([2, 2, 1.5])
        with kc1:
            sel_cycle = st.selectbox(
                "نوع التقييم",
                ["نصف سنوي", "سنوي"],
                index=0 if cur_cycle_val != "سنوي" else 1,
                key="kpi_cfg_cycle_sel",
            )
        with kc2:
            sel_kpi_start = st.date_input("تاريخ بداية التقييم", value=cur_start_dt, key="kpi_cfg_start_dt")
        with kc3:
            st.write("")
            st.write("")
            sel_kpi_active = st.checkbox("تفعيل التقييم", value=cur_active_val, key="kpi_cfg_active_chk")

        if st.button("💾 حفظ إعدادات فترة التقييم", type="primary", key="save_kpi_cfg_period_btn"):
            _save_org_settings_patch({
                "kpi_cycle_type": sel_cycle,
                "kpi_period_label": f"{sel_cycle} ({sel_kpi_start.isoformat()})",
                "kpi_start_date": sel_kpi_start.isoformat(),
                "kpi_evaluation_active": bool(sel_kpi_active),
            })
            st.success("✅ تم حفظ إعدادات فترة التقييم.")
            st.rerun()

        st.divider()
        st.markdown("#### 📝 أسئلة التقييم")

        self_q_all = _get_all_self_questions(org_settings)
        peer_q_all = _get_all_peer_questions(org_settings)
        cfg_tabs = st.tabs(KPI_CONFIG_TABS)

        for idx_t, t_name in enumerate(KPI_CONFIG_TABS):
            with cfg_tabs[idx_t]:
                if t_name == "مدير عام":
                    st.info("المدير العام ليس له أسئلة تقييم ذاتي لأنه لا يقيّم نفسه، ويتم تقييمه من موظفي الاستقبال واستقبال وسحب العينات.")
                    _render_q_editor_block(
                        section_title="الأسئلة الخاصة بتقييم الموظفين للمدير العام",
                        q_list=list(peer_q_all.get("gm") or []),
                        form_key="add_gm_peer_q_form_tab",
                        del_prefix="del_gm_peer_q_tab",
                        on_change_callback=lambda upd, p_all=peer_q_all: _save_org_settings_patch(
                            {"kpi_peer_questions_v2": {**p_all, "gm": upd}}
                        ),
                    )
                elif t_name == "مدير مباشر":
                    _render_q_editor_block(
                        section_title=f"أسئلة تقييم ({t_name}) لنفسه",
                        q_list=list(self_q_all.get(t_name) or []),
                        form_key=f"add_self_q_form_{t_name}",
                        del_prefix=f"del_self_q_{t_name}",
                        on_change_callback=lambda upd, s_all=self_q_all, tk=t_name: _save_org_settings_patch(
                            {"kpi_self_questions": {**s_all, tk: upd}}
                        ),
                    )
                    st.divider()
                    _render_q_editor_block(
                        section_title="الأسئلة الخاصة بتقييم الموظفين للمدير العام",
                        q_list=list(peer_q_all.get("gm") or []),
                        form_key="add_gm_peer_q_form_under_mgr",
                        del_prefix="del_gm_peer_q_under_mgr",
                        on_change_callback=lambda upd, p_all=peer_q_all: _save_org_settings_patch(
                            {"kpi_peer_questions_v2": {**p_all, "gm": upd}}
                        ),
                    )
                else:
                    _render_q_editor_block(
                        section_title=f"أسئلة تقييم ({t_name}) لنفسه",
                        q_list=list(self_q_all.get(t_name) or []),
                        form_key=f"add_self_q_form_{t_name}",
                        del_prefix=f"del_self_q_{t_name}",
                        on_change_callback=lambda upd, s_all=self_q_all, tk=t_name: _save_org_settings_patch(
                            {"kpi_self_questions": {**s_all, tk: upd}}
                        ),
                    )
                    st.divider()
                    _render_q_editor_block(
                        section_title=f"الأسئلة الخاصة بتقييم الموظفين في ({t_name}) للسينيور",
                        q_list=list(peer_q_all.get("senior") or []),
                        form_key=f"add_sen_peer_q_form_{t_name}",
                        del_prefix=f"del_sen_peer_q_{t_name}",
                        on_change_callback=lambda upd, p_all=peer_q_all: _save_org_settings_patch(
                            {"kpi_peer_questions_v2": {**p_all, "senior": upd}}
                        ),
                    )
                    st.divider()
                    _render_q_editor_block(
                        section_title=f"الأسئلة الخاصة بتقييم الموظفين في ({t_name}) للمدير المباشر",
                        q_list=list(peer_q_all.get("manager") or []),
                        form_key=f"add_mgr_peer_q_form_{t_name}",
                        del_prefix=f"del_mgr_peer_q_{t_name}",
                        on_change_callback=lambda upd, p_all=peer_q_all: _save_org_settings_patch(
                            {"kpi_peer_questions_v2": {**p_all, "manager": upd}}
                        ),
                    )
                    st.divider()
                    _render_q_editor_block(
                        section_title=f"الأسئلة الخاصة بتقييم الموظفين في ({t_name}) للمدير العام",
                        q_list=list(peer_q_all.get("gm") or []),
                        form_key=f"add_gm_peer_q_form_{t_name}",
                        del_prefix=f"del_gm_peer_q_{t_name}",
                        on_change_callback=lambda upd, p_all=peer_q_all: _save_org_settings_patch(
                            {"kpi_peer_questions_v2": {**p_all, "gm": upd}}
                        ),
                    )
