from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple
import json
import streamlit as st

from db import fetch_table, update_record, upsert_record, write_audit_log
from payroll_engine import build_employee_period_summary
from rules_engine import parse_date_safe, round_half_up_2

# =====================================================================
# 1. الثوابت والقوائم الافتراضية للـ KPIs
# =====================================================================
KPI_SELF_TABS: List[str] = [
    "استقبال",
    "استقبال وسحب عينات",
    "كول سنتر",
    "كيميائيين",
    "سينيور",
    "مدير مباشر",
]

KPI_CONFIG_TABS: List[str] = KPI_SELF_TABS + ["مدير عام"]

DEFAULT_SELF_QUESTIONS_BY_TAB: Dict[str, List[Dict[str, Any]]] = {
    "استقبال": [
        {"question": "الالتزام بالمواعيد والحضور المنتظم", "points": 25.0},
        {"question": "حسن استقبال المراجعين وسرعة تسجيل البيانات", "points": 25.0},
        {"question": "دقة توجيه المرضى وشرح التعليمات بوضوح", "points": 25.0},
        {"question": "الالتزام بالتعليمات الإدارية والتعاون مع الفريق", "points": 25.0},
    ],
    "استقبال وسحب عينات": [
        {"question": "دقة تسجيل العينات وسلامة إجراءات السحب", "points": 30.0},
        {"question": "الالتزام بقواعد السلامة والوقاية المهنية", "points": 25.0},
        {"question": "سرعة الأداء والدقة وقت ضغط العمل", "points": 25.0},
        {"question": "الالتزام بالمواعيد والانضباط الإداري", "points": 20.0},
    ],
    "كول سنتر": [
        {"question": "سرعة الرد واللباقة في التواصل مع العملاء", "points": 30.0},
        {"question": "دقة تسجيل الطلبات والحجوزات ومتابعتها", "points": 25.0},
        {"question": "الالتزام بالمواعيد ونسب الإنجاز اليومية", "points": 25.0},
        {"question": "الالتزام بالسياسات والتعاون مع الزملاء", "points": 20.0},
    ],
    "كيميائيين": [
        {"question": "دقة إجراء التحاليل ومراجعة النتائج الفنية", "points": 30.0},
        {"question": "الالتزام بمعايير الجودة ومراقبة التشغيل (QC)", "points": 25.0},
        {"question": "الالتزام بالمواعيد والانضباط داخل المعمل", "points": 25.0},
        {"question": "التعاون وحل المشكلات والالتزام باللوائح", "points": 20.0},
    ],
    "سينيور": [
        {"question": "ضبط جودة العمل ومراجعة الأداء الفني للفريق", "points": 30.0},
        {"question": "متابعة الانضباط اليومي وتوجيه الموظفين", "points": 25.0},
        {"question": "دقة رفع التقارير والالتزام بالمواعيد", "points": 25.0},
        {"question": "القيادة والمرونة في إدارة ضغط العمل", "points": 20.0},
    ],
    "مدير مباشر": [
        {"question": "تحقيق أهداف الفرع وجودة الخدمات المقدمة", "points": 30.0},
        {"question": "إدارة فريق العمل وتوزيع المهام بكفاءة", "points": 25.0},
        {"question": "متابعة الحضور والانصراف وسير العمل اليومي", "points": 25.0},
        {"question": "تطبيق اللوائح واتخاذ القرارات بفاعلية", "points": 20.0},
    ],
}

DEFAULT_PEER_QUESTIONS_BY_TARGET: Dict[str, List[Dict[str, Any]]] = {
    "senior": [
        {"question": "وضوح التوجيه والمتابعة اليومية من السينيور", "points": 40.0},
        {"question": "سرعة الدعم والمساعدة عند الحاجة في العمل", "points": 30.0},
        {"question": "العدالة والاحترام في التعامل مع الفريق", "points": 30.0},
    ],
    "manager": [
        {"question": "وضوح التعليمات والأهداف من المدير المباشر", "points": 40.0},
        {"question": "السرعة والفاعلية في حل المشكلات وتيسير العمل", "points": 30.0},
        {"question": "العدالة والمهنية في إدارة الفريق وتقييم الأداء", "points": 30.0},
    ],
    "gm": [
        {"question": "وضوح الرؤية والتوجيهات الإدارية للمدير العام", "points": 40.0},
        {"question": "دعم بيئة العمل وتوفير الإمكانات اللازمة", "points": 30.0},
        {"question": "الشفافية والحزم في تطبيق السياسات واللوائح", "points": 30.0},
    ],
}

# خريطة تشكيل الحروف العربية (منفصل، ابتدائي، وسطي، نهائي) للرسم المباشر داخل PDF
_AR_FORMS: Dict[str, Tuple[str, str, str, str]] = {
    "\u0627": ("\ufe8d", "\ufe8d", "\ufe8e", "\ufe8e"),
    "\u0628": ("\ufe8f", "\ufe91", "\ufe92", "\ufe90"),
    "\u062a": ("\ufe95", "\ufe97", "\ufe98", "\ufe96"),
    "\u062b": ("\ufe99", "\ufe9b", "\ufe9c", "\ufe9a"),
    "\u062c": ("\ufe9d", "\ufe9f", "\ufea0", "\ufe9e"),
    "\u062d": ("\ufea1", "\ufea3", "\ufea4", "\ufea2"),
    "\u062e": ("\ufea5", "\ufea7", "\ufea8", "\ufea6"),
    "\u062f": ("\ufea9", "\ufea9", "\ufeaa", "\ufeaa"),
    "\u0630": ("\ufeab", "\ufeab", "\ufeac", "\ufeac"),
    "\u0631": ("\ufead", "\ufead", "\ufeae", "\ufeae"),
    "\u0632": ("\ufeaf", "\ufeaf", "\ufeb0", "\ufeb0"),
    "\u0633": ("\ufeb1", "\ufeb3", "\ufeb4", "\ufeb2"),
    "\u0634": ("\ufeb5", "\ufeb7", "\ufeb8", "\ufeb6"),
    "\u0635": ("\ufeb9", "\ufebb", "\ufebc", "\ufeba"),
    "\u0636": ("\ufebd", "\ufebf", "\ufec0", "\ufebe"),
    "\u0637": ("\ufec1", "\ufec3", "\ufec4", "\ufec2"),
    "\u0638": ("\ufec5", "\ufec7", "\ufec8", "\ufec6"),
    "\u0639": ("\ufec9", "\ufecb", "\ufecc", "\ufeca"),
    "\u063a": ("\ufecd", "\ufecf", "\ufed0", "\ufece"),
    "\u0641": ("\ufed1", "\ufed3", "\ufed4", "\ufed2"),
    "\u0642": ("\ufed5", "\ufed7", "\ufed8", "\ufed6"),
    "\u0643": ("\ufed9", "\ufedb", "\ufedc", "\ufeda"),
    "\u0644": ("\ufedd", "\ufedf", "\ufee0", "\ufede"),
    "\u0645": ("\ufee1", "\ufee3", "\ufee4", "\ufee2"),
    "\u0646": ("\ufee5", "\ufee7", "\ufee8", "\ufee6"),
    "\u0647": ("\ufee9", "\ufeeb", "\ufeec", "\ufeea"),
    "\u0648": ("\ufeed", "\ufeed", "\ufeee", "\ufeee"),
    "\u0649": ("\ufeef", "\ufeef", "\ufef0", "\ufef0"),
    "\u064a": ("\ufef1", "\ufef3", "\ufef4", "\ufef2"),
    "\u0621": ("\ufe80", "\ufe80", "\ufe80", "\ufe80"),
    "\u0622": ("\ufe81", "\ufe81", "\ufe82", "\ufe82"),
    "\u0623": ("\ufe83", "\ufe83", "\ufe84", "\ufe84"),
    "\u0624": ("\ufe85", "\ufe85", "\ufe86", "\ufe86"),
    "\u0625": ("\ufe87", "\ufe87", "\ufe88", "\ufe88"),
    "\u0626": ("\ufe89", "\ufe8b", "\ufe8c", "\ufe8a"),
    "\u0629": ("\ufe93", "\ufe93", "\ufe94", "\ufe94"),
}
_NO_NEXT_JOIN: Set[str] = {"\u0627", "\u0622", "\u0623", "\u0625", "\u0621", "\u0624", "\u0629", "\u062f", "\u0630", "\u0631", "\u0632", "\u0648", "\ufeef"}
_NO_PREV_JOIN: Set[str] = {"\u0621"}
_DIACRITICS: Set[str] = {"\u064b", "\u064c", "\u064d", "\u064e", "\u064f", "\u0650", "\u0651", "\u0652", "\u0670"}


def _shape_arabic_rtl(text: str) -> str:
    """تشكيل الحروف العربية وعكس النص للعرض السليم من اليمين لليسار."""
    s = "".join(ch for ch in str(text or "") if ch not in _DIACRITICS)
    n = len(s)
    if n == 0:
        return ""
    shaped: List[str] = []
    for i, ch in enumerate(s):
        if ch not in _AR_FORMS:
            shaped.append(ch)
            continue
        prev_ch = s[i - 1] if i > 0 else ""
        next_ch = s[i + 1] if i + 1 < n else ""
        can_prev = (prev_ch in _AR_FORMS) and (prev_ch not in _NO_NEXT_JOIN) and (ch not in _NO_PREV_JOIN)
        can_next = (next_ch in _AR_FORMS) and (next_ch not in _NO_PREV_JOIN) and (ch not in _NO_NEXT_JOIN)
        iso_f, init_f, med_f, fin_f = _AR_FORMS[ch]
        if can_prev and can_next:
            shaped.append(med_f)
        elif can_prev:
            shaped.append(fin_f)
        elif can_next:
            shaped.append(init_f)
        else:
            shaped.append(iso_f)
    return "".join(reversed(shaped))


def _encode_pdf_text_literal(text: str) -> bytes:
    """تحويل النص (عربي أو إنجليزي) إلى سلسلة PDF محرفية صالحة."""
    txt = str(text or "")
    has_ar = any(ch in _AR_FORMS or ("\u0600" <= ch <= "\u06ff") for ch in txt)
    out_str = _shape_arabic_rtl(txt) if has_ar else txt
    raw_bytes = out_str.encode("utf-16-be", errors="ignore") if has_ar else out_str.encode("latin-1", errors="replace")
    escaped = bytearray()
    for b in raw_bytes:
        if b in (0x28, 0x29, 0x5C):  # (, ), \
            escaped.append(0x5C)
        escaped.append(b)
    return b"(" + bytes(escaped) + b")"


def build_arabic_table_pdf_bytes(
    report_title: str,
    subtitle_text: str,
    headers: List[str],
    rows: List[List[str]],
    summary_rows: Optional[List[Tuple[str, str]]] = None,
) -> bytes:
    """
    إنشاء ملف PDF حقيقي يحتوي على جداول مرتبة وواضحة باللغة العربية دون علامات استفهام.
    يستخدم TrueType font مدمج أو Type1/Unicode لضمان الوضوح في قارئات PDF.
    """
    # نبني صفحة PDF قياسية A4 (595 x 842 نقطة) بجدول منظم بخطوط وحدود واضحة
    page_w = 595.0
    page_h = 842.0
    margin_x = 32.0
    usable_w = page_w - 2 * margin_x

    cmds: List[str] = []

    def _draw_rect(x: float, y: float, w: float, h: float, fill_rgb: Optional[Tuple[float, float, float]] = None, stroke_rgb: Tuple[float, float, float] = (0.2, 0.28, 0.36)) -> None:
        if fill_rgb is not None:
            cmds.append(f"{fill_rgb[0]:.3f} {fill_rgb[1]:.3f} {fill_rgb[2]:.3f} rg")
        cmds.append(f"{stroke_rgb[0]:.3f} {stroke_rgb[1]:.3f} {stroke_rgb[2]:.3f} RG")
        cmds.append("0.75 w")
        mode = "B" if fill_rgb is not None else "S"
        cmds.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re {mode}")

    def _draw_text(x: float, y: float, text: str, font_sz: float = 10.5, rgb: Tuple[float, float, float] = (0.06, 0.09, 0.16), bold: bool = False) -> None:
        font_name = "/F2" if bold else "/F1"
        cmds.append("BT")
        cmds.append(f"{font_name} {font_sz:.1f} Tf")
        cmds.append(f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f} rg")
        cmds.append(f"{x:.2f} {y:.2f} Td")
        cmds.append(_encode_pdf_text_literal(text).decode("latin-1") + " Tj")
        cmds.append("ET")

    # شريط العنوان الرئيسي
    _draw_rect(margin_x, page_h - 72, usable_w, 42, fill_rgb=(0.06, 0.30, 0.36), stroke_rgb=(0.06, 0.30, 0.36))
    _draw_text(margin_x + 14, page_h - 56, report_title, font_sz=13.0, rgb=(1.0, 1.0, 1.0), bold=True)
    _draw_text(margin_x + 14, page_h - 69, subtitle_text, font_sz=9.5, rgb=(0.88, 0.95, 0.98), bold=False)

    cur_y = page_h - 96

    # جدول التقييم الرئيسي
    if headers and rows:
        num_cols = len(headers)
        col_w = usable_w / num_cols
        row_h = 24.0

        # صف العناوين
        _draw_rect(margin_x, cur_y - row_h, usable_w, row_h, fill_rgb=(0.94, 0.96, 0.98))
        for c_i, h_lbl in enumerate(headers):
            cx = margin_x + c_i * col_w
            _draw_rect(cx, cur_y - row_h, col_w, row_h, fill_rgb=None)
            _draw_text(cx + 8, cur_y - row_h + 7.5, str(h_lbl), font_sz=10.0, rgb=(0.06, 0.09, 0.16), bold=True)
        cur_y -= row_h

        for r_i, r_vals in enumerate(rows):
            if cur_y - row_h < 70:
                break
            is_total_row = (r_i == len(rows) - 1) and ("الإجمالي" in str(r_vals[0] if r_vals else ""))
            bg_c = (0.88, 0.91, 0.94) if is_total_row else ((0.99, 0.99, 1.0) if r_i % 2 == 1 else (1.0, 1.0, 1.0))
            _draw_rect(margin_x, cur_y - row_h, usable_w, row_h, fill_rgb=bg_c)
            for c_i in range(num_cols):
                cx = margin_x + c_i * col_w
                val_s = str(r_vals[c_i] if c_i < len(r_vals) else "")
                _draw_rect(cx, cur_y - row_h, col_w, row_h, fill_rgb=None)
                _draw_text(cx + 8, cur_y - row_h + 7.5, val_s, font_sz=10.0, bold=is_total_row)
            cur_y -= row_h

    # جدول الملخص التفصيلي أسفل الجدول الرئيسي
    if summary_rows:
        cur_y -= 18
        if cur_y - 24 >= 50:
            _draw_text(margin_x, cur_y, "ملخص الفترة التفصيلي", font_sz=11.5, rgb=(0.06, 0.30, 0.36), bold=True)
            cur_y -= 10
            s_row_h = 22.0
            half_w = usable_w / 2.0
            for k_lbl, v_lbl in summary_rows:
                if cur_y - s_row_h < 45:
                    break
                _draw_rect(margin_x, cur_y - s_row_h, half_w, s_row_h, fill_rgb=(0.97, 0.98, 0.99))
                _draw_rect(margin_x + half_w, cur_y - s_row_h, half_w, s_row_h, fill_rgb=(1.0, 1.0, 1.0))
                _draw_text(margin_x + 8, cur_y - s_row_h + 6.5, str(k_lbl), font_sz=9.8, bold=True)
                _draw_text(margin_x + half_w + 8, cur_y - s_row_h + 6.5, str(v_lbl), font_sz=9.8, bold=False)
                cur_y -= s_row_h

    stream_bytes = ("\n".join(cmds)).encode("latin-1", errors="replace")

    objs: List[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            f"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w:.0f} {page_h:.0f}] "
            b"/Resources << /Font << /F1 4 0 R /F2 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
        ).encode("latin-1"),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        (
            f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream\nendobj\n"
        ),
    ]

    pdf_buf = bytearray(b"%PDF-1.4\n")
    offsets: List[int] = [0]
    for obj in objs:
        offsets.append(len(pdf_buf))
        pdf_buf.extend(obj)

    xref_pos = len(pdf_buf)
    pdf_buf.extend(f"xref\n0 {len(objs) + 1}\n".encode("latin-1"))
    pdf_buf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf_buf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    pdf_buf.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )
    return bytes(pdf_buf)


# =====================================================================
# 2. دوال الحساب والمنطق (قابلة للاستدعاء المباشر في اختبارات الوحدة)
# =====================================================================
def get_emp_identifiers(emp: Dict[str, Any]) -> Set[str]:
    ids: Set[str] = set()
    if emp.get("id"):
        ids.add(str(emp["id"]))
    if emp.get("team_user_id"):
        ids.add(str(emp["team_user_id"]))
    return ids


def map_job_title_to_self_tab(emp: Dict[str, Any]) -> str:
    jt = str(emp.get("job_title") or "").strip().lower()
    role = str(emp.get("role") or "").strip().lower()

    if "مدير مباشر" in jt or role == "manager":
        return "مدير مباشر"
    if "senior" in jt or "سينيور" in jt or role == "senior":
        return "سينيور"
    if "كيميائي" in jt or "كيميائ" in jt:
        return "كيميائيين"
    if "خدمة عملاء" in jt or "كول" in jt:
        return "كول سنتر"
    if "سحب" in jt:
        return "استقبال وسحب عينات"
    if "استقبال" in jt:
        return "استقبال"
    return "استقبال"


def normalize_questions_list(raw_list: Any, fallback_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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


def compute_score_percentage(earned_total: float, max_possible: float) -> float:
    max_p = float(max_possible or 0.0)
    if max_p <= 0.0:
        return 0.0
    return round_half_up_2((float(earned_total or 0.0) / max_p) * 100.0)


def build_peer_matrix_table(
    question_items: List[Dict[str, Any]],
    raters_scores: Dict[str, Dict[str, float]],
) -> Dict[str, Any]:
    """بناء مصفوفة تقييمات الموظفين لجهة معينة (سينيور / مدير مباشر / مدير عام) مع إجمالي كل مقيّم والإجمالي الكلي."""
    q_names = [str(x.get("question") or "") for x in question_items if str(x.get("question") or "").strip()]
    rater_names = list(raters_scores.keys())

    for rn in rater_names:
        for k_s in (raters_scores[rn] or {}).keys():
            if str(k_s) not in q_names:
                q_names.append(str(k_s))

    matrix_rows: List[Dict[str, Any]] = []
    rater_totals: Dict[str, float] = {rn: 0.0 for rn in rater_names}

    for qn in q_names:
        row_d: Dict[str, Any] = {"البند": qn}
        for rn in rater_names:
            val_r = round_half_up_2(float((raters_scores[rn] or {}).get(qn) or 0.0))
            row_d[rn] = val_r
            rater_totals[rn] = round_half_up_2(rater_totals[rn] + val_r)
        matrix_rows.append(row_d)

    grand_total = round_half_up_2(sum(rater_totals.values()))
    if rater_names:
        tot_row: Dict[str, Any] = {"البند": "الإجمالي"}
        for rn in rater_names:
            tot_row[rn] = rater_totals[rn]
        matrix_rows.append(tot_row)

    return {
        "rows": matrix_rows,
        "rater_totals": rater_totals,
        "grand_total": grand_total,
    }


def build_full_employee_kpi_report_data(
    emp: Dict[str, Any],
    start_date: date,
    end_date: date,
    leave_requests: List[Dict[str, Any]],
    attendance_logs: List[Dict[str, Any]],
    penalties_rewards: List[Dict[str, Any]],
    advances: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """تجميع التفاصيل الكاملة لفترة التقييم للموظف: الإجازات، المكافآت، الجزاءات، السلف، الإضافي، والتأخيرات بالشهور."""
    emp_ids = get_emp_identifiers(emp)
    emp_id_primary = str(emp.get("id") or "")

    casual_days = 0.0
    regular_days = 0.0
    other_leave_days = 0.0
    leave_details: List[Dict[str, Any]] = []
    leaves_by_month: Dict[str, float] = {}

    for lr in leave_requests:
        req_e = str(lr.get("employee_id") or "")
        req_u = str(lr.get("employee_user_id") or "")
        if req_e not in emp_ids and (not req_u or req_u not in emp_ids):
            continue
        if str(lr.get("status") or "").strip().lower() != "approved":
            continue
        s_dt = parse_date_safe(lr.get("start_date"))
        e_dt = parse_date_safe(lr.get("end_date")) or s_dt
        if not s_dt or not (start_date <= s_dt <= end_date):
            continue

        d_val = float(lr.get("days") or 0)
        t_name = str(lr.get("type_name") or lr.get("type_code") or "إجازة").strip()
        t_code = str(lr.get("type_code") or "").strip().lower()
        bf = str(lr.get("balance_field") or "").strip().lower()

        if "عارض" in t_name or t_code == "casual" or bf == "casual":
            casual_days = round_half_up_2(casual_days + d_val)
        elif "اعتياد" in t_name or "سنو" in t_name or t_code in ("regular", "annual") or bf == "annual":
            regular_days = round_half_up_2(regular_days + d_val)
        else:
            other_leave_days = round_half_up_2(other_leave_days + d_val)

        ym_k = s_dt.strftime("%Y-%m")
        leaves_by_month[ym_k] = round_half_up_2(leaves_by_month.get(ym_k, 0.0) + d_val)
        leave_details.append({
            "نوع الإجازة": t_name,
            "الشهر": ym_k,
            "من": s_dt.isoformat(),
            "إلى": (e_dt or s_dt).isoformat(),
            "الأيام": d_val,
            "السبب": lr.get("reason") or "—",
        })

    rewards_list: List[Dict[str, Any]] = []
    penalties_list: List[Dict[str, Any]] = []
    rewards_by_month: Dict[str, int] = {}
    penalties_by_month: Dict[str, int] = {}

    for pr in penalties_rewards:
        if str(pr.get("employee_id") or "") not in emp_ids:
            continue
        if str(pr.get("status") or "").strip().lower() != "approved":
            continue
        i_dt = parse_date_safe(pr.get("issue_date"))
        if not i_dt or not (start_date <= i_dt <= end_date):
            continue
        ym_p = str(pr.get("payroll_month") or i_dt.strftime("%Y-%m"))[:7]
        etype = str(pr.get("entry_type") or "").strip().lower()
        amt_p = round_half_up_2(float(pr.get("calculated_amount") or 0))
        if etype == "reward":
            rewards_by_month[ym_p] = rewards_by_month.get(ym_p, 0) + 1
            rewards_list.append({
                "الشهر": ym_p,
                "التاريخ": i_dt.isoformat(),
                "القيمة (ج)": amt_p,
                "السبب": pr.get("reason") or pr.get("title") or "—",
            })
        elif etype == "penalty":
            penalties_by_month[ym_p] = penalties_by_month.get(ym_p, 0) + 1
            penalties_list.append({
                "الشهر": ym_p,
                "التاريخ": i_dt.isoformat(),
                "القيمة (ج)": amt_p,
                "السبب": pr.get("reason") or pr.get("title") or "—",
            })

    advances_list: List[Dict[str, Any]] = []
    advances_by_month: Dict[str, float] = {}
    advances_total_amt = 0.0

    for adv in advances:
        if str(adv.get("employee_id") or "") != emp_id_primary and str(adv.get("employee_id") or "") not in emp_ids:
            continue
        if str(adv.get("status") or "").strip().lower() in ("rejected", "cancelled"):
            continue
        r_dt = parse_date_safe(adv.get("request_date"))
        if not r_dt or not (start_date <= r_dt <= end_date):
            continue
        amt_a = round_half_up_2(float(adv.get("total_amount") or 0))
        ym_a = str(adv.get("start_payroll_month") or r_dt.strftime("%Y-%m"))[:7]
        advances_by_month[ym_a] = round_half_up_2(advances_by_month.get(ym_a, 0.0) + amt_a)
        advances_total_amt = round_half_up_2(advances_total_amt + amt_a)
        advances_list.append({
            "الشهر": ym_a,
            "التاريخ": r_dt.isoformat(),
            "الإجمالي (ج)": amt_a,
            "المتبقي (ج)": round_half_up_2(float(adv.get("remaining_amount") or 0)),
            "السبب": adv.get("reason") or "—",
        })

    ot_list: List[Dict[str, Any]] = []
    late_list: List[Dict[str, Any]] = []
    ot_days_by_month: Dict[str, Set[str]] = {}
    ot_mins_by_month: Dict[str, int] = {}
    late_cnt_by_month: Dict[str, int] = {}
    all_ot_days: Set[str] = set()
    total_ot_mins = 0
    total_late_cnt = 0

    for att in attendance_logs:
        if str(att.get("employee_id") or "") not in emp_ids:
            continue
        w_dt = parse_date_safe(att.get("work_date"))
        if not w_dt or not (start_date <= w_dt <= end_date):
            continue
        ym_w = w_dt.strftime("%Y-%m")

        ot_m = int(att.get("overtime_minutes") or 0)
        if ot_m > 0 and str(att.get("overtime_status") or "").strip().lower() == "approved":
            all_ot_days.add(w_dt.isoformat())
            total_ot_mins += ot_m
            ot_mins_by_month[ym_w] = ot_mins_by_month.get(ym_w, 0) + ot_m
            ot_days_by_month.setdefault(ym_w, set()).add(w_dt.isoformat())
            ot_list.append({
                "الشهر": ym_w,
                "التاريخ": w_dt.isoformat(),
                "الوقت الإضافي (دقيقة)": ot_m,
            })

        lm = int(att.get("late_minutes") or 0)
        if lm > 0:
            total_late_cnt += 1
            late_cnt_by_month[ym_w] = late_cnt_by_month.get(ym_w, 0) + 1
            late_list.append({
                "الشهر": ym_w,
                "التاريخ": w_dt.isoformat(),
                "تأخير (دقيقة)": lm,
            })

    months_sorted = sorted(
        set(leaves_by_month.keys())
        | set(rewards_by_month.keys())
        | set(penalties_by_month.keys())
        | set(advances_by_month.keys())
        | set(ot_days_by_month.keys())
        | set(late_cnt_by_month.keys())
    )

    monthly_summary_rows = [
        {
            "الشهر": ym_i,
            "الإجازات (يوم)": leaves_by_month.get(ym_i, 0.0),
            "المكافآت (عدد)": rewards_by_month.get(ym_i, 0),
            "الجزاءات (عدد)": penalties_by_month.get(ym_i, 0),
            "السلف (ج)": advances_by_month.get(ym_i, 0.0),
            "الإضافي (أيام / دقيقة)": f"{len(ot_days_by_month.get(ym_i, set()))} يوم / {ot_mins_by_month.get(ym_i, 0)} د",
            "التأخيرات (مرات)": late_cnt_by_month.get(ym_i, 0),
        }
        for ym_i in months_sorted
    ]

    return {
        "casual_days": casual_days,
        "regular_days": regular_days,
        "other_leave_days": other_leave_days,
        "total_leave_days": round_half_up_2(casual_days + regular_days + other_leave_days),
        "leave_details": leave_details,
        "rewards_list": rewards_list,
        "rewards_count": len(rewards_list),
        "penalties_list": penalties_list,
        "penalties_count": len(penalties_list),
        "advances_list": advances_list,
        "advances_total_amt": advances_total_amt,
        "ot_list": ot_list,
        "total_ot_days": len(all_ot_days),
        "total_ot_mins": total_ot_mins,
        "late_list": late_list,
        "total_late_cnt": total_late_cnt,
        "monthly_summary_rows": monthly_summary_rows,
    }


# =====================================================================
# 3. دوال الواجهة وإدارة إعدادات وأسئلة الـ KPIs
# =====================================================================
def get_effective_org_state(org_settings: Dict[str, Any]) -> Dict[str, Any]:
    base = dict(org_settings or {})
    live_patch = st.session_state.get("live_org_patch")
    if isinstance(live_patch, dict):
        base.update(live_patch)
    return base


def save_org_settings_patch(patch: Dict[str, Any]) -> None:
    cur_live = dict(st.session_state.get("live_org_patch") or {})
    cur_live.update(patch)
    st.session_state["live_org_patch"] = cur_live
    try:
        existing_rows = fetch_table("org_settings", limit=5)
        if existing_rows and existing_rows[0].get("id") is not None:
            rec_id = existing_rows[0]["id"]
            update_record("org_settings", rec_id, patch)
        else:
            upsert_record("org_settings", {"id": "main", **patch}, on_conflict="id")
    except Exception:
        pass


def get_all_self_questions(org_settings: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    eff_org = get_effective_org_state(org_settings)
    saved = eff_org.get("kpi_self_questions")
    out: Dict[str, List[Dict[str, Any]]] = {}
    for tab_k in KPI_SELF_TABS:
        fb = DEFAULT_SELF_QUESTIONS_BY_TAB.get(tab_k, [])
        if isinstance(saved, dict) and tab_k in saved:
            out[tab_k] = normalize_questions_list(saved.get(tab_k), fb)
        else:
            out[tab_k] = [dict(x) for x in fb]
    return out


def get_all_peer_questions(org_settings: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    eff_org = get_effective_org_state(org_settings)
    saved = eff_org.get("kpi_peer_questions_v2")
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key_p in ("senior", "manager", "gm"):
        fb = DEFAULT_PEER_QUESTIONS_BY_TARGET.get(key_p, [])
        if isinstance(saved, dict) and key_p in saved:
            out[key_p] = normalize_questions_list(saved.get(key_p), fb)
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
    st.markdown(f"##### {section_title}")
    tot_pts = round_half_up_2(sum(float(x.get("points") or 0) for x in q_list))
    st.caption(f"عدد الأسئلة: **{len(q_list)}** | إجمالي درجات الأسئلة: **{tot_pts:g}**")

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


def _render_printable_and_pdf_actions(
    doc_title: str,
    subtitle_text: str,
    table_rows: List[Dict[str, Any]],
    pdf_file_name: str,
    summary_rows: Optional[List[Tuple[str, str]]],
    key_suffix: str,
) -> None:
    if not table_rows:
        return
    cols_keys = list(table_rows[0].keys())
    hdr_html = "".join(f"<th>{c_k}</th>" for c_k in cols_keys)
    bdy_html = ""
    for idx_r, r_item in enumerate(table_rows):
        is_tot = str(r_item.get("البند") or "") == "الإجمالي" and idx_r == len(table_rows) - 1
        tr_cls = " class='tot-row'" if is_tot else ""
        bdy_html += f"<tr{tr_cls}>" + "".join(f"<td>{r_item[c_k]}</td>" for c_k in cols_keys) + "</tr>"

    print_doc_json = json.dumps(
        f"<!DOCTYPE html><html dir='rtl' lang='ar'><head><meta charset='utf-8'><title>{doc_title}</title>"
        "<style>"
        "body{font-family:'Cairo',Tahoma,sans-serif;direction:rtl;margin:28px;color:#0f172a;}"
        ".hdr{font-size:20px;font-weight:700;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid #0f4c5c;}"
        "table{width:100%;border-collapse:collapse;margin-top:10px;}"
        "th,td{border:1px solid #334155;padding:10px;text-align:center;font-size:14px;}"
        "th{background:#f1f5f9;font-weight:700;}"
        ".tot-row td{background:#e2e8f0;font-weight:700;font-size:15px;}"
        "</style></head><body>"
        f"<div class='hdr'>{doc_title}</div>"
        f"<table><thead><tr>{hdr_html}</tr></thead><tbody>{bdy_html}</tbody></table>"
        "</body></html>"
    )

    pdf_rows_list: List[List[str]] = [
        [str(r_item.get(c_k, "")) for c_k in cols_keys]
        for r_item in table_rows
    ]
    pdf_bytes = build_arabic_table_pdf_bytes(
        report_title=doc_title,
        subtitle_text=subtitle_text,
        headers=cols_keys,
        rows=pdf_rows_list,
        summary_rows=summary_rows,
    )

    p_col_a, p_col_b = st.columns([1.5, 1.5])
    with p_col_a:
        st.components.v1.html(
            f"""
            <div style="direction:rtl;text-align:right;">
                <button onclick="var w=window.open('','_blank','width=950,height=700');w.document.write({print_doc_json});w.document.close();w.focus();setTimeout(function(){{w.print();}},300);"
                        style="background:#0f4c5c;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-family:'Cairo',sans-serif;font-size:14px;font-weight:700;cursor:pointer;">
                    🖨️ طباعة في ورقة مستقلة
                </button>
            </div>
            """,
            height=50,
        )
    with p_col_b:
        st.download_button(
            "📄 تصدير PDF",
            data=pdf_bytes,
            file_name=pdf_file_name,
            mime="application/pdf",
            key=f"dl_kpi_pdf_{key_suffix}",
        )


def render_kpi_settings_tab(org_settings: Dict[str, Any]) -> None:
    """تبويب إعدادات وأسئلة الـ KPIs داخل شاشة الصلاحيات والإعدادات."""
    eff_org = get_effective_org_state(org_settings)
    st.markdown("#### ⚙️ إعدادات الـ KPIs")

    cur_cycle_val = str(eff_org.get("kpi_cycle_type") or "نصف سنوي")
    cur_start_dt = parse_date_safe(eff_org.get("kpi_start_date")) or date(date.today().year, 1, 1)
    cur_end_dt = parse_date_safe(eff_org.get("kpi_end_date")) or date.today()
    cur_active_val = bool(eff_org.get("kpi_evaluation_active", False))

    kc1, kc2, kc3, kc4 = st.columns([1.8, 1.8, 1.8, 1.3])
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
        sel_kpi_end = st.date_input("تاريخ انتهاء التقييم", value=cur_end_dt, key="kpi_cfg_end_dt")
    with kc4:
        st.write("")
        st.write("")
        sel_kpi_active = st.checkbox("تفعيل التقييم", value=cur_active_val, key="kpi_cfg_active_chk")

    if st.button("💾 حفظ إعدادات فترة التقييم", type="primary", key="save_kpi_cfg_period_btn"):
        save_org_settings_patch({
            "kpi_cycle_type": sel_cycle,
            "kpi_period_label": f"{sel_cycle} ({sel_kpi_start.isoformat()} إلى {sel_kpi_end.isoformat()})",
            "kpi_start_date": sel_kpi_start.isoformat(),
            "kpi_end_date": sel_kpi_end.isoformat(),
            "kpi_evaluation_active": bool(sel_kpi_active),
        })
        st.success("✅ تم حفظ إعدادات فترة التقييم.")
        st.rerun()

    st.divider()
    st.markdown("#### 📝 أسئلة التقييم")

    self_q_all = get_all_self_questions(eff_org)
    peer_q_all = get_all_peer_questions(eff_org)
    cfg_tabs = st.tabs(KPI_CONFIG_TABS)

    for idx_t, t_name in enumerate(KPI_CONFIG_TABS):
        with cfg_tabs[idx_t]:
            if t_name in ("استقبال", "استقبال وسحب عينات", "كول سنتر", "كيميائيين"):
                _render_q_editor_block(
                    section_title=f"أسئلة تقييم ({t_name}) لنفسه",
                    q_list=list(self_q_all.get(t_name) or []),
                    form_key=f"add_self_q_form_{t_name}",
                    del_prefix=f"del_self_q_{t_name}",
                    on_change_callback=lambda upd, s_all=self_q_all, tk=t_name: save_org_settings_patch(
                        {"kpi_self_questions": {**s_all, tk: upd}}
                    ),
                )
            elif t_name == "سينيور":
                _render_q_editor_block(
                    section_title="أسئلة تقييم السينيور لنفسه (وتقييم الإدارة له)",
                    q_list=list(self_q_all.get("سينيور") or []),
                    form_key="add_self_q_form_senior",
                    del_prefix="del_self_q_senior",
                    on_change_callback=lambda upd, s_all=self_q_all: save_org_settings_patch(
                        {"kpi_self_questions": {**s_all, "سينيور": upd}}
                    ),
                )
                st.divider()
                _render_q_editor_block(
                    section_title="الأسئلة الخاصة بتقييم الموظفين للسينيور",
                    q_list=list(peer_q_all.get("senior") or []),
                    form_key="add_sen_peer_q_form_tab",
                    del_prefix="del_sen_peer_q_tab",
                    on_change_callback=lambda upd, p_all=peer_q_all: save_org_settings_patch(
                        {"kpi_peer_questions_v2": {**p_all, "senior": upd}}
                    ),
                )
            elif t_name == "مدير مباشر":
                _render_q_editor_block(
                    section_title="أسئلة تقييم المدير المباشر لنفسه (وتقييم المدير العام له)",
                    q_list=list(self_q_all.get("مدير مباشر") or []),
                    form_key="add_self_q_form_manager",
                    del_prefix="del_self_q_manager",
                    on_change_callback=lambda upd, s_all=self_q_all: save_org_settings_patch(
                        {"kpi_self_questions": {**s_all, "مدير مباشر": upd}}
                    ),
                )
                st.divider()
                _render_q_editor_block(
                    section_title="الأسئلة الخاصة بتقييم الموظفين للمدير المباشر",
                    q_list=list(peer_q_all.get("manager") or []),
                    form_key="add_mgr_peer_q_form_tab",
                    del_prefix="del_mgr_peer_q_tab",
                    on_change_callback=lambda upd, p_all=peer_q_all: save_org_settings_patch(
                        {"kpi_peer_questions_v2": {**p_all, "manager": upd}}
                    ),
                )
            elif t_name == "مدير عام":
                _render_q_editor_block(
                    section_title="الأسئلة الخاصة بتقييم الموظفين للمدير العام",
                    q_list=list(peer_q_all.get("gm") or []),
                    form_key="add_gm_peer_q_form_tab",
                    del_prefix="del_gm_peer_q_tab",
                    on_change_callback=lambda upd, p_all=peer_q_all: save_org_settings_patch(
                        {"kpi_peer_questions_v2": {**p_all, "gm": upd}}
                    ),
                )


def _render_slider_rating_form(
    q_items: List[Dict[str, Any]],
    key_prefix: str,
    save_btn_label: str,
    save_btn_key: str,
    on_save_callback: Any,
) -> None:
    max_possible = round_half_up_2(sum(float(item.get("points") or 0) for item in q_items))
    scores: Dict[str, float] = {}

    for idx_q, item in enumerate(q_items):
        q_label = str(item.get("question") or "")
        q_pts = max(0.5, float(item.get("points") or 10.0))
        val_q = st.slider(
            f"{q_label} (الحد الأقصى: {q_pts:g})",
            min_value=0.0,
            max_value=float(q_pts),
            value=float(min(q_pts, round_half_up_2(q_pts * 0.8))),
            step=0.5,
            key=f"{key_prefix}_{idx_q}",
        )
        scores[q_label] = float(val_q)

    earned_total = round_half_up_2(sum(scores.values()))
    pct = compute_score_percentage(earned_total, max_possible)

    m1, m2 = st.columns(2)
    m1.metric("مجموع الدرجات", f"{earned_total:g} / {max_possible:g}")
    m2.metric("النسبة المئوية", f"{pct:g}%")

    if st.button(save_btn_label, type="primary", key=save_btn_key):
        on_save_callback(scores, earned_total, max_possible, pct)


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
        for k in get_emp_identifiers(e):
            emp_by_any_id[k] = e

    eff_org = get_effective_org_state(org_settings)
    kpi_active = bool(eff_org.get("kpi_evaluation_active", False))
    eval_cycle_type = str(eff_org.get("kpi_cycle_type") or "نصف سنوي")
    kpi_start_dt = parse_date_safe(eff_org.get("kpi_start_date")) or date(date.today().year, 1, 1)
    kpi_end_dt = parse_date_safe(eff_org.get("kpi_end_date")) or date.today()
    period_lbl = f"{eval_cycle_type} ({kpi_start_dt.isoformat()} إلى {kpi_end_dt.isoformat()})"

    if not kpi_active and user_role not in ("general_manager", "hr"):
        st.warning("فترة تقييم الأداء (KPIs) غير مفعّلة حالياً.")
        return

    st.caption(
        f"فترة التقييم: **{eval_cycle_type}** | من **`{kpi_start_dt.isoformat()}`** إلى **`{kpi_end_dt.isoformat()}`**"
    )

    self_q_by_tab = get_all_self_questions(eff_org)
    peer_q_by_target = get_all_peer_questions(eff_org)

    # --- حالة المدير العام: تقييم الموظفين + التقارير الأربعة ---
    if user_role == "general_manager":
        gm_tabs = st.tabs([
            "👥 تقييم الموظفين",
            "📄 تقرير الموظف",
            "👨‍🔬 تقرير السينيور",
            "👔 تقرير المدير المباشر",
            "🏢 تقرير المدير العام",
        ])

        with gm_tabs[0]:
            st.markdown("#### تقييم الموظفين")
            rateable_emps = [
                e for e in employees
                if (e.get("status") or "active") == "active"
                and str(e.get("role") or "").strip().lower() != "general_manager"
            ]
            if not rateable_emps:
                st.info("لا يوجد موظفون متاحون للتقييم حالياً.")
            else:
                remp_map = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in rateable_emps}
                sel_remp_lbl = st.selectbox("اختر الموظف", list(remp_map.keys()), key="gm_rate_emp_sel")
                picked_remp = remp_map[sel_remp_lbl]
                remp_tab_cat = map_job_title_to_self_tab(picked_remp)
                st.caption(f"الموظف: **{picked_remp.get('name_ar')}** — الفئة: **`{remp_tab_cat}`**")

                def _save_gm_emp_eval(sc_map: Dict[str, float], _tot: float, _max: float, pct_v: float) -> None:
                    upsert_record(
                        "kpi_evaluations",
                        {
                            "employee_id": picked_remp["id"],
                            "manager_id": user_id,
                            "period_label": f"{period_lbl} | مدير مباشر ({remp_tab_cat})",
                            "period_start": kpi_start_dt.isoformat(),
                            "period_end": kpi_end_dt.isoformat(),
                            "manager_scores": sc_map,
                            "manager_percentage": pct_v,
                            "status": "peer_manager_evaluated",
                        },
                        on_conflict="employee_id,period_label",
                    )
                    st.success(f"✅ تم حفظ تقييم ({picked_remp.get('name_ar')}) بنجاح.")
                    st.rerun()

                _render_slider_rating_form(
                    q_items=self_q_by_tab.get(remp_tab_cat) or [],
                    key_prefix=f"gm_rate_sl_{picked_remp['id']}",
                    save_btn_label="💾 حفظ التقييم",
                    save_btn_key=f"save_gm_rate_emp_{picked_remp['id']}",
                    on_save_callback=_save_gm_emp_eval,
                )

        with gm_tabs[1]:
            st.markdown("#### تقرير الموظف")
            report_emps = [
                e for e in employees
                if (e.get("status") or "active") == "active"
                and str(e.get("role") or "").strip().lower() != "general_manager"
            ]
            if not report_emps:
                st.info("لا توجد بيانات موظفين متاحة.")
            else:
                rep_emp_map = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in report_emps}
                sel_rep_emp_lbl = st.selectbox("اختر الموظف", list(rep_emp_map.keys()), key="gm_kpi_rep_emp_sel")
                target_rep_emp = rep_emp_map[sel_rep_emp_lbl]
                t_emp_ids = get_emp_identifiers(target_rep_emp)

                self_eval_rec: Optional[Dict[str, Any]] = None
                mgr_eval_rec: Optional[Dict[str, Any]] = None

                for ev in kpi_evaluations:
                    if str(ev.get("employee_id") or "") not in t_emp_ids:
                        continue
                    st_ev = str(ev.get("status") or "").strip().lower()
                    lbl_ev = str(ev.get("period_label") or "")
                    if st_ev == "submitted_by_employee" or "ذاتي" in lbl_ev:
                        if self_eval_rec is None:
                            self_eval_rec = ev
                    elif "مدير مباشر" in lbl_ev or st_ev == "peer_manager_evaluated":
                        if mgr_eval_rec is None:
                            mgr_eval_rec = ev

                self_scores_map: Dict[str, float] = (self_eval_rec or {}).get("self_scores") or {}
                mgr_scores_map: Dict[str, float] = (mgr_eval_rec or {}).get("manager_scores") or {}

                cat_for_emp = map_job_title_to_self_tab(target_rep_emp)
                emp_own_questions = [
                    str(x.get("question") or "")
                    for x in (self_q_by_tab.get(cat_for_emp) or [])
                    if str(x.get("question") or "").strip()
                ]

                rows_data: List[Dict[str, Any]] = []
                tot_self_val = 0.0
                tot_mgr_val = 0.0

                for crit_q in emp_own_questions:
                    s_val = round_half_up_2(float(self_scores_map.get(crit_q) or 0.0))
                    m_val = round_half_up_2(float(mgr_scores_map.get(crit_q) or 0.0))
                    tot_self_val = round_half_up_2(tot_self_val + s_val)
                    tot_mgr_val = round_half_up_2(tot_mgr_val + m_val)
                    rows_data.append({
                        "البند": crit_q,
                        "تقييم الموظف لنفسه": s_val,
                        "تقييم المدير المباشر": m_val,
                    })

                total_row: Dict[str, Any] = {
                    "البند": "الإجمالي",
                    "تقييم الموظف لنفسه": tot_self_val,
                    "تقييم المدير المباشر": tot_mgr_val,
                }

                st.markdown(f"### الاسم: {target_rep_emp.get('name_ar')}")
                st.dataframe(rows_data + [total_row], use_container_width=True, hide_index=True)

                det = build_full_employee_kpi_report_data(
                    emp=target_rep_emp,
                    start_date=kpi_start_dt,
                    end_date=kpi_end_dt,
                    leave_requests=leave_requests,
                    attendance_logs=attendance_logs,
                    penalties_rewards=penalties_rewards,
                    advances=advances,
                )

                st.divider()
                st.markdown("#### 📌 السجل التفصيلي للفترة (الإجازات، المكافآت، الجزاءات، السلف، الإضافي، التأخيرات)")

                m_top1, m_top2, m_top3, m_top4, m_top5 = st.columns(5)
                m_top1.metric("الإجازات (أيام)", det["total_leave_days"])
                m_top2.metric("المكافآت (مرات)", det["rewards_count"])
                m_top3.metric("الجزاءات (مرات)", det["penalties_count"])
                m_top4.metric("السلف (ج)", f"{det['advances_total_amt']:,.2f}")
                m_top5.metric("الإضافي / التأخيرات", f"{det['total_ot_days']} ي / {det['total_late_cnt']} ت")

                st.caption(f"تفصيل الإجازات: عارضة (**{det['casual_days']}** يوم) — اعتيادي (**{det['regular_days']}** يوم)")

                if det["monthly_summary_rows"]:
                    st.markdown("**التوزيع بالشهور خلال فترة التقييم:**")
                    st.dataframe(det["monthly_summary_rows"], use_container_width=True, hide_index=True)

                if det["leave_details"]:
                    st.markdown("**تفصيل الإجازات:**")
                    st.dataframe(det["leave_details"], use_container_width=True, hide_index=True)
                if det["rewards_list"]:
                    st.markdown("**تفصيل المكافآت:**")
                    st.dataframe(det["rewards_list"], use_container_width=True, hide_index=True)
                if det["penalties_list"]:
                    st.markdown("**تفصيل الجزاءات:**")
                    st.dataframe(det["penalties_list"], use_container_width=True, hide_index=True)
                if det["advances_list"]:
                    st.markdown("**تفصيل السلف:**")
                    st.dataframe(det["advances_list"], use_container_width=True, hide_index=True)
                if det["ot_list"]:
                    st.markdown("**تفصيل الوقت الإضافي:**")
                    st.dataframe(det["ot_list"], use_container_width=True, hide_index=True)
                if det["late_list"]:
                    st.markdown("**تفصيل التأخيرات:**")
                    st.dataframe(det["late_list"], use_container_width=True, hide_index=True)

                emp_summary_pdf: List[Tuple[str, str]] = [
                    ("الرقم الوظيفي", str(target_rep_emp.get("employee_no") or "—")),
                    ("الإجازات (أيام)", f"{det['total_leave_days']} (عارضة: {det['casual_days']} | اعتيادي: {det['regular_days']})"),
                    ("المكافآت / الجزاءات", f"مكافآت: {det['rewards_count']} | جزاءات: {det['penalties_count']}"),
                    ("السلف / الإضافي / التأخيرات", f"سلف: {det['advances_total_amt']:,.2f} ج | إضافي: {det['total_ot_days']} ي | تأخيرات: {det['total_late_cnt']}"),
                ]

                _render_printable_and_pdf_actions(
                    doc_title=f"الاسم: {target_rep_emp.get('name_ar')}",
                    subtitle_text=f"الفترة: {period_lbl}",
                    table_rows=rows_data + [total_row],
                    pdf_file_name=f"kpi_employee_{target_rep_emp.get('employee_no', 'emp')}.pdf",
                    summary_rows=emp_summary_pdf,
                    key_suffix=f"emp_{target_rep_emp['id']}",
                )

        with gm_tabs[2]:
            st.markdown("#### تقرير السينيور")
            seniors_only = [
                e for e in employees
                if (e.get("status") or "active") == "active"
                and str(e.get("role") or "").strip().lower() == "senior"
            ]
            if not seniors_only:
                st.info("لا يوجد سينيور مسجل في النظام حالياً.")
            else:
                sen_sel_map = {f"{s.get('name_ar')} (#{s.get('employee_no')})": s for s in seniors_only}
                picked_sen_lbl = st.selectbox("اختر السينيور", list(sen_sel_map.keys()), key="gm_rep_sen_filter_sel")
                picked_sen_obj = sen_sel_map[picked_sen_lbl]
                picked_sen_ids = get_emp_identifiers(picked_sen_obj)

                rater_cols_sen: Dict[str, Dict[str, float]] = {}
                rater_counter_s = 0
                for ev in kpi_evaluations:
                    if str(ev.get("employee_id") or "") not in picked_sen_ids:
                        continue
                    lbl_e = str(ev.get("period_label") or "")
                    st_e = str(ev.get("status") or "").strip().lower()
                    if "تقييم سينيور" not in lbl_e and st_e != "peer_senior_evaluated":
                        continue
                    rater_counter_s += 1
                    r_emp = emp_by_any_id.get(str(ev.get("manager_id") or ""))
                    r_disp_name = f"موظف {rater_counter_s} ({r_emp.get('name_ar')})" if r_emp else f"موظف {rater_counter_s}"
                    sc_d = ev.get("manager_scores") or {}
                    if isinstance(sc_d, dict) and r_disp_name not in rater_cols_sen:
                        rater_cols_sen[r_disp_name] = {str(k): float(v or 0.0) for k, v in sc_d.items()}

                if not rater_cols_sen:
                    st.info(f"لا توجد تقييمات مسجلة من الموظفين للسينيور ({picked_sen_obj.get('name_ar')}) بعد.")
                else:
                    mat_s = build_peer_matrix_table(peer_q_by_target.get("senior") or [], rater_cols_sen)
                    st.dataframe(mat_s["rows"], use_container_width=True, hide_index=True)
                    st.metric("الإجمالي الكلي", f"{mat_s['grand_total']:g}")
                    _render_printable_and_pdf_actions(
                        doc_title=f"تقرير تقييم السينيور: {picked_sen_obj.get('name_ar')}",
                        subtitle_text=f"الفترة: {period_lbl} | الإجمالي الكلي: {mat_s['grand_total']:g}",
                        table_rows=mat_s["rows"],
                        pdf_file_name="kpi_senior_report.pdf",
                        summary_rows=[("الإجمالي الكلي", f"{mat_s['grand_total']:g}")],
                        key_suffix="senior_rep",
                    )

        with gm_tabs[3]:
            st.markdown("#### تقرير المدير المباشر")
            managers_only = [
                e for e in employees
                if (e.get("status") or "active") == "active"
                and str(e.get("role") or "").strip().lower() == "manager"
            ]
            if not managers_only:
                st.info("لا يوجد مدير مباشر مسجل في النظام حالياً.")
            else:
                mgr_sel_map = {f"{m.get('name_ar')} (#{m.get('employee_no')})": m for m in managers_only}
                picked_mgr_lbl = st.selectbox("اختر المدير المباشر", list(mgr_sel_map.keys()), key="gm_rep_mgr_filter_sel")
                picked_mgr_obj = mgr_sel_map[picked_mgr_lbl]
                picked_mgr_ids = get_emp_identifiers(picked_mgr_obj)

                rater_cols_mgr: Dict[str, Dict[str, float]] = {}
                rater_counter_m = 0
                for ev in kpi_evaluations:
                    if str(ev.get("employee_id") or "") not in picked_mgr_ids:
                        continue
                    lbl_e = str(ev.get("period_label") or "")
                    st_e = str(ev.get("status") or "").strip().lower()
                    if "تقييم مدير مباشر" not in lbl_e and st_e != "peer_manager_evaluated":
                        continue
                    rater_counter_m += 1
                    r_emp = emp_by_any_id.get(str(ev.get("manager_id") or ""))
                    r_disp_name = f"موظف {rater_counter_m} ({r_emp.get('name_ar')})" if r_emp else f"موظف {rater_counter_m}"
                    sc_d = ev.get("manager_scores") or {}
                    if isinstance(sc_d, dict) and r_disp_name not in rater_cols_mgr:
                        rater_cols_mgr[r_disp_name] = {str(k): float(v or 0.0) for k, v in sc_d.items()}

                if not rater_cols_mgr:
                    st.info(f"لا توجد تقييمات مسجلة من الموظفين للمدير المباشر ({picked_mgr_obj.get('name_ar')}) بعد.")
                else:
                    mat_m = build_peer_matrix_table(peer_q_by_target.get("manager") or [], rater_cols_mgr)
                    st.dataframe(mat_m["rows"], use_container_width=True, hide_index=True)
                    st.metric("الإجمالي الكلي", f"{mat_m['grand_total']:g}")
                    _render_printable_and_pdf_actions(
                        doc_title=f"تقرير تقييم المدير المباشر: {picked_mgr_obj.get('name_ar')}",
                        subtitle_text=f"الفترة: {period_lbl} | الإجمالي الكلي: {mat_m['grand_total']:g}",
                        table_rows=mat_m["rows"],
                        pdf_file_name="kpi_manager_report.pdf",
                        summary_rows=[("الإجمالي الكلي", f"{mat_m['grand_total']:g}")],
                        key_suffix="manager_rep",
                    )

        with gm_tabs[4]:
            st.markdown("#### تقرير المدير العام")
            rater_cols_gm: Dict[str, Dict[str, float]] = {}
            rater_counter_g = 0
            for ev in kpi_evaluations:
                lbl_e = str(ev.get("period_label") or "")
                st_e = str(ev.get("status") or "").strip().lower()
                if "تقييم مدير عام" not in lbl_e and st_e != "peer_gm_evaluated":
                    continue
                rater_counter_g += 1
                r_emp = emp_by_any_id.get(str(ev.get("manager_id") or ""))
                r_disp_name = f"موظف {rater_counter_g} ({r_emp.get('name_ar')})" if r_emp else f"موظف {rater_counter_g}"
                sc_d = ev.get("manager_scores") or {}
                if isinstance(sc_d, dict) and r_disp_name not in rater_cols_gm:
                    rater_cols_gm[r_disp_name] = {str(k): float(v or 0.0) for k, v in sc_d.items()}

            if not rater_cols_gm:
                st.info("لا توجد تقييمات مسجلة من الموظفين للمدير العام بعد.")
            else:
                mat_g = build_peer_matrix_table(peer_q_by_target.get("gm") or [], rater_cols_gm)
                st.dataframe(mat_g["rows"], use_container_width=True, hide_index=True)
                st.metric("الإجمالي الكلي", f"{mat_g['grand_total']:g}")
                _render_printable_and_pdf_actions(
                    doc_title="تقرير تقييم المدير العام",
                    subtitle_text=f"الفترة: {period_lbl} | الإجمالي الكلي: {mat_g['grand_total']:g}",
                    table_rows=mat_g["rows"],
                    pdf_file_name="kpi_gm_report.pdf",
                    summary_rows=[("الإجمالي الكلي", f"{mat_g['grand_total']:g}")],
                    key_suffix="gm_rep",
                )
        return

    # --- المستخدمون غير المدير العام ---
    has_my_senior = bool(str(current_user.get("senior_id") or "").strip())
    my_mgr_id_str = str(current_user.get("direct_manager_id") or "").strip()
    my_mgr_emp = emp_by_any_id.get(my_mgr_id_str)
    mgr_is_gm = bool(my_mgr_emp and str(my_mgr_emp.get("role") or "").strip().lower() == "general_manager")

    user_sub_tabs: List[str] = ["🙋 تقييم نفسي"]
    if has_my_senior:
        user_sub_tabs.append("👨‍🔬 تقييم السينيور")
    if user_role == "manager":
        user_sub_tabs.append("👥 تقييم الموظفين")
    if not mgr_is_gm and my_mgr_emp:
        user_sub_tabs.append("👔 تقييم المدير المباشر")
    user_sub_tabs.append("🏢 تقييم المدير العام")

    sub_tabs = st.tabs(user_sub_tabs)
    st_idx = 0

    with sub_tabs[st_idx]:
        mapped_tab = map_job_title_to_self_tab(current_user)
        st.markdown(f"#### التقييم الذاتي — **{mapped_tab}**")

        det_self = build_full_employee_kpi_report_data(
            emp=current_user,
            start_date=kpi_start_dt,
            end_date=kpi_end_dt,
            leave_requests=leave_requests,
            attendance_logs=attendance_logs,
            penalties_rewards=penalties_rewards,
            advances=advances,
        )

        def _save_self_eval(sc_map: Dict[str, float], earned_t: float, max_p: float, pct_v: float) -> None:
            auto_sum = build_employee_period_summary(
                employee=current_user,
                start_date=kpi_start_dt,
                end_date=kpi_end_dt,
                leave_requests=leave_requests,
                attendance_logs=attendance_logs,
                penalties_rewards=penalties_rewards,
                advances=advances,
            )
            upsert_record(
                "kpi_evaluations",
                {
                    "employee_id": current_user["id"],
                    "manager_id": user_id,
                    "period_label": f"{period_lbl} | ذاتي ({mapped_tab})",
                    "period_start": kpi_start_dt.isoformat(),
                    "period_end": kpi_end_dt.isoformat(),
                    "self_scores": sc_map,
                    "self_percentage": pct_v,
                    "auto_summary": auto_sum,
                    "status": "submitted_by_employee",
                },
                on_conflict="employee_id,period_label",
            )
            write_audit_log(
                "kpi_evaluations",
                "save_kpi_self",
                "تقييم",
                f"حفظ تقييم ذاتي ({earned_t:g}/{max_p:g} = {pct_v:g}%) للموظف {current_user.get('name_ar')}",
                by_name=current_user.get("name_ar", ""),
                by_user_id=user_id,
            )
            st.success("✅ تم حفظ التقييم الذاتي بنجاح.")
            st.rerun()

        _render_slider_rating_form(
            q_items=self_q_by_tab.get(mapped_tab) or [],
            key_prefix=f"kpi_self_sl_{user_id}_{mapped_tab}",
            save_btn_label="💾 حفظ التقييم الذاتي",
            save_btn_key="save_kpi_self_btn",
            on_save_callback=_save_self_eval,
        )

        st.divider()
        st.markdown("##### 📊 السجل الآلي لفترة التقييم")
        s1, s2, s3, s4, s5, s6 = st.columns(6)
        s1.metric("الإجازات (أيام)", det_self["total_leave_days"])
        s2.metric("المكافآت (مرات)", det_self["rewards_count"])
        s3.metric("الجزاءات (مرات)", det_self["penalties_count"])
        s4.metric("السلف (ج)", f"{det_self['advances_total_amt']:,.2f}")
        s5.metric("الإضافي (أيام)", det_self["total_ot_days"])
        s6.metric("التأخيرات (مرات)", det_self["total_late_cnt"])
    st_idx += 1

    if has_my_senior:
        with sub_tabs[st_idx]:
            sen_emp = emp_by_any_id.get(str(current_user.get("senior_id") or "").strip())
            if sen_emp:
                st.markdown(f"#### تقييم السينيور: **{sen_emp.get('name_ar')}**")

                def _save_sen_eval(sc_map: Dict[str, float], _tot: float, _max: float, pct_v: float) -> None:
                    upsert_record(
                        "kpi_evaluations",
                        {
                            "employee_id": sen_emp["id"],
                            "manager_id": user_id,
                            "period_label": f"{period_lbl} | تقييم سينيور بواسطة {current_user.get('name_ar')}",
                            "period_start": kpi_start_dt.isoformat(),
                            "period_end": kpi_end_dt.isoformat(),
                            "manager_scores": sc_map,
                            "manager_percentage": pct_v,
                            "status": "peer_senior_evaluated",
                        },
                        on_conflict="employee_id,period_label",
                    )
                    st.success("✅ تم حفظ تقييم السينيور بنجاح.")
                    st.rerun()

                _render_slider_rating_form(
                    q_items=peer_q_by_target.get("senior") or [],
                    key_prefix=f"kpi_rate_sen_{user_id}_{sen_emp['id']}",
                    save_btn_label="💾 حفظ تقييم السينيور",
                    save_btn_key="save_kpi_rate_sen_btn",
                    on_save_callback=_save_sen_eval,
                )
        st_idx += 1

    if user_role == "manager":
        with sub_tabs[st_idx]:
            st.markdown("#### تقييم الموظفين التابعين")
            subs_list = [
                e for e in employees
                if (e.get("status") or "active") == "active"
                and str(e.get("id") or "") != user_id
                and str(e.get("role") or "").strip().lower() != "general_manager"
            ]
            if not subs_list:
                st.info("لا يوجد موظفون تابعون لك لتقييمهم حالياً.")
            else:
                sub_map = {f"{e.get('name_ar')} (#{e.get('employee_no')})": e for e in subs_list}
                sel_sub_lbl = st.selectbox("اختر الموظف", list(sub_map.keys()), key="mgr_rate_sub_emp_sel")
                picked_sub_emp = sub_map[sel_sub_lbl]
                auto_sub_cat = map_job_title_to_self_tab(picked_sub_emp)
                st.caption(f"الموظف: **{picked_sub_emp.get('name_ar')}** — الفئة: **`{auto_sub_cat}`**")

                def _save_mgr_sub_eval(sc_map: Dict[str, float], earned_t: float, max_p: float, pct_v: float) -> None:
                    upsert_record(
                        "kpi_evaluations",
                        {
                            "employee_id": picked_sub_emp["id"],
                            "manager_id": user_id,
                            "period_label": f"{period_lbl} | مدير مباشر ({auto_sub_cat})",
                            "period_start": kpi_start_dt.isoformat(),
                            "period_end": kpi_end_dt.isoformat(),
                            "manager_scores": sc_map,
                            "manager_percentage": pct_v,
                            "status": "peer_manager_evaluated",
                        },
                        on_conflict="employee_id,period_label",
                    )
                    write_audit_log(
                        "kpi_evaluations",
                        "save_kpi_mgr_sub",
                        "تقييم",
                        f"تقييم المدير المباشر للموظف {picked_sub_emp.get('name_ar')} ({earned_t:g}/{max_p:g})",
                        by_name=current_user.get("name_ar", ""),
                        by_user_id=user_id,
                    )
                    st.success(f"✅ تم حفظ تقييم الموظف {picked_sub_emp.get('name_ar')} بنجاح.")
                    st.rerun()

                _render_slider_rating_form(
                    q_items=self_q_by_tab.get(auto_sub_cat) or [],
                    key_prefix=f"mgr_rate_sub_sl_{picked_sub_emp['id']}_{auto_sub_cat}",
                    save_btn_label="💾 حفظ تقييم الموظف",
                    save_btn_key=f"save_mgr_rate_sub_{picked_sub_emp['id']}",
                    on_save_callback=_save_mgr_sub_eval,
                )
        st_idx += 1

    if not mgr_is_gm and my_mgr_emp:
        with sub_tabs[st_idx]:
            st.markdown(f"#### تقييم المدير المباشر: **{my_mgr_emp.get('name_ar')}**")

            def _save_mgr_eval(sc_map: Dict[str, float], _tot: float, _max: float, pct_v: float) -> None:
                upsert_record(
                    "kpi_evaluations",
                    {
                        "employee_id": my_mgr_emp["id"],
                        "manager_id": user_id,
                        "period_label": f"{period_lbl} | تقييم مدير مباشر بواسطة {current_user.get('name_ar')}",
                        "period_start": kpi_start_dt.isoformat(),
                        "period_end": kpi_end_dt.isoformat(),
                        "manager_scores": sc_map,
                        "manager_percentage": pct_v,
                        "status": "peer_manager_evaluated",
                    },
                    on_conflict="employee_id,period_label",
                )
                st.success("✅ تم حفظ تقييم المدير المباشر بنجاح.")
                st.rerun()

            _render_slider_rating_form(
                q_items=peer_q_by_target.get("manager") or [],
                key_prefix=f"kpi_rate_mgr_{user_id}_{my_mgr_emp['id']}",
                save_btn_label="💾 حفظ تقييم المدير المباشر",
                save_btn_key="save_kpi_rate_mgr_btn",
                on_save_callback=_save_mgr_eval,
            )
        st_idx += 1

    with sub_tabs[st_idx]:
        gm_emp = next((e for e in employees if str(e.get("role") or "").lower() == "general_manager"), None)
        gm_target_id = gm_emp["id"] if gm_emp else None
        gm_name = gm_emp.get("name_ar") if gm_emp else "المدير العام"

        st.markdown(f"#### تقييم المدير العام: **{gm_name}**")

        def _save_gm_eval(sc_map: Dict[str, float], _tot: float, _max: float, pct_v: float) -> None:
            if not gm_target_id:
                return
            upsert_record(
                "kpi_evaluations",
                {
                    "employee_id": gm_target_id,
                    "manager_id": user_id,
                    "period_label": f"{period_lbl} | تقييم مدير عام بواسطة {current_user.get('name_ar')}",
                    "period_start": kpi_start_dt.isoformat(),
                    "period_end": kpi_end_dt.isoformat(),
                    "manager_scores": sc_map,
                    "manager_percentage": pct_v,
                    "status": "peer_gm_evaluated",
                },
                on_conflict="employee_id,period_label",
            )
            st.success("✅ تم حفظ تقييم المدير العام بنجاح.")
            st.rerun()

        _render_slider_rating_form(
            q_items=peer_q_by_target.get("gm") or [],
            key_prefix=f"kpi_rate_gm_{user_id}",
            save_btn_label="💾 حفظ تقييم المدير العام",
            save_btn_key="save_kpi_rate_gm_btn",
            on_save_callback=_save_gm_eval,
        )
