from datetime import date, datetime
from typing import Any, Dict, List
import io
import pandas as pd
import streamlit as st

from db import (
    delete_record,
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


def _dept_map(departments: List[Dict[str, Any]]) -> Dict[str, str]:
    return {str(d["id"]): d.get("name", "") for d in departments if d.get("id")}


def _shift_map(shifts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(s["id"]): s for s in shifts if s.get("id")}


def _emp_identifiers(emp: Dict[str, Any]) -> set:
    ids = set()
    if emp.get("id"):
        ids.add(str(emp["id"]))
    if emp.get("team_user_id"):
        ids.add(str(emp["team_user_id"]))
    return ids


def _sync_leave_days_state() -> None:
    """تحديث عدد أيام الإجازة في session_state فوراً عند تغيير تاريخ البداية أو النهاية."""
    s = parse_date_safe(st.session_state.get("lr_start_date_input"))
    e = parse_date_safe(st.session_state.get("lr_end_date_input"))
    if not s or not e or e < s:
        st.session_state["calculated_leave_days"] = 0
    else:
        st.session_state["calculated_leave_days"] = int((e - s).days + 1)


# =====================================================================
# تجميع كافة الموافقات المعلقة في مكان واحد (إجازات + جزاءات/مكافآت + سلف + إضافي)
# =====================================================================
def get_pending_items_bundle(
    current_user: Dict[str, Any],
    leave_requests: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    perms = get_effective_permissions(current_user)
    if perms.get("is_monitor_only"):
        return {"leaves": [], "pr": [], "advances": [], "overtime": []}

    user_ids = _emp_identifiers(current_user)
    user_role = (current_user.get("role") or "employee").lower()

    pending_leaves: List[Dict[str, Any]] = []
    if perms.get("approve_leaves"):
        for lr in leave_requests:
            st_val = (lr.get("status") or "").lower()
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
    if perms.get("approve_advances"):
        for adv in advances:
            st_val = (adv.get("status") or "").lower()
            if user_role == "general_manager" and st_val in ("pending", "manager_approved"):
                pending_adv.append(adv)
            elif user_role in ("manager", "hr") and st_val == "pending":
                pending_adv.append(adv)

    pending_ot: List[Dict[str, Any]] = []
    if perms.get("approve_overtime"):
        for att in attendance_logs:
            if int(att.get("overtime_minutes") or 0) <= 0:
                continue
            st_ot = (att.get("overtime_status") or "").lower()
            if user_role == "general_manager" and st_ot in ("pending_manager", "pending_gm"):
                pending_ot.append(att)
            elif user_role == "manager" and st_ot == "pending_manager":
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
) -> int:
    """إجمالي عدد الإشعارات المعلقة على زر الموافقات الرئيسي."""
    bundle = get_pending_items_bundle(current_user, leave_requests, penalties_rewards, advances, attendance_logs)
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

        if (
            req_emp_id in subordinate_ids
            or (req_user_id and req_user_id in subordinate_ids)
            or (req_senior_id and req_senior_id in my_ids)
            or (req_mgr_id and req_mgr_id in my_ids)
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
    shift_by_id = _shift_map(shifts)
    today = date.today()
    today_iso = today.isoformat()
    current_ym = today.strftime("%Y-%m")

    on_leave_today = []
    on_leave_ids_today = set()
    for lr in leave_requests:
        if (lr.get("status") or "").lower() != "approved":
            continue
        s_dt = str(lr.get("start_date") or "")[:10]
        e_dt = str(lr.get("end_date") or "")[:10]
        if s_dt <= today_iso <= e_dt:
            on_leave_today.append(lr)
            if lr.get("employee_id"):
                on_leave_ids_today.add(str(lr["employee_id"]))

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

    st.markdown("#### 🕒 متابعة الورديات (صباحي / مسائي) وكشف العجز (Drop)")
    if shifts:
        shift_rows = []
        for s in shifts:
            if not s.get("active", True):
                continue
            sid = str(s["id"])
            did = str(s.get("department_id") or "")
            assigned_emps = [e for e in active_emps if str(e.get("shift_id") or "") == sid]
            absent_on_leave = [e for e in assigned_emps if str(e.get("id")) in on_leave_ids_today]
            present_count = max(0, len(assigned_emps) - len(absent_on_leave))
            target_cnt = int(s.get("target_headcount") or 100)
            drop_val = target_cnt - present_count
            stype_label = (
                "صباحي"
                if (s.get("shift_type") == "morning")
                else ("مسائي" if (s.get("shift_type") == "evening") else "مخصص")
            )
            shift_rows.append({
                "الفرع / القسم": dept_names.get(did, "عام"),
                "الوردية": s.get("name", ""),
                "النوع": stype_label,
                "المستهدف": target_cnt,
                "المسجلون": len(assigned_emps),
                "إجازة اليوم": len(absent_on_leave),
                "المتواجدون فعلياً": present_count,
                "حالة العجز (Drop)": f"⚠️ عجز {drop_val}" if drop_val > 0 else "✅ مكتمل",
            })
        st.dataframe(shift_rows, use_container_width=True, hide_index=True)
    else:
        st.info("لم يتم إضافة ورديات بعد. يمكنك إضافة الورديات الصباحية والمسائية من تبويب (الموظفون والفروع والصلاحيات).")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("#### 🌴 الموظفون في إجازة اليوم ومواقعهم")
        if on_leave_today:
            leave_today_table = []
            for lr in on_leave_today:
                emp_obj = emp_by_id.get(str(lr.get("employee_id") or ""), {})
                sh_obj = shift_by_id.get(str(emp_obj.get("shift_id") or lr.get("shift_id") or ""), {})
                leave_today_table.append({
                    "الموظف": lr.get("employee_name") or emp_obj.get("name_ar", ""),
                    "نوع الإجازة": lr.get("type_name") or lr.get("type_code", ""),
                    "الفرع": dept_names.get(str(lr.get("department_id") or emp_obj.get("department_id") or ""), "—"),
                    "الوردية": sh_obj.get("name") or "—",
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
# 2. الشاشة الموحدة الوحيدة للموافقات (إجازات + جزاءات/مكافآت + سلف + إضافي)
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

    bundle = get_pending_items_bundle(current_user, leave_requests, penalties_rewards, advances, attendance_logs)
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
                st.write(
                    f"**{lr.get('employee_name')}** — {lr.get('type_name')} | "
                    f"من `{str(lr.get('start_date'))[:10]}` إلى `{str(lr.get('end_date'))[:10]}` "
                    f"(**{lr.get('days')} يوم**){backup_txt} | الحالة: `{st_val}`"
                )
                if lr.get("reason"):
                    st.caption(f"السبب: {lr.get('reason')}")
                if lr.get("attachment_url"):
                    st.markdown(f"[📎 عرض التقرير المرفق]({lr.get('attachment_url')})")

                note_inp = st.text_input("ملاحظة الاعتماد/الرفض", key=f"unif_note_lr_{lr['id']}")
                col_a, col_r, _ = st.columns([1.2, 1.2, 4])

                with col_a:
                    if st.button("✅ موافقة", key=f"unif_app_lr_{lr['id']}", type="primary"):
                        approvals = list(lr.get("approvals") or [])
                        if st_val == "pending_senior":
                            stage = "senior"
                            next_status = "pending_gm" if not lr.get("direct_manager_id") else "pending_manager"
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
                        stage = "senior" if st_val == "pending_senior" else ("manager" if st_val == "pending_manager" else "gm")
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
                st.write(
                    f"**{emp_o.get('name_ar', '')}** — الإجمالي: `{adv.get('total_amount')} ج` | "
                    f"النظام: `{adv.get('repayment_mode')}` ({adv.get('installments_count')} شهر، قسط `{adv.get('monthly_installment')} ج`) | "
                    f"الحالة: `{st_val}`"
                )
                if adv.get("reason"):
                    st.caption(f"السبب: {adv.get('reason')}")
                ca, cr, _ = st.columns([1.4, 1.2, 4.4])
                with ca:
                    if st_val == "pending" and user_role in ("manager", "hr"):
                        if st.button("✅ موافقة إدارية", key=f"unif_mgr_adv_{adv['id']}", type="primary"):
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
        st.markdown("#### 🕒 موافقات الوقت الإضافي")
        for rec in bundle["overtime"]:
            emp_o = emp_by_id.get(str(rec.get("employee_id") or ""), {})
            st_ot = (rec.get("overtime_status") or "").lower()
            with st.container(border=True):
                st.write(
                    f"**{emp_o.get('name_ar', '')}** — التاريخ: `{str(rec.get('work_date'))[:10]}` | "
                    f"الانصراف: `{rec.get('check_out')}` (نهاية الدوام: `{rec.get('scheduled_end')}`) | "
                    f"الوقت الإضافي: **`{rec.get('overtime_minutes')} دقيقة`** | الحالة: `{st_ot}`"
                )
                ca, cr, _ = st.columns([1.4, 1.2, 4.4])
                with ca:
                    if st_ot == "pending_manager" and user_role == "manager":
                        if st.button("✅ موافقة مدير مباشر", key=f"unif_ot_mgr_{rec['id']}", type="primary"):
                            update_record("attendance_logs", rec["id"], {
                                "overtime_status": "pending_gm",
                                "overtime_manager_by": user_id,
                            })
                            st.rerun()
                    elif user_role == "general_manager":
                        if st.button("✅ اعتماد مدير عام", key=f"unif_ot_gm_{rec['id']}", type="primary"):
                            update_record("attendance_logs", rec["id"], {
                                "overtime_status": "approved",
                                "overtime_gm_by": user_id,
                            })
                            st.rerun()
                with cr:
                    if st.button("❌ رفض الإضافي", key=f"unif_ot_rej_{rec['id']}"):
                        update_record("attendance_logs", rec["id"], {"overtime_status": "rejected"})
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
            days_val = float(days_int)

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
                        route = determine_initial_approval_route(current_user, employees)
                        payload = {
                            "employee_id": current_user.get("id"),
                            "employee_user_id": current_user.get("team_user_id") or current_user.get("id"),
                            "employee_name": current_user.get("name_ar"),
                            "department_id": current_user.get("department_id"),
                            "shift_id": current_user.get("shift_id"),
                            "backup_employee_id": chosen_backup.get("id") if chosen_backup else None,
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
        r1.metric("الاعتيادي / السنوي المستحق", round_half_up_2(ann_total))
        r2.metric("المستخدم من الاعتيادي / السنوي", round_half_up_2(ann_used))
        r3.metric("المتبقي من الاعتيادي / السنوي", round_half_up_2(annual_rem))

        c_r1, c_r2, c_r3 = st.columns(3)
        c_r1.metric("العارضة المستحق", round_half_up_2(cas_total))
        c_r2.metric("المستخدم من العارضة", round_half_up_2(cas_used))
        c_r3.metric("المتبقي من العارضة", round_half_up_2(casual_rem))

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
                ann_t = float(b.get("annual") or 0)
                ann_u = float(b.get("used_annual") or 0)
                cas_t = float(b.get("casual") or 0)
                cas_u = float(b.get("used_casual") or 0)
                bal_rows.append({
                    "الرقم الوظيفي": emp.get("employee_no"),
                    "الاسم": emp.get("name_ar"),
                    "تاريخ التعيين": str(emp.get("hire_date") or "")[:10],
                    "اعتيادي/سنوي مستحق": ann_t,
                    "مستخدم": ann_u,
                    "متبقي": round_half_up_2(ann_t - ann_u),
                    "عارضة مستحق": cas_t,
                    "مستخدم عارضة": cas_u,
                    "متبقي عارضة": round_half_up_2(cas_t - cas_u),
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
    user_role = (current_user.get("role") or "employee").lower()
    emp_by_id = {str(e["id"]): e for e in employees if e.get("id")}
    emp_options = {
        f"{e.get('name_ar')} (#{e.get('employee_no')})": e
        for e in employees
        if (e.get("status") or "active") == "active"
    }

    tab_pr, tab_adv, tab_hr_settle = st.tabs([
        "⚖️ الجزاءات والمكافآت",
        "💳 طلبات السلف والتقسيط",
        "🏦 تجميع وسداد السلف (HR)",
    ])

    with tab_pr:
        can_add_pr = perms.get("approve_penalties") or perms.get("approve_rewards")
        if can_add_pr and emp_options:
            st.markdown("#### إضافة جزاء أو مكافأة جديدة")
            with st.form("add_pr_form", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    sel_emp_lbl = st.selectbox("الموظف", list(emp_options.keys()))
                with c2:
                    entry_type = st.selectbox("النوع", ["penalty", "reward"], format_func=lambda x: "جزاء" if x == "penalty" else "مكافأة")
                with c3:
                    calc_mode = st.selectbox("طريقة الحساب", ["amount", "days"], format_func=lambda x: "مبلغ مالي (جنيه)" if x == "amount" else "بعدد الأيام")

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

        st.markdown("#### سجل الجزاءات والمكافآت")
        if penalties_rewards:
            st.dataframe(
                [
                    {
                        "النوع": "جزاء" if pr.get("entry_type") == "penalty" else "مكافأة",
                        "الموظف": emp_by_id.get(str(pr.get("employee_id") or ""), {}).get("name_ar", ""),
                        "القيمة": pr.get("value"),
                        "طريقة الحساب": "أيام" if pr.get("calc_mode") == "days" else "مبلغ مالي",
                        "الإجمالي (ج)": pr.get("calculated_amount"),
                        "الشهر": pr.get("payroll_month"),
                        "العنوان": pr.get("title") or "—",
                        "السبب": pr.get("reason") or "—",
                        "الحالة": pr.get("status"),
                    }
                    for pr in penalties_rewards
                ],
                use_container_width=True,
                hide_index=True,
            )

    with tab_adv:
        st.markdown("#### تقديم طلب سلفة (في نفس الشهر أو مقسطة بدون كسور)")
        with st.form("add_advance_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                tot_amt = st.number_input("إجمالي السلفة (جنيه)", min_value=50.0, value=1000.0, step=50.0)
            with c2:
                rep_mode = st.selectbox(
                    "نظام السداد",
                    ["single_month", "installments_count", "fixed_installment"],
                    format_func=lambda x: {
                        "single_month": "خصم كامل في نفس الشهر",
                        "installments_count": "تقسيط على عدد شهور",
                        "fixed_installment": "قسط شهري ثابت وتصفية الباقي في النهاية",
                    }[x],
                )
            with c3:
                start_ym = st.text_input("شهر بداية الخصم (YYYY-MM)", value=date.today().strftime("%Y-%m"))

            c4, c5 = st.columns(2)
            with c4:
                inst_cnt = st.number_input("عدد الشهور (للتقسيط بعدد الشهور)", min_value=1, max_value=36, value=1, step=1)
            with c5:
                fixed_step = st.number_input("قيمة القسط الشهري الثابت (جنيه)", min_value=0.0, value=0.0, step=100.0)

            adv_reason = st.text_input("سبب السلفة (اختياري)")

            sched = calculate_advance_schedule(tot_amt, rep_mode, int(inst_cnt), float(fixed_step))
            st.info(
                f"معاينة السداد: {sched['installments_count']} قسط — "
                f"القسط الشهري: **{sched['monthly_installment']} ج** — "
                f"القسط الأخير: **{sched['last_installment_amount']} ج**"
            )

            if st.form_submit_button("إرسال طلب السلفة", type="primary"):
                insert_record("advances", {
                    "employee_id": current_user.get("id"),
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
                    f"طلب سلفة بقيمة {tot_amt} ج للموظف {current_user.get('name_ar')}",
                    by_name=current_user.get("name_ar", ""),
                    by_user_id=user_id,
                )
                st.success("✅ تم إرسال طلب السلفة إلى صندوق الموافقات الموحّد.")
                st.rerun()

        st.markdown("#### سجل السلف وحالاتها")
        if advances:
            st.dataframe(
                [
                    {
                        "الموظف": emp_by_id.get(str(adv.get("employee_id") or ""), {}).get("name_ar", ""),
                        "الإجمالي (ج)": adv.get("total_amount"),
                        "المتبقي (ج)": adv.get("remaining_amount"),
                        "النظام": adv.get("repayment_mode"),
                        "عدد الأقساط": adv.get("installments_count"),
                        "القسط الشهري": adv.get("monthly_installment"),
                        "بداية الخصم": adv.get("start_payroll_month"),
                        "الحالة": adv.get("status"),
                    }
                    for adv in advances
                ],
                use_container_width=True,
                hide_index=True,
            )

    with tab_hr_settle:
        st.markdown("#### تجميع سلف الموظف المستحقة للشهر وتأكيد السداد")
        settle_month = st.text_input("الشهر المستهدف للتجميع والسداد (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="hr_adv_ym")
        can_settle = perms.get("manage_payroll") or user_role in ("hr", "general_manager")

        rows_summary = []
        for emp in employees:
            summ = get_employee_monthly_advances_summary(str(emp["id"]), settle_month, advances)
            if summ["total_due"] > 0:
                rows_summary.append({"emp": emp, "summ": summ})

        if not rows_summary:
            st.info(f"لا توجد سلف مستحقة مجمّعة لشهر {settle_month}.")
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
# 5. البصمة والإجازات الرسمية والـ Payroll
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
) -> None:
    st.markdown("### ⏱️ البصمة والإجازات الرسمية والـ Payroll")
    perms = get_effective_permissions(current_user)
    user_id = str(current_user.get("id"))
    user_role = (current_user.get("role") or "employee").lower()

    emp_by_no = {str(e.get("employee_no") or "").strip(): e for e in employees if e.get("employee_no")}
    emp_by_id = {str(e["id"]): e for e in employees if e.get("id")}
    shift_by_id = _shift_map(shifts)
    grace_late = int(org_settings.get("grace_late_minutes") or 15)
    grace_early = int(org_settings.get("grace_early_minutes") or 5)

    tab_att, tab_ot_view, tab_hol, tab_pay = st.tabs([
        "📥 رفع البصمة (Excel / CSV)",
        "🕒 سجل الوقت الإضافي",
        "🎉 الإجازات الرسمية والمرتبات",
        "💵 شيت المرتب الفعلي والأرشيف",
    ])

    with tab_att:
        st.caption(
            f"الأعمدة المطلوبة في ملف البصمة: `employee_no`، `work_date` (YYYY-MM-DD)، `check_in` (HH:MM)، `check_out` (HH:MM) — "
            f"سماح الحضور: {grace_late} دقيقة | سماح الانصراف: {grace_early} دقيقة."
        )
        att_file = st.file_uploader("رفع ملف البصمة", type=["xlsx", "xls", "csv"])
        if att_file is not None and (perms.get("manage_payroll") or user_role in ("hr", "general_manager")):
            if att_file.name.lower().endswith(".csv"):
                df_att = pd.read_csv(att_file)
            else:
                df_att = pd.read_excel(att_file)

            st.dataframe(df_att.head(20), use_container_width=True, hide_index=True)

            if st.button("معالجة وحفظ سجلات البصمة في قاعدة البيانات", type="primary"):
                saved_cnt = 0
                for _, r in df_att.iterrows():
                    eno = str(r.get("employee_no") or "").strip()
                    emp = emp_by_no.get(eno)
                    if not emp:
                        continue
                    w_dt = parse_date_safe(r.get("work_date"))
                    if not w_dt:
                        continue
                    sh = shift_by_id.get(str(emp.get("shift_id") or ""))
                    s_start = (sh or {}).get("start_time") or "09:00:00"
                    s_end = (sh or {}).get("end_time") or "17:00:00"
                    cin_str = str(r.get("check_in") or "").strip()
                    cout_str = str(r.get("check_out") or "").strip()

                    eval_res = evaluate_attendance_record(
                        cin_str,
                        cout_str,
                        s_start,
                        s_end,
                        grace_late_minutes=grace_late,
                        grace_early_minutes=grace_early,
                        is_leave_day=False,
                    )
                    upsert_record(
                        "attendance_logs",
                        {
                            "employee_id": emp["id"],
                            "work_date": w_dt.isoformat(),
                            "check_in": cin_str or None,
                            "check_out": cout_str or None,
                            "scheduled_start": str(s_start),
                            "scheduled_end": str(s_end),
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

    with tab_ot_view:
        st.markdown("#### سجل الوقت الإضافي (الاعتماد يتم من زر الموافقات الرئيسي)")
        ot_records = [a for a in attendance_logs if int(a.get("overtime_minutes") or 0) > 0]
        if ot_records:
            st.dataframe(
                [
                    {
                        "الموظف": emp_by_id.get(str(rec.get("employee_id") or ""), {}).get("name_ar", ""),
                        "التاريخ": str(rec.get("work_date") or "")[:10],
                        "الانصراف": rec.get("check_out"),
                        "نهاية الدوام": rec.get("scheduled_end"),
                        "الوقت الإضافي (دقيقة)": rec.get("overtime_minutes"),
                        "حالة الاعتماد": rec.get("overtime_status"),
                    }
                    for rec in ot_records
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("لا توجد سجلات وقت إضافي حتى الآن.")

    with tab_hol:
        st.markdown("#### الإجازات الرسمية السنوية وتأثيرها على المرتبات (إضافة يوم لكل إجازة مفعّلة)")
        if perms.get("manage_settings") or perms.get("manage_payroll"):
            with st.form("add_holiday_form", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    h_name = st.text_input("اسم الإجازة الرسمية", placeholder="مثال: عيد الفطر المبارك")
                with c2:
                    h_date = st.date_input("تاريخ الإجازة", value=date.today())
                with c3:
                    add_day = st.checkbox("تُضاف للمرتبات (يوم إضافي لكل العاملين)", value=True)
                if st.form_submit_button("إضافة إجازة رسمية", type="primary"):
                    if h_name.strip():
                        insert_record("official_holidays", {
                            "holiday_date": h_date.isoformat(),
                            "holiday_year": str(h_date.year),
                            "name_ar": h_name.strip(),
                            "add_day_to_payroll": bool(add_day),
                            "active": True,
                        })
                        st.success("✅ تمت إضافة الإجازة الرسمية.")
                        st.rerun()

        if official_holidays:
            for hol in official_holidays:
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 1])
                    c1.write(f"**{hol.get('name_ar')}** — `{str(hol.get('holiday_date'))[:10]}`")
                    flag_val = bool(hol.get("add_day_to_payroll", True))
                    if perms.get("manage_settings") or perms.get("manage_payroll"):
                        new_flag = c2.checkbox("إضافة يوم للمرتبات", value=flag_val, key=f"hol_chk_{hol['id']}")
                        if new_flag != flag_val:
                            update_record("official_holidays", hol["id"], {"add_day_to_payroll": new_flag})
                            st.rerun()
                        if c3.button("حذف", key=f"hol_del_{hol['id']}"):
                            delete_record("official_holidays", hol["id"])
                            st.rerun()
                    else:
                        c2.write("تُضاف للمرتبات: نعم" if flag_val else "تُضاف للمرتبات: لا")

    with tab_pay:
        st.markdown("#### شيت المرتب الشهري الفعلي والأرشيف")
        pay_ym = st.text_input("شهر المرتب (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="pay_sheet_ym")

        computed_list = []
        for emp in employees:
            if (emp.get("status") or "active") != "active":
                continue
            sh = shift_by_id.get(str(emp.get("shift_id") or ""))
            res = compute_employee_monthly_payroll(
                employee=emp,
                payroll_month=pay_ym,
                shift=sh,
                attendance_logs=attendance_logs,
                penalties_rewards=penalties_rewards,
                advances=advances,
                official_holidays=official_holidays,
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
                    "المكافآت": r["rewards_total"],
                    "وقت إضافي معتمد": r["overtime_pay"],
                    "خصم السلف": r["advances_deduction"],
                    "خصم الجزاءات": r["penalties_deduction"],
                    "خصم التأخير/الانصراف": r["late_deduction"],
                    "المرتب الفعلي": r["net_salary"],
                }
                for r in computed_list
            ]
            st.dataframe(sheet_rows, use_container_width=True, hide_index=True)

            excel_buf = io.BytesIO()
            with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
                pd.DataFrame(sheet_rows).to_excel(writer, index=False, sheet_name="Payroll")
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
                        "إضافي": p.get("overtime_pay"),
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
# 6. التقييم (KPIs نصف سنوي وسنوي + ملخص الأداء الآلي) والتقارير
# =====================================================================
def render_kpis_and_reports(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    leave_requests: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    kpi_evaluations: List[Dict[str, Any]],
    org_settings: Dict[str, Any],
) -> None:
    st.markdown("### 📈 تقييم الأداء (KPIs) والتقارير التفصيلية")
    perms = get_effective_permissions(current_user)
    user_id = str(current_user.get("id"))
    user_role = (current_user.get("role") or "employee").lower()
    emp_by_id = {str(e["id"]): e for e in employees if e.get("id")}

    criteria_items = [
        "الالتزام بالمواعيد والانضباط الوظيفي",
        "جودة ودقة تنفيذ المهام اليومية",
        "التعاون والعمل الجماعي داخل الفريق",
        "الالتزام بالسياسات والتعليمات الإدارية",
        "المبادرة وسرعة الإنجاز تحت الضغط",
    ]

    tab_kpi, tab_rep = st.tabs(["🎯 تقييم الـ KPIs (نصف سنوي / سنوي)", "📑 التقارير"])

    with tab_kpi:
        kpi_active = bool(org_settings.get("kpi_evaluation_active", False))
        period_lbl = str(org_settings.get("kpi_period_label") or "نصف سنوي 2026")

        if perms.get("manage_settings") or user_role == "general_manager":
            with st.expander("⚙️ التحكم في تفعيل فترة التقييم (نصف سنوي / سنوي)", expanded=True):
                c1, c2, c3 = st.columns([2, 2, 1])
                new_lbl = c1.text_input("مسمى فترة التقييم", value=period_lbl)
                new_act = c2.checkbox("تفعيل أيقونة التقييم للموظفين الآن", value=kpi_active)
                if c3.button("حفظ", key="save_kpi_toggle"):
                    update_record("org_settings", "main", {
                        "kpi_period_label": new_lbl.strip() or period_lbl,
                        "kpi_evaluation_active": bool(new_act),
                    })
                    st.rerun()

        if not kpi_active and user_role not in ("general_manager", "hr"):
            st.warning("فترة تقييم الأداء (KPIs) غير مفعّلة حالياً.")
        else:
            target_choices = {
                f"{e.get('name_ar')} (#{e.get('employee_no')})": e
                for e in employees
                if (e.get("status") or "active") == "active"
            }
            default_key = next(
                (k for k, v in target_choices.items() if str(v["id"]) == user_id),
                list(target_choices.keys())[0] if target_choices else "",
            )
            sel_target_lbl = st.selectbox(
                "اختر الموظف للتقييم",
                list(target_choices.keys()),
                index=list(target_choices.keys()).index(default_key) if default_key in target_choices else 0,
            )
            target_emp = target_choices[sel_target_lbl]

            p_col1, p_col2 = st.columns(2)
            p_start = p_col1.date_input("من تاريخ (لفترة السجل الآلي)", value=date(date.today().year, 1, 1))
            p_end = p_col2.date_input("إلى تاريخ", value=date.today())

            col_self, col_mgr = st.columns(2)
            self_scores: Dict[str, int] = {}
            mgr_scores: Dict[str, int] = {}

            with col_self:
                st.markdown("#### تقييم الموظف لنفسه (من 100%)")
                for idx, item in enumerate(criteria_items):
                    self_scores[item] = st.slider(item, min_value=0, max_value=20, value=16, key=f"self_{target_emp['id']}_{idx}")
                self_pct = round_half_up_2(sum(self_scores.values()))
                st.metric("نسبة التقييم الذاتي", f"{self_pct}%")

            with col_mgr:
                st.markdown("#### تقييم المدير المباشر (من 100%)")
                can_mgr_rate = perms.get("approve_penalties") or user_role in ("manager", "general_manager", "hr")
                for idx, item in enumerate(criteria_items):
                    mgr_scores[item] = st.slider(
                        f"{item} (المدير)",
                        min_value=0,
                        max_value=20,
                        value=16,
                        disabled=not can_mgr_rate,
                        key=f"mgr_{target_emp['id']}_{idx}",
                    )
                mgr_pct = round_half_up_2(sum(mgr_scores.values()))
                st.metric("نسبة تقييم المدير", f"{mgr_pct}%")

            st.divider()
            st.markdown("#### 📋 ملف الأداء التلقائي للموظف خلال الفترة المحددة")
            auto_sum = build_employee_period_summary(
                employee=target_emp,
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

            if st.button("💾 حفظ التقييم والملخص", type="primary"):
                upsert_record(
                    "kpi_evaluations",
                    {
                        "employee_id": target_emp["id"],
                        "manager_id": user_id,
                        "period_label": f"{period_lbl} ({p_start.isoformat()} إلى {p_end.isoformat()})",
                        "period_start": p_start.isoformat(),
                        "period_end": p_end.isoformat(),
                        "self_scores": self_scores,
                        "self_percentage": self_pct,
                        "manager_scores": mgr_scores,
                        "manager_percentage": mgr_pct,
                        "auto_summary": auto_sum,
                        "status": "reviewed_by_manager" if can_mgr_rate else "submitted_by_employee",
                    },
                    on_conflict="employee_id,period_label",
                )
                write_audit_log(
                    "kpi_evaluations",
                    "save_kpi",
                    "تقييم",
                    f"حفظ تقييم KPI للموظف {target_emp.get('name_ar')} (ذاتي {self_pct}% / مدير {mgr_pct}%)",
                    by_name=current_user.get("name_ar", ""),
                    by_user_id=user_id,
                )
                st.success("✅ تم حفظ تقييم الـ KPI وملخص الأداء.")
                st.rerun()

    with tab_rep:
        rep_type = st.selectbox(
            "اختر نوع التقرير",
            ["تقرير الجزاءات الشهري", "تقرير المكافآت الشهري", "تقرير الإجازات الشهري", "تقرير شامل لموظف محدد"],
        )
        rep_ym = st.text_input("الشهر (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="rep_ym_inp")

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
                sel_e = st.selectbox("اختر الموظف", list(emp_map.keys()), key="rep_emp_sel")
                e_obj = emp_map[sel_e]
                eid_str = str(e_obj["id"])
                st.write("#### الإجازات")
                st.dataframe([lr for lr in leave_requests if str(lr.get("employee_id")) == eid_str], use_container_width=True, hide_index=True)
                st.write("#### الجزاءات والمكافآت")
                st.dataframe([pr for pr in penalties_rewards if str(pr.get("employee_id")) == eid_str], use_container_width=True, hide_index=True)
                st.write("#### السلف")
                st.dataframe([adv for adv in advances if str(adv.get("employee_id")) == eid_str], use_container_width=True, hide_index=True)


# =====================================================================
# 7. الموظفون والفروع والورديات والصلاحيات والإعدادات
# =====================================================================
def render_admin_and_staff(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
    departments: List[Dict[str, Any]],
    shifts: List[Dict[str, Any]],
    org_settings: Dict[str, Any],
) -> None:
    st.markdown("### 👥 إدارة الموظفين والفروع والورديات والصلاحيات والإعدادات")
    perms = get_effective_permissions(current_user)
    user_id = str(current_user.get("id"))
    user_role = (current_user.get("role") or "employee").lower()

    if not (perms.get("manage_settings") or perms.get("manage_payroll") or user_role in ("general_manager", "hr")):
        st.warning("هذا القسم مخصص للإدارة والموارد البشرية.")
        return

    tab_emp, tab_org_sh, tab_perms = st.tabs([
        "👤 الموظفون وتعديل البيانات",
        "🏢 الفروع والورديات (صباحي/مسائي)",
        "🔐 الصلاحيات والإعدادات",
    ])

    with tab_emp:
        st.markdown("#### إضافة موظف جديد")
        dept_choices = {d.get("name", ""): d["id"] for d in departments}
        shift_choices = {f"{s.get('name')} ({s.get('shift_type')})": s["id"] for s in shifts}
        emp_ref_choices = {"لا يوجد": None}
        for e in employees:
            emp_ref_choices[f"{e.get('name_ar')} ({e.get('role')})"] = e.get("team_user_id") or e.get("id")

        with st.form("create_emp_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                eno = st.text_input("الرقم الوظيفي", placeholder="مثال: 101")
                name_ar = st.text_input("اسم الموظف", placeholder="الاسم الرباعي")
                job_title = st.text_input("المسمى الوظيفي", placeholder="مثال: أخصائي تحاليل / استقبال")
            with c2:
                hire_dt = st.date_input("تاريخ التعيين", value=date.today())
                base_sal = st.number_input("المرتب الأساسي (جنيه)", min_value=0.0, value=5000.0, step=250.0)
                role_val = st.selectbox(
                    "الدور الوظيفي",
                    ["employee", "senior", "manager", "hr", "general_manager", "chairman", "vice_chairman"],
                )
            with c3:
                dept_lbl = st.selectbox("الفرع / القسم", ["—"] + list(dept_choices.keys()))
                sh_lbl = st.selectbox("الوردية", ["—"] + list(shift_choices.keys()))
                pin_val = st.text_input("رمز الدخول (PIN)", value="1234")

            c4, c5 = st.columns(2)
            sen_lbl = c4.selectbox("الـ Senior المسؤول عن الإجازات (إن وُجد)", list(emp_ref_choices.keys()))
            mgr_lbl = c5.selectbox("المدير المباشر / مدير الفرع", list(emp_ref_choices.keys()))

            if st.form_submit_button("إضافة الموظف", type="primary"):
                if eno.strip() and name_ar.strip():
                    insert_record("employees", {
                        "employee_no": eno.strip(),
                        "name_ar": name_ar.strip(),
                        "job_title": job_title.strip(),
                        "role": role_val,
                        "department_id": dept_choices.get(dept_lbl),
                        "shift_id": shift_choices.get(sh_lbl),
                        "senior_id": emp_ref_choices.get(sen_lbl),
                        "direct_manager_id": emp_ref_choices.get(mgr_lbl),
                        "hire_date": hire_dt.isoformat(),
                        "base_salary": float(base_sal),
                        "pin_code": pin_val.strip(),
                        "status": "active",
                        "created_by": user_id,
                    })
                    write_audit_log(
                        "employees",
                        "create_employee",
                        "إضافة",
                        f"إضافة الموظف {name_ar.strip()} ({eno.strip()})",
                        by_name=current_user.get("name_ar", ""),
                        by_user_id=user_id,
                    )
                    st.success("✅ تمت إضافة الموظف بنجاح.")
                    st.rerun()

        st.markdown("#### تعديل بيانات الموظفين (الاسم، المسمى الوظيفي، تاريخ التعيين، المرتب)")
        if employees:
            edit_map = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in employees}
            sel_edit = st.selectbox("اختر موظفاً للتعديل", list(edit_map.keys()), key="edit_emp_picker")
            target_e = edit_map[sel_edit]
            with st.form("edit_emp_form"):
                ec1, ec2, ec3 = st.columns(3)
                new_name = ec1.text_input("الاسم", value=target_e.get("name_ar") or "")
                new_title = ec2.text_input("المسمى الوظيفي", value=target_e.get("job_title") or "")
                new_hire = ec3.date_input("تاريخ التعيين", value=parse_date_safe(target_e.get("hire_date")) or date.today())

                ec4, ec5, ec6 = st.columns(3)
                new_sal = ec4.number_input("المرتب الأساسي", min_value=0.0, value=float(target_e.get("base_salary") or 0), step=250.0)
                new_status = ec5.selectbox("الحالة", ["active", "inactive"], index=0 if (target_e.get("status") or "active") == "active" else 1)
                new_sh = ec6.selectbox("الوردية", ["—"] + list(shift_choices.keys()))

                if st.form_submit_button("حفظ التعديلات", type="primary"):
                    upd_payload: Dict[str, Any] = {
                        "name_ar": new_name.strip(),
                        "job_title": new_title.strip(),
                        "hire_date": new_hire.isoformat(),
                        "base_salary": float(new_sal),
                        "status": new_status,
                    }
                    if new_sh in shift_choices:
                        upd_payload["shift_id"] = shift_choices[new_sh]
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
                    "تاريخ التعيين": str(e.get("hire_date") or "")[:10],
                    "المرتب الأساسي": e.get("base_salary"),
                    "الحالة": e.get("status"),
                }
                for e in employees
            ],
            use_container_width=True,
            hide_index=True,
        )

    with tab_org_sh:
        c_d, c_s = st.columns(2)
        with c_d:
            st.markdown("#### إضافة فرع / قسم")
            with st.form("add_dept_form", clear_on_submit=True):
                d_code = st.text_input("كود الفرع", placeholder="مثال: BR-01")
                d_name = st.text_input("اسم الفرع / القسم", placeholder="مثال: فرع إمبابة")
                if st.form_submit_button("إضافة فرع"):
                    if d_code.strip() and d_name.strip():
                        insert_record("departments", {"code": d_code.strip(), "name": d_name.strip(), "active": True})
                        st.success("✅ تمت إضافة الفرع.")
                        st.rerun()
            st.dataframe([{"الكود": d.get("code"), "الاسم": d.get("name")} for d in departments], use_container_width=True, hide_index=True)

        with c_s:
            st.markdown("#### إضافة وردية (صباحي / مسائي) وسعة الكشف")
            dept_opt = {d.get("name", ""): d["id"] for d in departments}
            with st.form("add_shift_form", clear_on_submit=True):
                s_dept = st.selectbox("الفرع", ["—"] + list(dept_opt.keys()))
                s_name = st.text_input("اسم الوردية", placeholder="مثال: شفت صباحي / شفت مسائي")
                s_type = st.selectbox("نوع الشفت", ["morning", "evening", "custom"])
                sc1, sc2 = st.columns(2)
                s_start = sc1.time_input("بداية الدوام", value=datetime.strptime("08:00", "%H:%M").time())
                s_end = sc2.time_input("نهاية الدوام", value=datetime.strptime("16:00", "%H:%M").time())
                s_target = st.number_input("العدد المستهدف في الشفت", min_value=1, max_value=1000, value=100, step=1)
                if st.form_submit_button("إضافة وردية"):
                    if s_name.strip():
                        insert_record("shifts", {
                            "department_id": dept_opt.get(s_dept),
                            "name": s_name.strip(),
                            "shift_type": s_type,
                            "start_time": s_start.strftime("%H:%M:%S"),
                            "end_time": s_end.strftime("%H:%M:%S"),
                            "target_headcount": int(s_target),
                            "active": True,
                        })
                        st.success("✅ تمت إضافة الوردية.")
                        st.rerun()
            st.dataframe(
                [
                    {
                        "الوردية": s.get("name"),
                        "النوع": s.get("shift_type"),
                        "من": str(s.get("start_time"))[:5],
                        "إلى": str(s.get("end_time"))[:5],
                        "المستهدف": s.get("target_headcount"),
                    }
                    for s in shifts
                ],
                use_container_width=True,
                hide_index=True,
            )

    with tab_perms:
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
                upsert_record("org_settings", {
                    "id": "main",
                    "org_name": org_n.strip(),
                    "admin_year": adm_y.strip(),
                    "default_annual": int(def_a),
                    "default_casual": int(def_c),
                    "grace_late_minutes": int(g_late),
                    "grace_early_minutes": int(g_early),
                }, on_conflict="id")
                st.success("✅ تم حفظ إعدادات النظام.")
                st.rerun()
