from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple


def _round2(val: float) -> float:
    return float(Decimal(str(val or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_dt_date(val: Any) -> Optional[date]:
    if val is None or val == "":
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        s = val.strip()[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    return None


def _get_payroll_cycle_bounds(ym_str: str) -> Tuple[date, date]:
    """إرجاع بداية ونهاية الشهر الإداري (من 21 الشهر السابق إلى 20 الشهر المختار)."""
    try:
        yr_s, mo_s = str(ym_str or "").strip()[:7].split("-")
        yr = int(yr_s)
        mo = int(mo_s)
        end_dt = date(yr, mo, 20)
    except Exception:
        td = date.today()
        end_dt = date(td.year, td.month, 20)

    prev_yr = end_dt.year - 1 if end_dt.month == 1 else end_dt.year
    prev_mo = 12 if end_dt.month == 1 else end_dt.month - 1
    start_dt = date(prev_yr, prev_mo, 21)
    return start_dt, end_dt


def _parse_time_minutes(val: Any) -> Optional[int]:
    """تحويل وقت (HH:MM أو HH:MM:SS أو time أو datetime) إلى عدد الدقائق من منتصف الليل."""
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


def determine_initial_overtime_status(employee: Optional[Dict[str, Any]]) -> str:
    """
    تحديد حالة بداية اعتماد الوقت الإضافي حسب التسلسل الوظيفي للموظف:
    - إذا كان للموظف Senior مسجل: pending_senior (ثم مدير مباشر ثم مدير عام).
    - إذا لم يكن له Senior ولكن له مدير مباشر: pending_manager (ثم مدير عام).
    - إذا لم يكن له Senior ولا مدير مباشر: pending_gm.
    """
    emp = employee or {}
    role = str(emp.get("role") or "employee").strip().lower()
    if role == "general_manager":
        return "approved"

    has_senior = bool(str(emp.get("senior_id") or "").strip())
    has_manager = bool(str(emp.get("direct_manager_id") or "").strip())

    if role == "senior":
        return "pending_manager" if has_manager else "pending_gm"
    if role == "manager":
        return "pending_gm"

    if has_senior:
        return "pending_senior"
    if has_manager:
        return "pending_manager"
    return "pending_gm"


def evaluate_attendance_record(
    check_in_val: Any,
    check_out_val: Any,
    scheduled_start_val: Any,
    scheduled_end_val: Any,
    grace_late_minutes: int = 15,
    grace_early_minutes: int = 5,
    is_leave_day: bool = False,
    employee: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    تقييم سجل البصمة اليومي مع خصم دقائق السماح من الإعدادات:
    - late_minutes: صافي دقائق التأخير بعد خصم سماح الحضور.
    - early_leave_minutes: صافي دقائق الانصراف المبكر بعد خصم سماح الانصراف.
    - overtime_minutes: دقائق الوقت الإضافي بعد نهاية الدوام، وتبدأ بالمرحلة الصحيحة حسب وجود Senior أو مدير مباشر.
    """
    if is_leave_day:
        return {
            "late_minutes": 0,
            "early_leave_minutes": 0,
            "overtime_minutes": 0,
            "overtime_status": "none",
        }

    sched_start_m = _parse_time_minutes(scheduled_start_val)
    if sched_start_m is None:
        sched_start_m = 9 * 60

    sched_end_m = _parse_time_minutes(scheduled_end_val)
    if sched_end_m is None:
        sched_end_m = 17 * 60

    if sched_end_m <= sched_start_m:
        sched_end_m += 24 * 60

    cin_m = _parse_time_minutes(check_in_val)
    cout_m = _parse_time_minutes(check_out_val)

    late_grace = max(0, int(grace_late_minutes or 0))
    early_grace = max(0, int(grace_early_minutes or 0))

    late_minutes = 0
    if cin_m is not None:
        diff_in = cin_m - sched_start_m
        if diff_in > late_grace:
            late_minutes = int(diff_in - late_grace)

    early_leave_minutes = 0
    overtime_minutes = 0
    overtime_status = "none"

    if cout_m is not None:
        work_cout_m = cout_m
        if cout_m < sched_start_m:
            work_cout_m += 24 * 60

        if work_cout_m < sched_end_m:
            raw_early = int(sched_end_m - work_cout_m)
            if raw_early > early_grace:
                early_leave_minutes = int(raw_early - early_grace)
        elif work_cout_m > sched_end_m:
            overtime_minutes = int(work_cout_m - sched_end_m)
            overtime_status = determine_initial_overtime_status(employee)

    return {
        "late_minutes": late_minutes,
        "early_leave_minutes": early_leave_minutes,
        "overtime_minutes": overtime_minutes,
        "overtime_status": overtime_status,
    }


def calculate_penalty_or_reward_amount(
    base_salary: float,
    calc_mode: str,
    value: float,
) -> float:
    """حساب قيمة الجزاء أو المكافأة (مبلغ مباشر أو بعدد الأيام من أجر اليوم على 30 يوماً)."""
    val = float(value or 0)
    mode = (calc_mode or "amount").strip().lower()
    if mode == "days":
        daily_rate = float(base_salary or 0) / 30.0
        return _round2(daily_rate * val)
    return _round2(val)


def calculate_advance_schedule(
    total_amount: float,
    repayment_mode: str,
    installments_count: int = 1,
    fixed_installment: float = 0.0,
) -> Dict[str, Any]:
    """حساب جدول السلفة (خصم في نفس الشهر أو تقسيط على عدد شهور)."""
    tot = _round2(max(0.0, float(total_amount or 0)))
    mode = (repayment_mode or "single_month").strip().lower()

    if mode == "single_month" or int(installments_count or 1) <= 1:
        return {
            "installments_count": 1,
            "monthly_installment": tot,
            "last_installment_amount": tot,
        }

    cnt = max(1, int(installments_count))
    base_inst = _round2(tot / cnt)
    last_inst = _round2(tot - base_inst * (cnt - 1))
    return {
        "installments_count": cnt,
        "monthly_installment": base_inst,
        "last_installment_amount": last_inst,
    }


def _add_months_to_ym(ym_str: str, offset: int) -> str:
    try:
        y_s, m_s = str(ym_str or "").strip()[:7].split("-")
        yr = int(y_s)
        mo = int(m_s)
    except Exception:
        td = date.today()
        yr, mo = td.year, td.month

    total = (yr * 12 + (mo - 1)) + int(offset)
    return f"{total // 12:04d}-{(total % 12) + 1:02d}"


def get_employee_monthly_advances_summary(
    employee_id: str,
    target_ym: str,
    advances: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """تجميع أقساط السلف المستحقة لموظف في شهر محدد (YYYY-MM)."""
    ym = str(target_ym or "").strip()[:7]
    items: List[Dict[str, Any]] = []
    total_due = 0.0

    for adv in advances:
        if str(adv.get("employee_id") or "") != str(employee_id):
            continue
        st_val = (adv.get("status") or "").strip().lower()
        if st_val in ("rejected", "cancelled", "settled"):
            continue

        rem = _round2(float(adv.get("remaining_amount") or 0))
        if rem <= 0:
            continue

        cnt = max(1, int(adv.get("installments_count") or 1))
        m_inst = _round2(float(adv.get("monthly_installment") or rem))
        l_inst = _round2(float(adv.get("last_installment_amount") or m_inst))
        start_ym = str(adv.get("start_payroll_month") or ym)[:7]

        rem_left = rem
        for idx in range(cnt):
            if rem_left <= 0:
                break
            due_slot = l_inst if (idx == cnt - 1 and l_inst > 0) else m_inst
            due_slot = min(due_slot, rem_left)
            slot_ym = _add_months_to_ym(start_ym, idx)
            if slot_ym == ym:
                items.append({
                    "advance_id": str(adv.get("id") or ""),
                    "installment_no": idx + 1,
                    "installments_count": cnt,
                    "due_amount": _round2(due_slot),
                })
                total_due = _round2(total_due + due_slot)
            rem_left = _round2(rem_left - due_slot)

    return {
        "total_due": _round2(total_due),
        "items": items,
    }


def compute_employee_monthly_payroll(
    employee: Dict[str, Any],
    payroll_month: str,
    shift: Optional[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
    official_holidays: List[Dict[str, Any]],
    grace_late_minutes: int = 15,
    grace_early_minutes: int = 5,
) -> Dict[str, Any]:
    """
    حساب المرتب الشهري الفعلي للموظف (لكل الموظفين بلا استثناء):
    - المرتب الأساسي + إجازات رسمية (+يوم لكل إجازة مفعّلة) + المكافآت المعتمدة + الوقت الإضافي المعتمد
    - ناقص: خصم السلف المستحقة للشهر + الجزاءات المعتمدة + خصم التأخير والانصراف المبكر (بعد خصم السماح).
    """
    ym = str(payroll_month or "").strip()[:7]
    cycle_start, cycle_end = _get_payroll_cycle_bounds(ym)
    emp_id = str(employee.get("id") or "")
    emp_team_id = str(employee.get("team_user_id") or "")
    emp_ids = {k for k in (emp_id, emp_team_id) if k}

    base_salary = _round2(float(employee.get("base_salary") or 0))
    daily_rate = base_salary / 30.0 if base_salary > 0 else 0.0

    work_hours = float(employee.get("work_hours") or 8.0)
    if work_hours <= 0:
        work_hours = 8.0
    minute_rate = (daily_rate / work_hours) / 60.0 if daily_rate > 0 else 0.0

    # 1. الإجازات الرسمية المفعّلة والمضافة للمرتبات ضمن الشهر الإداري أو الشهر المحدد
    active_holidays_in_month = 0
    seen_holiday_dates = set()
    for hol in official_holidays:
        is_act = hol.get("active", True)
        add_flag = hol.get("add_day_to_payroll")
        if add_flag is None:
            add_flag = False
        if not (is_act is not False and bool(add_flag)):
            continue
        h_dt_obj = _parse_dt_date(hol.get("holiday_date"))
        h_raw_str = str(hol.get("holiday_date") or "")[:7]
        in_cycle = bool(h_dt_obj and (cycle_start <= h_dt_obj <= cycle_end))
        in_ym = (h_raw_str == ym) or bool(h_dt_obj and h_dt_obj.strftime("%Y-%m") == ym)
        if in_cycle or in_ym:
            key_d = h_dt_obj.isoformat() if h_dt_obj else str(hol.get("id") or hol.get("name_ar") or "")
            if key_d not in seen_holiday_dates:
                seen_holiday_dates.add(key_d)
                active_holidays_in_month += 1

    holiday_bonus = _round2(daily_rate * active_holidays_in_month)

    # 2. المكافآت والجزاءات المعتمدة للشهر
    rewards_total = 0.0
    penalties_deduction = 0.0
    for pr in penalties_rewards:
        if str(pr.get("employee_id") or "") not in emp_ids:
            continue
        if (pr.get("status") or "").strip().lower() != "approved":
            continue
        p_month = str(pr.get("payroll_month") or pr.get("issue_date") or "")[:7]
        if p_month != ym:
            continue
        amt = _round2(float(pr.get("calculated_amount") or 0))
        etype = (pr.get("entry_type") or "").strip().lower()
        if etype == "reward":
            rewards_total = _round2(rewards_total + amt)
        elif etype == "penalty":
            penalties_deduction = _round2(penalties_deduction + amt)

    # 3. الوقت الإضافي المعتمد وخصم التأخير والانصراف المبكر من سجلات البصمة
    approved_ot_minutes = 0
    deductible_late_and_early_minutes = 0
    late_grace = max(0, int(grace_late_minutes or 0))
    early_grace = max(0, int(grace_early_minutes or 0))
    s_start = employee.get("work_start_time") or "09:00:00"
    s_end = employee.get("work_end_time") or "17:00:00"

    for att in attendance_logs:
        if str(att.get("employee_id") or "") not in emp_ids:
            continue
        w_dt = _parse_dt_date(att.get("work_date"))
        w_ym = str(att.get("work_date") or "")[:7]
        in_period = (w_ym == ym) or bool(w_dt and (cycle_start <= w_dt <= cycle_end))
        if not in_period:
            continue

        ot_mins = int(att.get("overtime_minutes") or 0)
        ot_st = (att.get("overtime_status") or "").strip().lower()
        if ot_mins > 0 and ot_st == "approved":
            approved_ot_minutes += ot_mins

        has_times = bool(att.get("check_in") or att.get("check_out"))
        if has_times:
            ev = evaluate_attendance_record(
                att.get("check_in"),
                att.get("check_out"),
                att.get("scheduled_start") or s_start,
                att.get("scheduled_end") or s_end,
                grace_late_minutes=late_grace,
                grace_early_minutes=early_grace,
                is_leave_day=False,
                employee=employee,
            )
            deductible_late_and_early_minutes += int(ev["late_minutes"]) + int(ev["early_leave_minutes"])
        else:
            raw_late = int(att.get("late_minutes") or 0)
            raw_early = int(att.get("early_leave_minutes") or 0)
            if raw_late > late_grace:
                deductible_late_and_early_minutes += (raw_late - late_grace)
            if raw_early > early_grace:
                deductible_late_and_early_minutes += (raw_early - early_grace)

    overtime_pay = _round2(approved_ot_minutes * minute_rate)
    late_deduction = _round2(deductible_late_and_early_minutes * minute_rate)

    # 4. السلف المستحقة للشهر
    adv_summ = get_employee_monthly_advances_summary(emp_id, ym, advances)
    advances_deduction = _round2(float(adv_summ.get("total_due") or 0))

    net_salary = _round2(
        base_salary
        + holiday_bonus
        + rewards_total
        + overtime_pay
        - advances_deduction
        - penalties_deduction
        - late_deduction
    )

    return {
        "employee_id": emp_id,
        "employee_no": employee.get("employee_no", ""),
        "name_ar": employee.get("name_ar", ""),
        "job_title": employee.get("job_title", ""),
        "base_salary": base_salary,
        "holiday_days_count": active_holidays_in_month,
        "holiday_bonus": holiday_bonus,
        "rewards_total": rewards_total,
        "approved_overtime_minutes": approved_ot_minutes,
        "overtime_pay": overtime_pay,
        "advances_deduction": advances_deduction,
        "penalties_deduction": penalties_deduction,
        "late_deduction": late_deduction,
        "net_salary": net_salary,
    }


def build_employee_period_summary(
    employee: Dict[str, Any],
    start_date: date,
    end_date: date,
    leave_requests: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """تجميع ملخص الأداء التلقائي للموظف خلال فترة زمنية محددة (للتقييم KPIs)."""
    emp_id = str(employee.get("id") or "")
    emp_team_id = str(employee.get("team_user_id") or "")
    emp_ids = {k for k in (emp_id, emp_team_id) if k}

    approved_leaves_days = 0.0
    for lr in leave_requests:
        if str(lr.get("employee_id") or "") not in emp_ids:
            continue
        if (lr.get("status") or "").strip().lower() != "approved":
            continue
        s_dt = _parse_dt_date(lr.get("start_date"))
        if s_dt and start_date <= s_dt <= end_date:
            approved_leaves_days += float(lr.get("days") or 0)

    late_days_count = 0
    total_late_minutes = 0
    approved_overtime_minutes = 0

    for att in attendance_logs:
        if str(att.get("employee_id") or "") not in emp_ids:
            continue
        w_dt = _parse_dt_date(att.get("work_date"))
        if not w_dt or not (start_date <= w_dt <= end_date):
            continue
        lm = int(att.get("late_minutes") or 0)
        if lm > 0:
            late_days_count += 1
            total_late_minutes += lm
        ot_m = int(att.get("overtime_minutes") or 0)
        ot_s = (att.get("overtime_status") or "").strip().lower()
        if ot_m > 0 and ot_s == "approved":
            approved_overtime_minutes += ot_m

    total_rewards = 0.0
    total_penalties = 0.0
    for pr in penalties_rewards:
        if str(pr.get("employee_id") or "") not in emp_ids:
            continue
        if (pr.get("status") or "").strip().lower() != "approved":
            continue
        i_dt = _parse_dt_date(pr.get("issue_date"))
        if not i_dt or not (start_date <= i_dt <= end_date):
            continue
        amt = float(pr.get("calculated_amount") or 0)
        if (pr.get("entry_type") or "").strip().lower() == "reward":
            total_rewards += amt
        else:
            total_penalties += amt

    total_advances = 0.0
    for adv in advances:
        if str(adv.get("employee_id") or "") not in emp_ids:
            continue
        st_a = (adv.get("status") or "").strip().lower()
        if st_a in ("rejected", "cancelled"):
            continue
        r_dt = _parse_dt_date(adv.get("request_date"))
        if r_dt and start_date <= r_dt <= end_date:
            total_advances += float(adv.get("total_amount") or 0)

    return {
        "approved_leaves_days": _round2(approved_leaves_days),
        "late_days_count": late_days_count,
        "total_late_minutes": total_late_minutes,
        "approved_overtime_minutes": approved_overtime_minutes,
        "total_rewards": _round2(total_rewards),
        "total_penalties": _round2(total_penalties),
        "total_advances": _round2(total_advances),
    }
