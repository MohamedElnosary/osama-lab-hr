from datetime import date, datetime
import io
from typing import Any, Dict, List
import pandas as pd
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


def _normalize_items_table(raw_items: Any, fallback_content: str = "") -> List[Dict[str, Any]]:
    """تحويل بنود اللائحة إلى قائمة صفوف بثلاثة أعمدة: مسلسل، البند، مقدار البند أو التقدير."""
    rows: List[Dict[str, Any]] = []
    if isinstance(raw_items, list):
        for idx, item in enumerate(raw_items, start=1):
            if isinstance(item, dict):
                serial_val = item.get("مسلسل") if item.get("مسلسل") is not None else item.get("serial", idx)
                clause_val = str(item.get("البند") or item.get("clause") or item.get("question") or "").strip()
                amount_val = str(item.get("مقدار البند أو التقدير") or item.get("amount") or item.get("estimate") or "").strip()
                if clause_val or amount_val:
                    rows.append({
                        "مسلسل": serial_val,
                        "البند": clause_val,
                        "مقدار البند أو التقدير": amount_val,
                    })

    if not rows and str(fallback_content or "").strip():
        lines = [ln.strip() for ln in str(fallback_content).splitlines() if ln.strip()]
        for idx, ln in enumerate(lines, start=1):
            rows.append({
                "مسلسل": idx,
                "البند": ln,
                "مقدار البند أو التقدير": "",
            })

    if not rows:
        rows = [{"مسلسل": 1, "البند": "", "مقدار البند أو التقدير": ""}]
    return rows


def _clean_editable_rows(df_edited: pd.DataFrame) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    if df_edited is None or df_edited.empty:
        return cleaned
    auto_seq = 1
    for _, r in df_edited.iterrows():
        clause_txt = str(r.get("البند") or "").strip()
        amount_txt = str(r.get("مقدار البند أو التقدير") or "").strip()
        if not clause_txt and not amount_txt:
            continue
        raw_seq = r.get("مسلسل")
        try:
            seq_num = int(float(raw_seq)) if raw_seq is not None and str(raw_seq).strip() != "" else auto_seq
        except (ValueError, TypeError):
            seq_num = auto_seq
        cleaned.append({
            "مسلسل": seq_num,
            "البند": clause_txt,
            "مقدار البند أو التقدير": amount_txt,
        })
        auto_seq += 1
    return cleaned


def _save_regulation_record(
    title: str,
    category: str,
    items_rows: List[Dict[str, Any]],
    effective_dt: date,
    current_user: Dict[str, Any],
) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    text_summary = "\n".join(
        f"{it.get('مسلسل')}. {it.get('البند')} — {it.get('مقدار البند أو التقدير')}"
        for it in items_rows
    )
    payload: Dict[str, Any] = {
        "title": title.strip(),
        "category": (category or "لائحة").strip(),
        "content": text_summary,
        "items": items_rows,
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
            f"إضافة لائحة: {title.strip()} ({len(items_rows)} بند)",
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
    items_rows: List[Dict[str, Any]],
    effective_dt: date,
    current_user: Dict[str, Any],
) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    text_summary = "\n".join(
        f"{it.get('مسلسل')}. {it.get('البند')} — {it.get('مقدار البند أو التقدير')}"
        for it in items_rows
    )
    patch: Dict[str, Any] = {
        "title": title.strip(),
        "category": (category or "لائحة").strip(),
        "content": text_summary,
        "items": items_rows,
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


def _build_regulation_excel_bytes(reg: Dict[str, Any], org_name: str, items_rows: List[Dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        info_df = pd.DataFrame([
            {
                "المؤسسة": org_name,
                "اسم اللائحة": str(reg.get("title") or ""),
                "التصنيف": str(reg.get("category") or ""),
                "تاريخ السريان": str(reg.get("effective_date") or "")[:10],
            }
        ])
        info_df.to_excel(writer, index=False, sheet_name="Regulation_Info")
        pd.DataFrame(items_rows).to_excel(writer, index=False, sheet_name="Clauses")
    return buf.getvalue()


def render_regulations_module(
    current_user: Dict[str, Any],
    org_settings: Dict[str, Any],
) -> None:
    st.markdown("### 📜 اللوائح")
    org_name = str((org_settings or {}).get("org_name") or "معامل ا.د أسامة عبد المتعال منصور")
    can_manage = _can_manage_regulations(current_user)
    regulations = [r for r in _load_regulations_safe() if r.get("active", True) is not False]

    if can_manage:
        with st.expander("➕ إضافة لائحة جديدة", expanded=len(regulations) == 0):
            c1, c2, c3 = st.columns([2.2, 1.4, 1.4])
            with c1:
                reg_title = st.text_input("اسم اللائحة", placeholder="مثال: لائحة الجزاءات والمكافآت", key="new_reg_title_inp")
            with c2:
                reg_category = st.text_input("التصنيف", value="جزاءات ومكافآت", key="new_reg_cat_inp")
            with c3:
                reg_eff_date = st.date_input("تاريخ السريان", value=date.today(), key="new_reg_dt_inp")

            st.caption("أدخل البنود في الجدول ويمكنك إضافة صفوف جديدة (`+` في أسفل الجدول أو الكتابة في الصف الفارغ):")
            seed_df = pd.DataFrame([{"مسلسل": 1, "البند": "", "مقدار البند أو التقدير": ""}])
            edited_new_df = st.data_editor(
                seed_df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                column_config={
                    "مسلسل": st.column_config.NumberColumn("مسلسل", step=1, width="small"),
                    "البند": st.column_config.TextColumn("البند", width="large"),
                    "مقدار البند أو التقدير": st.column_config.TextColumn("مقدار البند أو التقدير", width="medium"),
                },
                key="new_reg_items_editor",
            )

            if st.button("💾 حفظ اللائحة", type="primary", key="save_new_reg_btn"):
                clean_rows = _clean_editable_rows(edited_new_df)
                if not reg_title.strip():
                    st.error("يرجى إدخال اسم اللائحة.")
                elif not clean_rows:
                    st.error("يرجى إدخال بند واحد على الأقل في الجدول.")
                else:
                    _save_regulation_record(
                        title=reg_title,
                        category=reg_category,
                        items_rows=clean_rows,
                        effective_dt=reg_eff_date,
                        current_user=current_user,
                    )
                    st.success("✅ تم حفظ اللائحة بنجاح.")
                    st.rerun()

    st.divider()
    st.markdown("#### 📚 اللوائح المتاحة")

    if not regulations:
        st.info("لا توجد لوائح مضافة حتى الآن.")
        return

    search_q = st.text_input("بحث باسم اللائحة", placeholder="اكتب اسم اللائحة للبحث...", key="reg_search_inp").strip().lower()
    filtered_regs = [
        r for r in regulations
        if not search_q
        or search_q in str(r.get("title") or "").lower()
        or search_q in str(r.get("category") or "").lower()
    ]

    if not filtered_regs:
        st.info("لا توجد لوائح مطابقة للبحث.")
        return

    for idx_r, reg in enumerate(filtered_regs):
        r_title = str(reg.get("title") or "لائحة بدون عنوان")
        r_cat = str(reg.get("category") or "عام")
        r_eff = str(reg.get("effective_date") or "")[:10]
        items_rows = _normalize_items_table(reg.get("items"), fallback_content=str(reg.get("content") or ""))

        with st.container(border=True):
            top_c1, top_c2 = st.columns([3.5, 1.5])
            top_c1.markdown(f"#### 📌 {r_title}")
            top_c2.caption(f"التصنيف: `{r_cat}` | السريان: `{r_eff or '—'}`")

            st.dataframe(items_rows, use_container_width=True, hide_index=True)

            xl_bytes = _build_regulation_excel_bytes(reg, org_name, items_rows)
            safe_fname = "".join(ch for ch in r_title if ch.isalnum() or ch in (" ", "_", "-")).strip().replace(" ", "_") or "regulation"

            act_cols = st.columns([1.8, 1.2, 6.0])
            with act_cols[0]:
                st.download_button(
                    "📥 تحميل اللائحة Excel",
                    data=xl_bytes,
                    file_name=f"{safe_fname}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_reg_xl_{reg.get('id') or idx_r}_{idx_r}",
                    type="primary",
                )

            if can_manage and reg.get("id") is not None:
                with act_cols[1]:
                    if st.button("🗑️ حذف", key=f"del_reg_{reg.get('id') or idx_r}_{idx_r}"):
                        _delete_regulation_record(reg.get("id"), r_title, current_user)
                        st.rerun()

                with st.expander("✏️ تعديل اللائحة وبنودها", expanded=False):
                    u_title = st.text_input("اسم اللائحة", value=r_title, key=f"u_reg_t_{idx_r}")
                    u_cat = st.text_input("التصنيف", value=r_cat, key=f"u_reg_c_{idx_r}")
                    u_eff = st.date_input(
                        "تاريخ السريان",
                        value=parse_date_safe(reg.get("effective_date")) or date.today(),
                        key=f"u_reg_d_{idx_r}",
                    )
                    u_edited_df = st.data_editor(
                        pd.DataFrame(items_rows),
                        use_container_width=True,
                        hide_index=True,
                        num_rows="dynamic",
                        column_config={
                            "مسلسل": st.column_config.NumberColumn("مسلسل", step=1, width="small"),
                            "البند": st.column_config.TextColumn("البند", width="large"),
                            "مقدار البند أو التقدير": st.column_config.TextColumn("مقدار البند أو التقدير", width="medium"),
                        },
                        key=f"u_reg_items_ed_{idx_r}",
                    )
                    if st.button("💾 حفظ التعديل", type="primary", key=f"save_u_reg_{idx_r}"):
                        u_clean_rows = _clean_editable_rows(u_edited_df)
                        if u_title.strip() and u_clean_rows:
                            _update_regulation_record(
                                reg_id=reg.get("id"),
                                title=u_title,
                                category=u_cat,
                                items_rows=u_clean_rows,
                                effective_dt=u_eff,
                                current_user=current_user,
                            )
                            st.success("✅ تم تحديث اللائحة.")
                            st.rerun()
                        else:
                            st.error("يرجى إدخال اسم اللائحة وبند واحد على الأقل.")
