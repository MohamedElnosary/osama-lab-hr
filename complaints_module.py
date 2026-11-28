from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set
import streamlit as st

from db import fetch_table, insert_record, update_record, write_audit_log
from payroll_engine import calculate_penalty_or_reward_amount
from rules_engine import get_effective_permissions, parse_date_safe, round_half_up_2

DECISION_DAY_OPTIONS: List[float] = [
    0.25,
    0.5,
    0.75,
    1.0,
    1.5,
    2.0,
    2.5,
    3.0,
    3.5,
    4.0,
    4.5,
    5.0,
    5.5,
    6.0,
    6.5,
    7.0,
    7.5,
    8.0,
    8.5,
    9.0,
    9.5,
    10.0,
]

LEADERSHIP_RANK_ORDER: List[str] = [
    "chairman",
    "vice_chairman",
    "general_manager",
    "manager",
    "senior",
]


def _apply_complaints_rtl_styles() -> None:
    st.markdown(
        """
        <style>
            .stApp, .main, [data-testid="stAppViewContainer"], [data-testid="stHeader"],
            [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"],
            [data-testid="stText"], [data-testid="stTextInput"], [data-testid="stTextArea"],
            [data-testid="stDateInput"], [data-testid="stSelectbox"], [data-testid="stRadio"],
            [data-testid="stCheckbox"], [data-testid="stExpander"], [data-testid="stForm"],
            .stMarkdown, .stCaption, .stText, p, span, div, label, h1, h2, h3, h4, h5, h6 {
                direction: rtl !important;
                text-align: right !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _emp_ids(emp: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    if emp.get("id"):
        out.add(str(emp["id"]))
    if emp.get("team_user_id"):
        out.add(str(emp["team_user_id"]))
    return out


def can_create_patient_complaint(current_user: Dict[str, Any]) -> bool:
    role = str(current_user.get("role") or "employee").strip().lower()
    if role in ("general_manager", "chairman", "vice_chairman", "hr", "manager", "senior"):
        return True
    perms = current_user.get("permissions")
    if isinstance(perms, dict) and bool(perms.get("create_complaints", False)):
        return True
    return False


def _load_complaints_safe() -> List[Dict[str, Any]]:
    try:
        rows = fetch_table("complaints", order_by="created_at", desc=True, limit=1000)
        if isinstance(rows, list):
            return rows
    except Exception:
        pass
    cached = st.session_state.get("local_complaints_store")
    return list(cached) if isinstance(cached, list) else []


def _insert_complaint_safe(payload: Dict[str, Any], audit_msg: str, current_user: Dict[str, Any]) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    try:
        insert_record("complaints", payload)
        write_audit_log("complaints", "create_complaint", "إضافة", audit_msg, by_name=by_name, by_user_id=user_id)
    except Exception:
        store = list(st.session_state.get("local_complaints_store") or [])
        store.insert(
            0,
            {
                "id": f"cmp_{datetime.utcnow().timestamp()}",
                "created_at": datetime.utcnow().isoformat() + "Z",
                **payload,
            },
        )
        st.session_state["local_complaints_store"] = store


def _update_complaint_safe(rec_id: Any, patch: Dict[str, Any], audit_msg: str, current_user: Dict[str, Any]) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    try:
        update_record("complaints", rec_id, patch)
        write_audit_log("complaints", "update_complaint", "تعديل", audit_msg, by_name=by_name, by_user_id=user_id)
    except Exception:
        store = list(st.session_state.get("local_complaints_store") or [])
        for idx, item in enumerate(store):
            if str(item.get("id")) == str(rec_id):
                store[idx] = {**item, **patch}
        st.session_state["local_complaints_store"] = store


def _apply_financial_or_notice_for_complaint(
    complaint: Dict[str, Any],
    target_emp: Dict[str, Any],
    outcome_type: str,
    avg_days: float,
    actor_user: Dict[str, Any],
) -> None:
    """تطبيق القرار النهائي للشكوى: جزاء أو مكافأة في المرتب، أو لفت نظر في قائمة الجزاءات."""
    if not target_emp or not target_emp.get("id"):
        return
    if complaint.get("payroll_applied"):
        return

    cmp_id = str(complaint.get("id") or "")
    cmp_text = str(complaint.get("complaint_text") or "").strip()
    ref_label = f"شكوى مريض ({cmp_text[:45]})" if cmp_text else f"شكوى رقم {cmp_id[:8]}"
    pay_month = str(complaint.get("complaint_date") or date.today().isoformat())[:7]
    base_sal = float(target_emp.get("base_salary") or 0.0)
    actor_id = str(actor_user.get("id") or "")

    if outcome_type in ("penalty", "reward") and avg_days > 0:
        amt = calculate_penalty_or_reward_amount(base_sal, "days", float(avg_days))
        try:
            insert_record(
                "penalties_rewards",
                {
                    "employee_id": target_emp["id"],
                    "entry_type": outcome_type,
                    "calc_mode": "days",
                    "issue_date": date.today().isoformat(),
                    "payroll_month": pay_month,
                    "value": float(avg_days),
                    "calculated_amount": amt,
                    "title": f"{'جزاء' if outcome_type == 'penalty' else 'مكافأة'} بسبب {ref_label}",
                    "reason": f"قرار إداري ناتج عن {ref_label} بمتوسط {avg_days:g} يوم",
                    "status": "approved",
                    "approved_by_gm": actor_id or None,
                    "created_by": actor_id or None,
                },
            )
        except Exception:
            pass
    elif outcome_type == "notice":
        try:
            insert_record(
                "penalties_rewards",
                {
                    "employee_id": target_emp["id"],
                    "entry_type": "penalty",
                    "calc_mode": "amount",
                    "issue_date": date.today().isoformat(),
                    "payroll_month": pay_month,
                    "value": 0.0,
                    "calculated_amount": 0.0,
                    "title": f"لفت نظر بسبب {ref_label}",
                    "reason": f"لفت نظر مسجل بسبب {ref_label}",
                    "status": "approved",
                    "approved_by_gm": actor_id or None,
                    "created_by": actor_id or None,
                },
            )
        except Exception:
            pass


def resolve_complaint_decisions(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    حساب القرار النهائي من قرارات القيادات:
    - أغلبية جزاء أو مكافأة: متوسط الأيام المقررة.
    - تعادل بين جزاء وعدم جزاء: أولوية الترتيب (رئيس مجلس الإدارة، نائب الرئيس، المدير العام، المدير المباشر، السينيور).
    - لفت نظر: يُسجّل كـ notice إذا ساد أو اختير.
    """
    if not decisions:
        return {"final_decision": "pending", "avg_days": 0.0, "label_ar": "في انتظار قرار القيادات"}

    pen_list = [d for d in decisions if str(d.get("decision") or "").lower() == "penalty"]
    rew_list = [d for d in decisions if str(d.get("decision") or "").lower() == "reward"]
    not_list = [d for d in decisions if str(d.get("decision") or "").lower() == "notice"]

    if len(pen_list) > len(rew_list) and len(pen_list) >= len(not_list):
        avg_p = round_half_up_2(sum(float(x.get("days") or 0.0) for x in pen_list) / len(pen_list))
        return {
            "final_decision": "penalty",
            "avg_days": avg_p,
            "label_ar": f"جزاء بمتوسط {avg_p:g} يوم (يُطبق في المرتب)",
        }

    if len(rew_list) > len(pen_list) and len(rew_list) >= len(not_list):
        avg_r = round_half_up_2(sum(float(x.get("days") or 0.0) for x in rew_list) / len(rew_list))
        return {
            "final_decision": "reward",
            "avg_days": avg_r,
            "label_ar": f"مكافأة بمتوسط {avg_r:g} يوم (تُطبق في المرتب)",
        }

    if len(pen_list) == len(rew_list) and len(pen_list) > 0:
        ranked = sorted(
            pen_list + rew_list,
            key=lambda x: LEADERSHIP_RANK_ORDER.index(str(x.get("role") or "").lower())
            if str(x.get("role") or "").lower() in LEADERSHIP_RANK_ORDER
            else 999,
        )
        top_choice = ranked[0]
        top_kind = str(top_choice.get("decision") or "penalty").lower()
        top_days = round_half_up_2(float(top_choice.get("days") or 0.0))
        return {
            "final_decision": top_kind,
            "avg_days": top_days,
            "label_ar": f"حُسم بالأعلى ترتيباً ({top_choice.get('name_ar') or top_choice.get('role')}): {'جزاء' if top_kind == 'penalty' else 'مكافأة'} {top_days:g} يوم",
        }

    if not_list:
        return {
            "final_decision": "notice",
            "avg_days": 0.0,
            "label_ar": "لفت نظر (مسجّل في قائمة الجزاءات)",
        }

    return {"final_decision": "pending", "avg_days": 0.0, "label_ar": "في انتظار القرار"}


def _get_leadership_chain_for_employee(
    target_emp: Dict[str, Any],
    employees: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """بناء قائمة القيادات المعنية بالشكوى: السينيور (إن وُجد) أو المدير المباشر، ثم المدير العام، نائب الرئيس، رئيس مجلس الإدارة."""
    chain: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    emp_by_any: Dict[str, Dict[str, Any]] = {}
    for e in employees:
        for k in _emp_ids(e):
            emp_by_any[k] = e

    sen_ref = str(target_emp.get("senior_id") or "").strip()
    mgr_ref = str(target_emp.get("direct_manager_id") or "").strip()

    if sen_ref and sen_ref in emp_by_any:
        s_emp = emp_by_any[sen_ref]
        if str(s_emp.get("id")) not in seen_ids:
            chain.append(s_emp)
            seen_ids.add(str(s_emp.get("id")))
    elif mgr_ref and mgr_ref in emp_by_any:
        m_emp = emp_by_any[mgr_ref]
        if str(m_emp.get("id")) not in seen_ids:
            chain.append(m_emp)
            seen_ids.add(str(m_emp.get("id")))

    if mgr_ref and mgr_ref in emp_by_any:
        m_emp = emp_by_any[mgr_ref]
        if str(m_emp.get("id")) not in seen_ids:
            chain.append(m_emp)
            seen_ids.add(str(m_emp.get("id")))

    for r_code in ("general_manager", "vice_chairman", "chairman"):
        for e in employees:
            if (e.get("status") or "active") != "active":
                continue
            if str(e.get("role") or "").strip().lower() == r_code and str(e.get("id")) not in seen_ids:
                chain.append(e)
                seen_ids.add(str(e.get("id")))

    return chain


def render_complaints_module(
    current_user: Dict[str, Any],
    employees: List[Dict[str, Any]],
) -> None:
    _apply_complaints_rtl_styles()
    st.markdown("### 📩 الشكاوى والمقترحات")

    user_id = str(current_user.get("id") or "")
    my_ids = _emp_ids(current_user)
    user_role = str(current_user.get("role") or "employee").strip().lower()
    emp_by_id: Dict[str, Dict[str, Any]] = {str(e["id"]): e for e in employees if e.get("id")}
    all_complaints = _load_complaints_safe()

    has_patient_perm = can_create_patient_complaint(current_user)

    tab_titles: List[str] = ["✍️ شكوى أو مقترح"]
    if has_patient_perm:
        tab_titles.append("🩺 تسجيل شكوى مريض")
    tab_titles.append("💬 الرد على الشكاوى والقرارات")

    tabs = st.tabs(tab_titles)
    t_idx = 0

    # 1) شكوى أو مقترح من الموظف -> للمدير العام أو رئيس مجلس الإدارة
    with tabs[t_idx]:
        st.markdown("#### تقديم شكوى أو مقترح")
        c_top1, c_top2, c_top3 = st.columns([2.0, 2.0, 1.5])
        with c_top1:
            entry_kind = st.radio("النوع", ["complaint", "suggestion"], format_func=lambda x: "شكوى" if x == "complaint" else "مقترح", horizontal=True, key="emp_cmp_kind")
        with c_top2:
            recipient_choice = st.selectbox(
                "إرسال إلى",
                ["general_manager", "chairman"],
                format_func=lambda x: "المدير العام" if x == "general_manager" else "رئيس مجلس الإدارة",
                key="emp_cmp_recipient",
            )
        with c_top3:
            st.write("")
            show_my_name = st.checkbox("إظهار اسمي", value=True, key="emp_cmp_show_name")

        with st.form("submit_emp_complaint_form", clear_on_submit=True):
            cmp_date_val = st.date_input("التاريخ", value=date.today())
            cmp_body_txt = st.text_area("نص الشكوى أو المقترح", placeholder="اكتب التفاصيل هنا...", height=130)

            if st.form_submit_button("إرسال", type="primary"):
                if not cmp_body_txt.strip():
                    st.error("يرجى كتابة نص الشكوى أو المقترح أولاً.")
                else:
                    sender_display = str(current_user.get("name_ar") or "") if show_my_name else "مجهول"
                    _insert_complaint_safe(
                        payload={
                            "source_type": "staff_feedback",
                            "feedback_type": entry_kind,
                            "show_sender_name": bool(show_my_name),
                            "sender_id": user_id if show_my_name else None,
                            "sender_name": sender_display,
                            "recipient_role": recipient_choice,
                            "complaint_date": cmp_date_val.isoformat(),
                            "complaint_text": cmp_body_txt.strip(),
                            "status": "sent",
                            "created_by": user_id,
                        },
                        audit_msg=f"إرسال {'شكوى' if entry_kind == 'complaint' else 'مقترح'} ({sender_display})",
                        current_user=current_user,
                    )
                    st.success("✅ تم الإرسال بنجاح.")
                    st.rerun()

        st.divider()
        st.markdown("#### سجل الشكاوى والمقترحات الواردة")
        if user_role in ("general_manager", "chairman", "vice_chairman"):
            visible_staff_cmp = [
                c for c in all_complaints
                if str(c.get("source_type") or "") == "staff_feedback"
            ]
        else:
            visible_staff_cmp = [
                c for c in all_complaints
                if str(c.get("source_type") or "") == "staff_feedback"
                and str(c.get("sender_id") or "") in my_ids
            ]

        if visible_staff_cmp:
            st.dataframe(
                [
                    {
                        "النوع": "شكوى" if c.get("feedback_type") == "complaint" else "مقترح",
                        "المرسل": c.get("sender_name") or ("مجهول" if not c.get("show_sender_name") else ""),
                        "الجهة": "رئيس مجلس الإدارة" if c.get("recipient_role") == "chairman" else "المدير العام",
                        "التاريخ": str(c.get("complaint_date") or "")[:10],
                        "النص": c.get("complaint_text") or "",
                    }
                    for c in visible_staff_cmp
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("لا توجد سجلات معروضة حالياً.")

    t_idx += 1

    # 2) تسجيل شكوى مريض (للمخولين بالصلاحية)
    if has_patient_perm:
        with tabs[t_idx]:
            st.markdown("#### تسجيل شكوى مريض")
            active_emps = [e for e in employees if (e.get("status") or "active") == "active"]
            emp_pick_map = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in active_emps}

            if not emp_pick_map:
                st.warning("لا يوجد موظفون متاحون.")
            else:
                with st.form("create_patient_complaint_form", clear_on_submit=True):
                    pc1, pc2, pc3 = st.columns(3)
                    with pc1:
                        picked_target_lbl = st.selectbox("اسم الموظف", list(emp_pick_map.keys()))
                    with pc2:
                        car_num_txt = st.text_input("رقم السيارة", placeholder="اكتب رقم السيارة فقط")
                    with pc3:
                        p_cmp_date = st.date_input("التاريخ", value=date.today())

                    p_cmp_text = st.text_area("نص الشكوى", placeholder="اكتب نص شكوى المريض...", height=130)

                    if st.form_submit_button("إرسال الشكوى", type="primary"):
                        if not p_cmp_text.strip():
                            st.error("يرجى كتابة نص الشكوى أولاً.")
                        else:
                            t_emp = emp_pick_map[picked_target_lbl]
                            _insert_complaint_safe(
                                payload={
                                    "source_type": "patient_complaint",
                                    "target_employee_id": t_emp["id"],
                                    "target_employee_name": t_emp.get("name_ar"),
                                    "car_number": car_num_txt.strip(),
                                    "complaint_date": p_cmp_date.isoformat(),
                                    "complaint_text": p_cmp_text.strip(),
                                    "employee_reply": "",
                                    "replied_at": None,
                                    "decisions": [],
                                    "final_outcome": "pending",
                                    "payroll_applied": False,
                                    "created_by": user_id,
                                },
                                audit_msg=f"تسجيل شكوى مريض على {t_emp.get('name_ar')} (سيارة: {car_num_txt.strip()})",
                                current_user=current_user,
                            )
                            st.success(f"✅ تم إرسال الشكوى إلى الموظف ({t_emp.get('name_ar')}).")
                            st.rerun()

        t_idx += 1

    # 3) الرد من الموظف + قرارات القيادات وتطبيقها
    with tabs[t_idx]:
        st.markdown("#### الرد على الشكاوى والقرارات الإدارية")
        patient_complaints = [
            c for c in all_complaints
            if str(c.get("source_type") or "") == "patient_complaint"
        ]

        relevant_complaints: List[Dict[str, Any]] = []
        for cmp_item in patient_complaints:
            t_eid = str(cmp_item.get("target_employee_id") or "")
            t_emp_obj = emp_by_id.get(t_eid) or {}
            chain_emps = _get_leadership_chain_for_employee(t_emp_obj, employees) if t_emp_obj else []
            chain_ids: Set[str] = set()
            for ce in chain_emps:
                chain_ids.update(_emp_ids(ce))

            is_target_emp = t_eid in my_ids
            is_in_chain = any(k in chain_ids for k in my_ids)
            is_top_exec = user_role in ("general_manager", "chairman", "vice_chairman")

            if is_target_emp or is_in_chain or is_top_exec:
                relevant_complaints.append(cmp_item)

        if not relevant_complaints:
            st.info("لا توجد شكاوى مرضى مرسلة حالياً.")
            return

        for idx_c, cmp_item in enumerate(relevant_complaints):
            c_id = cmp_item.get("id")
            t_eid = str(cmp_item.get("target_employee_id") or "")
            t_emp_obj = emp_by_id.get(t_eid) or {}
            t_emp_name = str(cmp_item.get("target_employee_name") or t_emp_obj.get("name_ar") or "موظف")
            c_date_str = str(cmp_item.get("complaint_date") or "")[:10]
            c_car_str = str(cmp_item.get("car_number") or "—")
            c_body_str = str(cmp_item.get("complaint_text") or "")
            cur_reply = str(cmp_item.get("employee_reply") or "")
            decisions_list: List[Dict[str, Any]] = list(cmp_item.get("decisions") or [])

            res_info = resolve_complaint_decisions(decisions_list)

            with st.container(border=True):
                st.markdown(f"#### 📌 شكوى بحق: **{t_emp_name}** — التاريخ: `{c_date_str}` | رقم السيارة: `{c_car_str}`")
                st.write(f"**نص الشكوى:** {c_body_str}")

                if cur_reply:
                    st.info(f"**الرد:** {cur_reply}")

                if t_eid in my_ids:
                    reply_inp = st.text_area(
                        "الرد",
                        value=cur_reply,
                        placeholder="اكتب ردك هنا...",
                        key=f"emp_reply_txt_{c_id}_{idx_c}",
                    )
                    if st.button("إرسال الرد", type="primary", key=f"send_emp_reply_btn_{c_id}_{idx_c}"):
                        if not reply_inp.strip():
                            st.error("يرجى كتابة الرد أولاً.")
                        else:
                            _update_complaint_safe(
                                rec_id=c_id,
                                patch={
                                    "employee_reply": reply_inp.strip(),
                                    "replied_at": datetime.utcnow().isoformat() + "Z",
                                },
                                audit_msg=f"إرسال رد الموظف ({t_emp_name}) على الشكوى",
                                current_user=current_user,
                            )
                            st.success("✅ تم إرسال الرد وإحالته للقيادات.")
                            st.rerun()

                is_leader = user_role in LEADERSHIP_RANK_ORDER
                if is_leader and cur_reply:
                    st.divider()
                    st.markdown("##### القرار")
                    dec_choice = st.radio(
                        "اختر القرار",
                        ["penalty", "reward", "notice"],
                        format_func=lambda x: {"penalty": "جزاء", "reward": "مكافأة", "notice": "لفت نظر"}[x],
                        horizontal=True,
                        key=f"cmp_dec_radio_{c_id}_{idx_c}",
                    )

                    chosen_days_val = 0.0
                    if dec_choice in ("penalty", "reward"):
                        chosen_days_val = float(
                            st.selectbox(
                                "مقدار (بالأيام)",
                                DECISION_DAY_OPTIONS,
                                format_func=lambda d: f"{d:g} يوم",
                                key=f"cmp_dec_days_{c_id}_{idx_c}",
                            )
                        )

                    if st.button("إرسال القرار", type="primary", key=f"send_cmp_dec_btn_{c_id}_{idx_c}"):
                        new_dec_entry = {
                            "user_id": user_id,
                            "name_ar": current_user.get("name_ar"),
                            "role": user_role,
                            "decision": dec_choice,
                            "days": float(chosen_days_val),
                            "at": datetime.utcnow().isoformat() + "Z",
                        }
                        updated_decisions = [d for d in decisions_list if str(d.get("user_id")) != user_id] + [new_dec_entry]
                        resolved = resolve_complaint_decisions(updated_decisions)

                        should_apply_now = (
                            resolved["final_decision"] in ("penalty", "reward", "notice")
                            and not cmp_item.get("payroll_applied")
                        )
                        if should_apply_now:
                            _apply_financial_or_notice_for_complaint(
                                complaint=cmp_item,
                                target_emp=t_emp_obj,
                                outcome_type=resolved["final_decision"],
                                avg_days=float(resolved["avg_days"]),
                                actor_user=current_user,
                            )

                        _update_complaint_safe(
                            rec_id=c_id,
                            patch={
                                "decisions": updated_decisions,
                                "final_outcome": resolved["final_decision"],
                                "final_days": resolved["avg_days"],
                                "payroll_applied": True if should_apply_now else bool(cmp_item.get("payroll_applied")),
                            },
                            audit_msg=f"قرار ({resolved['label_ar']}) بشأن شكوى الموظف {t_emp_name}",
                            current_user=current_user,
                        )
                        st.success(f"✅ تم تسجيل القرار: {resolved['label_ar']}")
                        st.rerun()

                if decisions_list:
                    st.caption(f"النتيجة الحالية: **{res_info['label_ar']}**")
