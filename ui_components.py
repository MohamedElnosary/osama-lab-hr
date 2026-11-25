def _sync_leave_days_state() -> None:
    """تحديث عدد أيام الإجازة في session_state فوراً عند تغيير تاريخ البداية أو النهاية."""
    s = parse_date_safe(st.session_state.get("lr_start_date_input"))
    e = parse_date_safe(st.session_state.get("lr_end_date_input"))
    if not s or not e or e < s:
        st.session_state["calculated_leave_days"] = 0
    else:
        st.session_state["calculated_leave_days"] = int((e - s).days + 1)


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
