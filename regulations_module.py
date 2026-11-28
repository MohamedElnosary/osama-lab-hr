from datetime import date, datetime
import io
from typing import Any, Dict, List
import streamlit as st

from db import delete_record, fetch_table, insert_record, update_record, write_audit_log
from rules_engine import get_effective_permissions, parse_date_safe


def _can_manage_regulations(current_user: Dict[str, Any]) -> bool:
    perms = get_effective_permissions(current_user)
    role = str(current_user.get("role") or "employee").strip().lower()
    return bool(
        perms.get("manage_settings")
        or perms.get("manage_payroll")
        or role in ("general_manager", "hr", "chairman", "vice_chairman")
    )


def _load_regulations_safe() -> List[Dict[str, Any]]:
    try:
        rows = fetch_table("regulations", order_by="created_at", desc=True, limit=500)
        if rows:
            return rows
    except Exception:
        pass

    cached = st.session_state.get("local_regulations_store")
    if isinstance(cached, list):
        return cached
    return []


def _save_regulation_record(
    title: str,
    category: str,
    content: str,
    effective_dt: date,
    current_user: Dict[str, Any],
) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    payload: Dict[str, Any] = {
        "title": title.strip(),
        "category": (category or "عام").strip(),
        "content": content.strip(),
        "effective_date": effective_dt.isoformat(),
        "active": True,
        "created_by": user_id,
    }
    try:
        insert_record("regulations", payload)
        write_audit_log(
            "regulations",
            "create_regulation",
            "إضافة",
            f"إضافة لائحة: {title.strip()}",
            by_name=by_name,
            by_user_id=user_id,
        )
    except Exception:
        local_list = list(st.session_state.get("local_regulations_store") or [])
        local_list.insert(
            0,
            {
                "id": f"reg_{datetime.utcnow().timestamp()}",
                "created_at": datetime.utcnow().isoformat() + "Z",
                **payload,
            },
        )
        st.session_state["local_regulations_store"] = local_list


def _update_regulation_record(
    reg_id: Any,
    title: str,
    category: str,
    content: str,
    effective_dt: date,
    current_user: Dict[str, Any],
) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    patch: Dict[str, Any] = {
        "title": title.strip(),
        "category": (category or "عام").strip(),
        "content": content.strip(),
        "effective_date": effective_dt.isoformat(),
    }
    try:
        update_record("regulations", reg_id, patch)
        write_audit_log(
            "regulations",
            "update_regulation",
            "تعديل",
            f"تعديل لائحة: {title.strip()}",
            by_name=by_name,
            by_user_id=user_id,
        )
    except Exception:
        local_list = list(st.session_state.get("local_regulations_store") or [])
        for idx, item in enumerate(local_list):
            if str(item.get("id")) == str(reg_id):
                local_list[idx] = {**item, **patch}
        st.session_state["local_regulations_store"] = local_list


def _delete_regulation_record(reg_id: Any, title: str, current_user: Dict[str, Any]) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    try:
        delete_record("regulations", reg_id)
        write_audit_log(
            "regulations",
            "delete_regulation",
            "حذف",
            f"حذف لائحة: {title}",
            by_name=by_name,
            by_user_id=user_id,
        )
    except Exception:
        local_list = [
            item for item in (st.session_state.get("local_regulations_store") or [])
            if str(item.get("id")) != str(reg_id)
        ]
        st.session_state["local_regulations_store"] = local_list


def _build_regulation_download_bytes(reg: Dict[str, Any], org_name: str) -> bytes:
    title = str(reg.get("title") or "لائحة").strip()
    category = str(reg.get("category") or "عام").strip()
    eff_dt = str(reg.get("effective_date") or "")[:10]
    content = str(reg.get("content") or "").strip()

    lines = [
        org_name,
        "=" * 50,
        f"اسم اللائحة: {title}",
        f"التصنيف: {category}",
        f"تاريخ السريان: {eff_dt or 'غير محدد'}",
        "-" * 50,
        content,
    ]
    buf = io.StringIO()
    buf.write("\ufeff" + "\n".join(lines))
    return buf.getvalue().encode("utf-8")


def render_regulations_module(
    current_user: Dict[str, Any],
    org_settings: Dict[str, Any],
) -> None:
    st.markdown("### 📜 اللوائح الداخلية للمعمل")
    org_name = str((org_settings or {}).get("org_name") or "معامل ا.د أسامة عبد المتعال منصور")
    can_manage = _can_manage_regulations(current_user)
    regulations = [r for r in _load_regulations_safe() if r.get("active", True) is not False]

    if can_manage:
        with st.expander("➕ إضافة لائحة جديدة", expanded=len(regulations) == 0):
            with st.form("create_regulation_form", clear_on_submit=True):
                c1, c2 = st.columns([2.5, 1.5])
                with c1:
                    reg_title = st.text_input("اسم اللائحة", placeholder="مثال: لائحة الحضور والانصراف")
                with c2:
                    reg_category = st.text_input("التصنيف / القسم", value="إدارية وفنية")

                reg_eff_date = st.date_input("تاريخ السريان", value=date.today())
                reg_content = st.text_area(
                    "نص وبنود اللائحة",
                    placeholder="اكتب بنود وتفاصيل اللائحة هنا...",
                    height=180,
                )

                if st.form_submit_button("حفظ اللائحة", type="primary"):
                    if not reg_title.strip():
                        st.error("يرجى إدخال اسم اللائحة.")
                    elif not reg_content.strip():
                        st.error("يرجى إدخال نص اللائحة.")
                    else:
                        _save_regulation_record(
                            title=reg_title,
                            category=reg_category,
                            content=reg_content,
                            effective_dt=reg_eff_date,
                            current_user=current_user,
                        )
                        st.success("✅ تم حفظ اللائحة وإتاحتها للموظفين.")
                        st.rerun()

    st.divider()
    st.markdown("#### 📚 اللوائح المعتمدة والمتاحة للتحميل")

    if not regulations:
        st.info("لا توجد لوائح مضافة حتى الآن.")
        return

    search_q = st.text_input("بحث باسم اللائحة أو التصنيف", placeholder="اكتب للبحث...", key="reg_search_inp").strip().lower()
    filtered_regs = [
        r for r in regulations
        if not search_q
        or search_q in str(r.get("title") or "").lower()
        or search_q in str(r.get("category") or "").lower()
        or search_q in str(r.get("content") or "").lower()
    ]

    if not filtered_regs:
        st.info("لا توجد لوائح مطابقة لبحثك.")
        return

    for idx_r, reg in enumerate(filtered_regs):
        r_title = str(reg.get("title") or "لائحة بدون عنوان")
        r_cat = str(reg.get("category") or "عام")
        r_eff = str(reg.get("effective_date") or "")[:10]
        r_content = str(reg.get("content") or "")

        with st.container(border=True):
            top_c1, top_c2 = st.columns([3.5, 1.5])
            top_c1.markdown(f"#### 📌 {r_title}")
            top_c2.caption(f"التصنيف: `{r_cat}` | السريان: `{r_eff or '—'}`")

            st.write(r_content)

            dl_bytes = _build_regulation_download_bytes(reg, org_name)
            safe_fname = "".join(ch for ch in r_title if ch.isalnum() or ch in (" ", "_", "-")).strip().replace(" ", "_") or "regulation"

            act_cols = st.columns([1.8, 1.2, 1.2, 3.8])
            with act_cols[0]:
                st.download_button(
                    "⬇️ تحميل اللائحة",
                    data=dl_bytes,
                    file_name=f"{safe_fname}.txt",
                    mime="text/plain; charset=utf-8",
                    key=f"dl_reg_{reg.get('id') or idx_r}_{idx_r}",
                    type="primary",
                )

            if can_manage and reg.get("id") is not None:
                with act_cols[2]:
                    if st.button("🗑️ حذف", key=f"del_reg_{reg.get('id') or idx_r}_{idx_r}"):
                        _delete_regulation_record(reg.get("id"), r_title, current_user)
                        st.rerun()

                with st.expander("✏️ تعديل اللائحة", expanded=False):
                    with st.form(f"edit_reg_form_{reg.get('id') or idx_r}_{idx_r}"):
                        u_title = st.text_input("اسم اللائحة", value=r_title, key=f"u_reg_t_{idx_r}")
                        u_cat = st.text_input("التصنيف", value=r_cat, key=f"u_reg_c_{idx_r}")
                        u_eff = st.date_input(
                            "تاريخ السريان",
                            value=parse_date_safe(reg.get("effective_date")) or date.today(),
                            key=f"u_reg_d_{idx_r}",
                        )
                        u_body = st.text_area("نص وبنود اللائحة", value=r_content, height=150, key=f"u_reg_b_{idx_r}")
                        if st.form_submit_button("💾 حفظ التعديل", type="primary"):
                            if u_title.strip() and u_body.strip():
                                _update_regulation_record(
                                    reg_id=reg.get("id"),
                                    title=u_title,
                                    category=u_cat,
                                    content=u_body,
                                    effective_dt=u_eff,
                                    current_user=current_user,
                                )
                                st.success("✅ تم تحديث اللائحة.")
                                st.rerun()
                            else:
                                st.error("يرجى إدخال اسم اللائحة ونصها.")
