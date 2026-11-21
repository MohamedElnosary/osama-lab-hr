from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_PERMISSIONS: Dict[str, bool] = {
    "approve_leaves": False,
    "approve_penalties": False,
    "approve_rewards": False,
    "approve_advances": False,
    "approve_overtime": False,
    "manage_payroll": False,
    "view_all_reports": False,
    "manage_settings": False,
    "is_monitor_only": False,
}


def parse_date_safe(value: Any) -> Optional[date]:
    """تحويل قيمة تاريخ (date / datetime / str) إلى كائن date بأمان."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        text = value.strip()[:10]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def round_half_up_2(val: float) -> float:
    """تقريب رقم عشري إلى منزلتين بطريقة رياضية نظيفة."""
    return float(Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_effective_permissions(employee: Dict[str, Any]) -> Dict[str, bool]:
    """دمج الصلاحيات الافتراضية للدور مع التخصيصات الفردية للموظف/الـ Senior."""
    role = (employee.get("role") or "employee").strip().lower()
    perms = dict(DEFAULT_PERMISSIONS)

    if role == "general_manager":
        perms.update({
            "approve_leaves": True,
            "approve_penalties": True,
            "approve_rewards": True,
            "approve_advances": True,
            "approve_overtime": True,
            "manage_payroll": True,
            "view_all_reports": True,
            "manage_settings": True,
        })
    elif role in ("chairman", "vice_chairman"):
        perms.update({
            "view_all_reports": True,
            "is_monitor_only": True,
        })
    elif role == "hr":
        perms.update({
            "manage_payroll": True,
            "approve_advances": True,
            "view_all_reports": True,
        })
    elif role == "manager":
        perms.update({
            "approve_leaves": True,
            "approve_penalties": True,
            "approve_rewards": True,
            "approve_advances": True,
            "approve_overtime": True,
        })
    elif role == "senior":
        # الـ Senior مسؤول عن تنظيم وموافقة الإجازات فقط افتراضياً، ويمكن منحه صلاحيات إضافية
        perms.update({
            "approve_leaves": True,
        })

    custom_perms = employee.get("permissions")
    if isinstance(custom_perms, dict):
        for k, v in custom_perms.items():
            if k in perms and isinstance(v, bool):
                perms[k] = v

    return perms


def calculate_prorated_leave_entitlement(
    hire_date_val: Any,
    admin_year: str = "2026",
    annual_base: float = 15.0,
    casual_base: float = 6.0,
    is_annual_reset: bool = False,
) -> Tuple[float, float, str]:
    """
    حساب الاستحقاق السنوي والعارض بناءً على تاريخ التعيين خلال السنة،
    أو توحيد الجميع عند تصفير/تجديد بداية السنة الإدارية.
    - تعيين 6 أشهر فأكثر متبقية: النصف تقريباً (أو حسب نسبة الأشهر المتبقية من السنة)
    - تعيين خلال آخر 3 شهور من السنة: الربع تقريباً
    - عند التصفير السنوي (is_annual_reset=True): يحصل الجميع على الرصيد الكامل بالتساوي.
    """
    if is_annual_reset:
        return (
            round_half_up_2(annual_base),
            round_half_up_2(casual_base),
            "رصيد بداية السنة الموحّد للجميع",
        )

    hire_dt = parse_date_safe(hire_date_val)
    if not hire_dt:
        return round_half_up_2(annual_base), round_half_up_2(casual_base), "رصيد افتراضي"

    try:
        year_int = int(str(admin_year).strip()[:4])
    except ValueError:
        year_int = date.today().year

    year_end = date(year_int, 12, 31)
    if hire_dt.year < year_int:
        return round_half_up_2(annual_base), round_half_up_2(casual_base), "معيّن قبل بداية السنة"

    if hire_dt.year > year_int:
        return 0.0, 0.0, "تاريخ التعيين في سنة لاحقة"

    # حساب عدد الأشهر المتبقية تقريباً حتى نهاية السنة
    remaining_months = (year_end.year - hire_dt.year) * 12 + (year_end.month - hire_dt.month) + 1
    if remaining_months >= 9:
        factor = 1.0
        note = "استحقاق كامل للسنة"
    elif 5 <= remaining_months <= 8:
        factor = 0.5
        note = "استحقاق نسبي (نصف الرصيد - حوالي 6 أشهر متبقية)"
    else:
        factor = 0.25
        note = "استحقاق نسبي (ربع الرصيد - آخر 3 شهور من السنة)"

    return (
        round_half_up_2(annual_base * factor),
        round_half_up_2(casual_base * factor),
        note,
    )


def has_completed_min_tenure(hire_date_val: Any, ref_date: date, min_days: int = 30) -> Tuple[bool, str]:
    """التحقق من مرور المدة المطلوبة (افتراضياً شهر = 30 يوماً) من تاريخ التعيين."""
    hire_dt = parse_date_safe(hire_date_val)
    if not hire_dt:
        return False, "تاريخ التعيين غير مسجل للموظف."

    activation_date = hire_dt + timedelta(days=max(0, int(min_days)))
    if ref_date < activation_date:
        return (
            False,
            f"رصيد الإجازات غير مفعّل بعد؛ يبدأ التفعيل بعد مرور {min_days} يوماً من تاريخ التعيين ({activation_date.isoformat()}).",
        )
    return True, ""


def determine_initial_approval_route(
    employee: Dict[str, Any],
    all_employees: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    تحديد مسار الموافقات الأولي لطلب الإجازة:
    - إذا كان الموظف تابعاً لـ Senior (له senior_id): يمر أولاً على الـ Senior ثم المدير المباشر/الفرع ثم المدير العام.
    - إذا كان الموظف هو Senior نفسه أو تحت مدير مباشر/فرع: يمر على المدير المباشر/الفرع ثم المدير العام.
    - إذا لم يكن له مدير مباشر: يمر مباشرة للمدير العام.
    """
    senior_id = employee.get("senior_id")
    direct_manager_id = employee.get("direct_manager_id")

    emp_map_by_id = {str(e.get("id")): e for e in all_employees if e.get("id")}
    emp_map_by_team = {str(e.get("team_user_id")): e for e in all_employees if e.get("team_user_id")}

    def _resolve_emp(raw_id: Any) -> Optional[Dict[str, Any]]:
        if not raw_id:
            return None
        s = str(raw_id)
        return emp_map_by_id.get(s) or emp_map_by_team.get(s)

    senior_emp = _resolve_emp(senior_id)
    manager_emp = _resolve_emp(direct_manager_id)

    if senior_emp and str(senior_emp.get("id")) != str(employee.get("id")):
        return {
            "status": "pending_senior",
            "senior_id": senior_emp.get("team_user_id") or senior_emp.get("id"),
            "direct_manager_id": (manager_emp.get("team_user_id") or manager_emp.get("id")) if manager_emp else None,
            "next_stage": "senior",
        }

    if manager_emp and str(manager_emp.get("id")) != str(employee.get("id")):
        return {
            "status": "pending_manager",
            "senior_id": None,
            "direct_manager_id": manager_emp.get("team_user_id") or manager_emp.get("id"),
            "next_stage": "manager",
        }

    return {
        "status": "pending_gm",
        "senior_id": None,
        "direct_manager_id": None,
        "next_stage": "gm",
    }


def validate_leave_request(
    employee: Dict[str, Any],
    leave_type: Dict[str, Any],
    start_date: date,
    end_date: date,
    days_requested: float,
    existing_requests: List[Dict[str, Any]],
    leave_balance: Optional[Dict[str, Any]] = None,
    has_attachment: bool = False,
    reason_text: str = "",
    now_dt: Optional[datetime] = None,
) -> Tuple[bool, str]:
    """
    التحقق الكامل من صحة طلب الإجازة وفق قواعد معامل أ.د أسامة عبد المتعال منصور:
    - إجازة عارضة: في نفس اليوم، مفتوحة حتى نفاد الرصيد العارض.
    - إجازة اعتيادية: قبل التنفيذ بـ 24 ساعة على الأقل، حد أقصى يومان في الشهر، وتُخصم من الاعتيادي.
    - إجازة سنوية: قبلها بأسبوع (7 أيام) على الأقل، تُخصم من الاعتيادي، وحد أقصى 6 أيام.
    - إجازة مرضية: في أي يوم، سبب ومرفق تقرير (عند التفعيل).
    - عمرة (10 أيام) وحج (15 يوماً).
    - عدم تفعيل الرصيد إلا بعد مرور 30 يوماً من تاريخ التعيين (للأنواع الخاضعة للشرط).
    """
    current_dt = now_dt or datetime.now()
    today_date = current_dt.date()

    if end_date < start_date:
        return False, "تاريخ نهاية الإجازة لا يمكن أن يكون قبل تاريخ البداية."

    if days_requested <= 0:
        return False, "عدد أيام الإجازة يجب أن يكون أكبر من صفر."

    code = (leave_type.get("code") or leave_type.get("id") or "").strip().lower()
    type_name = leave_type.get("name_ar") or code

    # التحقق من فترة التفعيل بعد التعيين (شهر = 30 يوماً)
    min_tenure = int(leave_type.get("min_tenure_days") if leave_type.get("min_tenure_days") is not None else 30)
    if min_tenure > 0:
        tenure_ok, tenure_msg = has_completed_min_tenure(employee.get("hire_date"), start_date, min_tenure)
        if not tenure_ok:
            return False, tenure_msg

    # التحقق من الحد الأقصى للأيام في الطلب الواحد
    max_per_req = leave_type.get("max_days_per_request")
    if max_per_req is not None and float(days_requested) > float(max_per_req):
        return False, f"الحد الأقصى لـ ({type_name}) هو {max_per_req} أيام ولا يمكن تجاوزه."

    # قواعد خاصة بكل نوع إجازة
    if code == "casual":
        # الإجازة العارضة تُطلب في نفس اليوم
        if start_date != today_date or end_date != today_date:
            return False, "الإجازة العارضة متاحة للتقديم في نفس اليوم فقط."
    elif code == "regular":
        # الإجازة الاعتيادية: قبل التنفيذ بـ 24 ساعة على الأقل + حد أقصى يومان في الشهر
        start_dt_midnight = datetime.combine(start_date, datetime.min.time())
        if (start_dt_midnight - current_dt).total_seconds() < 24 * 3600:
            return False, "الإجازة الاعتيادية يجب تقديمها قبل موعد التنفيذ بـ 24 ساعة على الأقل."

        max_monthly = int(leave_type.get("max_days_per_month") or 2)
        emp_id = str(employee.get("id"))
        emp_team_id = str(employee.get("team_user_id") or "")
        target_ym = start_date.strftime("%Y-%m")

        used_regular_in_month = 0.0
        for req in existing_requests:
            req_emp = str(req.get("employee_id") or "")
            req_user = str(req.get("employee_user_id") or "")
            if req_emp != emp_id and (not emp_team_id or req_user != emp_team_id):
                continue
            if (req.get("type_code") or "").lower() != "regular":
                continue
            status = (req.get("status") or "").lower()
            if status.startswith("rejected") or status.startswith("cancelled"):
                continue
            req_start = parse_date_safe(req.get("start_date"))
            if req_start and req_start.strftime("%Y-%m") == target_ym:
                used_regular_in_month += float(req.get("days") or 0)

        if used_regular_in_month + float(days_requested) > max_monthly:
            return (
                False,
                f"غير مسموح بأكثر من {max_monthly} أيام إجازة اعتيادية في الشهر؛ لقد تجاوزت الحد المسموح لشهر {target_ym}.",
            )
    elif code == "annual":
        # الإجازة السنوية: قبلها بأسبوع على الأقل، تُخصم من الاعتيادي، بحد أقصى 6 أيام
        if (start_date - today_date).days < 7:
            return False, "الإجازة السنوية يجب تقديمها قبل موعدها بأسبوع (7 أيام) على الأقل."
        if float(days_requested) > 6.0:
            return False, "الإجازة السنوية لا تزيد عن 6 أيام تحت أي ظرف."
    elif code == "sick":
        if not reason_text.strip():
            return False, "يرجى كتابة سبب الإجازة المرضية."
        if bool(leave_type.get("allow_attachment")) and not has_attachment:
            return False, "يرجى إرفاق التقرير الطبي مع طلب الإجازة المرضية."
    elif code == "umrah":
        if float(days_requested) > 10.0:
            return False, "إجازة العمرة بحد أقصى 10 أيام."
    elif code == "hajj":
        if float(days_requested) > 15.0:
            return False, "إجازة الحج بحد أقصى 15 يوماً."

    # التحقق من الرصيد المتاح إذا كان النوع يخصم من رصيد
    if bool(leave_type.get("deducts")) and leave_balance is not None:
        bal_field = (leave_type.get("balance_field") or "annual").strip().lower()
        if bal_field == "casual":
            total_bal = float(leave_balance.get("casual") or 0)
            used_bal = float(leave_balance.get("used_casual") or 0)
            remaining = total_bal - used_bal
            if float(days_requested) > remaining:
                return False, f"رصيد الإجازات العارضة المتبقي ({remaining}) لا يكفي لطلب ({days_requested}) يوم."
        else:
            total_bal = float(leave_balance.get("annual") or 0)
            used_bal = float(leave_balance.get("used_annual") or 0)
            remaining = total_bal - used_bal
            if float(days_requested) > remaining:
                return False, f"رصيد الإجازات الاعتيادي/السنوي المتبقي ({remaining}) لا يكفي لطلب ({days_requested}) يوم."

    return True, "الطلب مطابق لقواعد الإجازات."
