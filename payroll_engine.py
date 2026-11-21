import math
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple


def _round2(val: float) -> float:
    return float(Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _round_int(val: float) -> int:
    return int(Decimal(str(val)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_time_safe(val: Any) -> Optional[time]:
    """تحويل نص أو كائن وقت (HH:MM أو HH:MM:SS) إلى كائن time."""
    if val is None or val == "":
        return None
    if isinstance(val, time):
        return val
    if isinstance(val, datetime):
        return val.time()
    text = str(val).strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def calculate_advance_schedule(
    total_amount: float,
    repayment_mode: str = "single_month",
    installments_count: int = 1,
    fixed_monthly_amount: float = 0.0,
) -> Dict[str, Any]:
    """
    حساب خطة سداد السلفة بدون كسور مزعجة:
    1) single_month: تُخصم بالكامل في نفس الشهر.
    2) installments_count: تقسيم المبلغ على عدد شهور بأرقام صحيحة نظيفة + تصفية المتبقي في القسط الأخير.
       مثال: 10,000 على 10 شهور -> 1,000 شهرياً × 10 شهور.
    3) fixed_installment: تحديد قيمة القسط الشهري الثابت وتصفية الباقي في القسط الأخير.
       مثال: 9,000 بقسط 2,000 -> 4 شهور × 2,000 + القسط الخامس 1,000.
    """
    total = _round2(max(0.0, float(total_amount)))
    if total <= 0:
        return {
            "installments_count": 1,
            "monthly_installment": 0.0,
            "last_installment_amount": 0.0,
            "schedule_preview": [],
        }

    mode = (repayment_mode or "single_month").strip().lower()

    if mode == "single_month":
        return {
            "installments_count": 1,
            "monthly_installment": total,
            "last_installment_amount": total,
            "schedule_preview": [total],
        }

    if mode == "fixed_installment" and float(fixed_monthly_amount) > 0:
        step = _round2(min(total, float(fixed_monthly_amount)))
        full_months = int(math.floor(total / step))
        remainder = _round2(total - (full_months * step))
        if remainder > 0:
            count = full_months + 1
            last_amt = remainder
            preview = [step] * full_months + [last_amt]
        else:
            count = max(1, full_months)
            last_amt = step
            preview = [step] * count
        return {
            "installments_count": count,
            "monthly_installment": step,
            "last_installment_amount": last_amt,
            "schedule_preview": preview,
        }

    # الوضع الافتراضي للتقسيط بعدد الشهور (installments_count) بدون كسور عشرية
    count = max(1, int(installments_count or 1))
    if count == 1:
        return {
            "installments_count": 1,
            "monthly_installment": total,
            "last_installment_amount": total,
            "schedule_preview": [total],
        }

    # تقريب القسط الشهري لأقرب عدد صحيح (أو مضاعف نظيف) وتصفية الفرق في الشهر الأخير
    base_step = float(_round_int(total / count))
    if base_step <= 0:
        base_step = 1.0
    while base_step * (count - 1) >= total and base_step > 1:
        base_step -= 1.0

    last_amt = _round2(total - (base_step * (count - 1)))
    if last_amt <= 0:
        count = max(1, int(math.ceil(total / base_step)))
        last_amt = _round2(total - (base_step * (count - 1)))

    preview = [base_step] * (count - 1) + [last_amt]
    return {
        "installments_count": count,
        "monthly_installment": base_step,
        "last_installment_amount": last_amt,
        "schedule_preview": preview,
    }


def evaluate_attendance_record(
    check_in_val: Any,
    check_out_val: Any,
    scheduled_start_val: Any,
    scheduled_end_val: Any,
    grace_late_minutes: int = 15,
    grace_early_minutes: int = 5,
    is_leave_day: bool = False,
) -> Dict[str, Any]:
    """
    احتساب دقائق التأخير والانصراف المبكر والوقت الإضافي لبصمة يوم واحد:
    - إذا كان اليوم إجازة معتمدة: لا يُحتسب تأخير.
    - الحضور: سماح 15 دقيقة، وبعدها يُخصم بالدقيقة (دقيقة، دقيقتان، إلخ).
    - الانصراف: سماح 5 دقائق فقط، وأي انصراف أبكر من ذلك يُخصم بالدقيقة.
    - الوقت الإضافي: يُقفل الدوام الرسمي عند scheduled_end، وما بعده يُرفع للموافقة (مدير مباشر ثم مدير عام).
    """
    if is_leave_day:
        return {
            "late_minutes": 0,
            "early_leave_minutes": 0,
            "overtime_minutes": 0,
            "overtime_status": "none",
        }

    cin = parse_time_safe(check_in_val)
    cout = parse_time_safe(check_out_val)
    s_start = parse_time_safe(scheduled_start_val)
    s_end = parse_time_safe(scheduled_end_val)

    if not s_start or not s_end:
        return {
            "late_minutes": 0,
            "early_leave_minutes": 0,
            "overtime_minutes": 0,
            "overtime_status": "none",
        }

    start_min = _time_to_minutes(s_start)
    end_min = _time_to_minutes(s_end)
    # دعم الشفتات التي تمتد لما بعد منتصف الليل
    if end_min <= start_min:
        end_min += 24 * 60

    late_minutes = 0
    if cin:
        cin_min = _time_to_minutes(cin)
        diff_in = cin_min - start_min
        if diff_in > int(grace_late_minutes):
            late_minutes = diff_in - int(grace_late_minutes)

    early_leave_minutes = 0
    overtime_minutes = 0
    if cout:
        cout_min = _time_to_minutes(cout)
        if cout_min < start_min and end_min > 24 * 60:
            cout_min += 24 * 60

        if cout_min < end_min:
            diff_early = end_min - cout_min
            if diff_early > int(grace_early_minutes):
                early_leave_minutes = diff_early - int(grace_early_minutes)
        elif cout_min > end_min:
            overtime_minutes = cout_min - end_min

    return {
        "late_minutes": int(max(0, late_minutes)),
        "early_leave_minutes": int(max(0, early_leave_minutes)),
        "overtime_minutes": int(max(0, overtime_minutes)),
        "overtime_status": "pending_manager" if overtime_minutes > 0 else "none",
    }


def calculate_penalty_or_reward_amount(
    base_salary: float,
    calc_mode: str,
    value: float,
    days_in_month: int = 30,
) -> float:
    """تحويل الجزاء أو المكافأة إلى قيمة مالية (سواء أُدخل كمبلغ مباشر أو بعدد الأيام)."""
    v = max(0.0, float(value or 0))
    if (calc_mode or "amount").strip().lower() == "days":
        daily_rate = float(base_salary or 0) / max(1, int(days_in_month))
        return _round2(daily_rate * v)
    return _round2(v)


def _months_diff(start_ym: str, target_ym: str) -> int:
    """حساب فرق الشهور بين شهر البداية YYYY-MM والشهر المستهدف YYYY-MM."""
    sy, sm = int(start_ym[:4]), int(start_ym[5:7])
    ty, tm = int(target_ym[:4]), int(target_ym[5:7])
    return (ty - sy) * 12 + (tm - sm)


def get_employee_monthly_advances_summary(
    employee_id: str,
    payroll_month: str,
    all_advances: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    تجميع كل السلف المستحقة للموظف في شهر محدد (سواء سلفة فورية في نفس الشهر أو قسط من سلفة مقسطة)
    في مكان واحد ليسهل على HR تسديدها دفعة واحدة.
    """
    items = []
    total_due = 0.0
    for adv in all_advances:
        if str(adv.get("employee_id")) != str(employee_id):
            continue
        status = (adv.get("status") or "").lower()
        if status != "gm_approved":
            continue
        remaining = float(adv.get("remaining_amount") or 0)
        if remaining <= 0:
            continue

        start_ym = (adv.get("start_payroll_month") or "")[:7]
        if len(start_ym) < 7:
            continue

        idx = _months_diff(start_ym, payroll_month[:7])
        count = int(adv.get("installments_count") or 1)
        if idx < 0 or idx >= count:
            continue

        installment_amt = (
            float(adv.get("last_installment_amount") or 0)
            if idx == count - 1
            else float(adv.get("monthly_installment") or 0)
        )
        due_this_month = _round2(min(remaining, installment_amt))
        if due_this_month > 0:
            total_due = _round2(total_due + due_this_month)
            items.append({
                "advance_id": adv.get("id"),
                "installment_no": idx + 1,
                "installments_count": count,
                "due_amount": due_this_month,
                "remaining_before": remaining,
                "reason": adv.get("reason") or "",
            })

    return {
        "total_due": total_due,
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
    days_in_month: int = 30,
) -> Dict[str, Any]:
    """
    حساب شيت المرتب الشهري الكامل للموظف:
    - الأساسي: base_salary
    - الإضافات: مكافآت معتمدة + وقت إضافي معتمد + يوم إضافي عن كل إجازة رسمية مفعّلة في الشهر
    - الخصومات: السلف المستحقة في الشهر + الجزاءات المعتمدة + خصم التأخير والانصراف المبكر
    - المرتب الفعلي (الصافي): الأساسي + الإضافات - الخصومات (بدون تأمينات أو ضرائب)
    """
    emp_id = str(employee.get("id"))
    base_salary = _round2(float(employee.get("base_salary") or 0))
    daily_rate = base_salary / max(1, int(days_in_month))

    # حساب ساعات العمل اليومية لحساب أجر الدقيقة
    shift_minutes = 8 * 60
    if shift:
        st_time = parse_time_safe(shift.get("start_time"))
        en_time = parse_time_safe(shift.get("end_time"))
        if st_time and en_time:
            diff_m = _time_to_minutes(en_time) - _time_to_minutes(st_time)
            if diff_m <= 0:
                diff_m += 24 * 60
            shift_minutes = max(60, diff_m)

    minute_rate = daily_rate / shift_minutes

    # 1. الإجازات الرسمية المفعّلة في هذا الشهر (تضيف يوماً لكل إجازة رسمية مفعّلة)
    active_holidays_count = 0
    for hol in official_holidays:
        if not hol.get("active", True) or not hol.get("add_day_to_payroll", True):
            continue
        h_date = str(hol.get("holiday_date") or "")[:7]
        if h_date == payroll_month[:7]:
            active_holidays_count += 1
    holiday_bonus = _round2(active_holidays_count * daily_rate)

    # 2. سجلات البصمة (التأخير + الانصراف المبكر + الوقت الإضافي المعتمد من المدير العام)
    total_late_mins = 0
    total_early_mins = 0
    approved_ot_mins = 0
    for log in attendance_logs:
        if str(log.get("employee_id")) != emp_id:
            continue
        w_date = str(log.get("work_date") or "")[:7]
        if w_date != payroll_month[:7]:
            continue
        total_late_mins += int(log.get("late_minutes") or 0)
        total_early_mins += int(log.get("early_leave_minutes") or 0)
        if (log.get("overtime_status") or "").lower() == "approved":
            approved_ot_mins += int(log.get("overtime_minutes") or 0)

    late_deduction = _round2((total_late_mins + total_early_mins) * minute_rate)
    overtime_pay = _round2(approved_ot_mins * minute_rate)

    # 3. الجزاءات والمكافآت المعتمدة في هذا الشهر
    rewards_total = 0.0
    penalties_deduction = 0.0
    for pr in penalties_rewards:
        if str(pr.get("employee_id")) != emp_id:
            continue
        if (pr.get("payroll_month") or "")[:7] != payroll_month[:7]:
            continue
        if (pr.get("status") or "").lower() != "approved":
            continue
        amt = float(pr.get("calculated_amount") or 0)
        if amt <= 0:
            amt = calculate_penalty_or_reward_amount(
                base_salary,
                pr.get("calc_mode") or "amount",
                float(pr.get("value") or 0),
                days_in_month,
            )
        if (pr.get("entry_type") or "").lower() == "reward":
            rewards_total = _round2(rewards_total + amt)
        else:
            penalties_deduction = _round2(penalties_deduction + amt)

    # 4. السلف المستحقة في هذا الشهر (مجمّعة)
    adv_summary = get_employee_monthly_advances_summary(emp_id, payroll_month, advances)
    advances_deduction = adv_summary["total_due"]

    # 5. المرتب الفعلي النهائي
    total_additions = _round2(holiday_bonus + rewards_total + overtime_pay)
    total_deductions = _round2(advances_deduction + penalties_deduction + late_deduction)
    net_salary = _round2(max(0.0, base_salary + total_additions - total_deductions))

    return {
        "payroll_month": payroll_month[:7],
        "employee_id": emp_id,
        "employee_no": employee.get("employee_no") or "",
        "name_ar": employee.get("name_ar") or "",
        "job_title": employee.get("job_title") or "",
        "base_salary": base_salary,
        "active_holidays_count": active_holidays_count,
        "holiday_bonus": holiday_bonus,
        "rewards_total": rewards_total,
        "approved_ot_minutes": approved_ot_mins,
        "overtime_pay": overtime_pay,
        "advances_deduction": advances_deduction,
        "advances_items": adv_summary["items"],
        "penalties_deduction": penalties_deduction,
        "total_late_minutes": total_late_mins,
        "total_early_minutes": total_early_mins,
        "late_deduction": late_deduction,
        "total_additions": total_additions,
        "total_deductions": total_deductions,
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
    """
    بناء ملف الأداء التلقائي للموظف خلال فترة محددة (لعرضه أثناء تقييم الـ KPI نصف السنوي أو السنوي):
    الإجازات + التأخيرات + الوقت الإضافي + الخصومات + المكافآت + السلف.
    """
    emp_id = str(employee.get("id"))
    s_iso = start_date.isoformat()
    e_iso = end_date.isoformat()
    s_ym = start_date.strftime("%Y-%m")
    e_ym = end_date.strftime("%Y-%m")

    approved_leaves_days = 0.0
    leaves_by_type: Dict[str, float] = {}
    for lr in leave_requests:
        if str(lr.get("employee_id")) != emp_id:
            continue
        if (lr.get("status") or "").lower() != "approved":
            continue
        lr_start = str(lr.get("start_date") or "")[:10]
        if s_iso <= lr_start <= e_iso:
            d = float(lr.get("days") or 0)
            approved_leaves_days += d
            t_name = lr.get("type_name") or lr.get("type_code") or "أخرى"
            leaves_by_type[t_name] = leaves_by_type.get(t_name, 0.0) + d

    late_days_count = 0
    total_late_minutes = 0
    total_early_minutes = 0
    approved_overtime_minutes = 0
    for att in attendance_logs:
        if str(att.get("employee_id")) != emp_id:
            continue
        w_date = str(att.get("work_date") or "")[:10]
        if s_iso <= w_date <= e_iso:
            lm = int(att.get("late_minutes") or 0)
            em = int(att.get("early_leave_minutes") or 0)
            if lm > 0:
                late_days_count += 1
            total_late_minutes += lm
            total_early_minutes += em
            if (att.get("overtime_status") or "").lower() == "approved":
                approved_overtime_minutes += int(att.get("overtime_minutes") or 0)

    total_penalties = 0.0
    total_rewards = 0.0
    for pr in penalties_rewards:
        if str(pr.get("employee_id")) != emp_id:
            continue
        if (pr.get("status") or "").lower() != "approved":
            continue
        pm = (pr.get("payroll_month") or "")[:7]
        if s_ym <= pm <= e_ym:
            amt = float(pr.get("calculated_amount") or 0)
            if (pr.get("entry_type") or "").lower() == "reward":
                total_rewards = _round2(total_rewards + amt)
            else:
                total_penalties = _round2(total_penalties + amt)

    total_advances = 0.0
    for adv in advances:
        if str(adv.get("employee_id")) != emp_id:
            continue
        if (adv.get("status") or "").lower() in ("gm_approved", "settled"):
            r_date = str(adv.get("request_date") or "")[:10]
            if s_iso <= r_date <= e_iso:
                total_advances = _round2(total_advances + float(adv.get("total_amount") or 0))

    return {
        "period_start": s_iso,
        "period_end": e_iso,
        "approved_leaves_days": approved_leaves_days,
        "leaves_by_type": leaves_by_type,
        "late_days_count": late_days_count,
        "total_late_minutes": total_late_minutes,
        "total_early_minutes": total_early_minutes,
        "approved_overtime_minutes": approved_overtime_minutes,
        "total_penalties": total_penalties,
        "total_rewards": total_rewards,
        "total_advances": total_advances,
    }
