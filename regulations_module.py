from datetime import date, datetime
import io
from typing import Any, Dict, List, Optional
import pandas as pd
import streamlit as st

from db import delete_record, fetch_table, insert_record, update_record, write_audit_log
from rules_engine import get_effective_permissions, parse_date_safe

BASE_REG_COLUMNS: List[str] = ["مسلسل", "البند", "المقدار"]


def _apply_regulations_rtl_styles() -> None:
    st.markdown(
        """
        <style>
            .stApp, .main, [data-testid="stAppViewContainer"], [data-testid="stHeader"],
            [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"],
            [data-testid="stText"], [data-testid="stTextInput"], [data-testid="stTextArea"],
            [data-testid="stDateInput"], [data-testid="stExpander"], [data-testid="stForm"],
            [data-testid="stDataFrame"], [data-testid="stTable"],
            .stMarkdown, .stCaption, .stText, p, span, div, label, h1, h2, h3, h4, h5, h6 {
                direction: rtl !important;
                text-align: right !important;
            }
            [data-testid="stDataFrame"] th, [data-testid="stDataFrame"] td,
            [data-testid="stTable"] th, [data-testid="stTable"] td {
                text-align: right !important;
                direction: rtl !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
            valid = [
                r for r in rows
                if r.get("active", True) is not False
                and (str(r.get("title") or "").strip() or isinstance(r.get("items"), list))
            ]
            if valid:
                return valid
    except Exception:
        pass

    cached = st.session_state.get("local_regulations_store")
    if isinstance(cached, list):
        return [r for r in cached if r.get("active", True) is not False]
    return []


def _get_custom_cols_state(state_key: str) -> List[str]:
    cols = st.session_state.get(state_key)
    if isinstance(cols, list):
        return [
            str(c).strip()
            for c in cols
            if str(c).strip() and str(c).strip() not in BASE_REG_COLUMNS
        ]
    return []


def _normalize_items_table(
    raw_items: Any,
    fallback_content: str = "",
    extra_cols: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    ordered_cols = list(BASE_REG_COLUMNS) + [
        c for c in (extra_cols or []) if c not in BASE_REG_COLUMNS
    ]
    rows: List[Dict[str, Any]] = []

    if isinstance(raw_items, list):
        for idx, item in enumerate(raw_items, start=1):
            if isinstance(item, dict):
                row_out: Dict[str, Any] = {}
                for col_name in ordered_cols:
                    if col_name == "مسلسل":
                        sv = item.get("مسلسل") if item.get("مسلسل") is not None else item.get("serial", idx)
                        row_out["مسلسل"] = sv
                    elif col_name == "البند":
                        row_out["البند"] = str(
                            item.get("البند") or item.get("clause") or item.get("question") or ""
                        ).strip()
                    elif col_name == "المقدار":
                        row_out["المقدار"] = str(
                            item.get("المقدار")
                            or item.get("مقدار البند أو التقدير")
                            or item.get("amount")
                            or item.get("estimate")
                            or ""
                        ).strip()
                    else:
                        row_out[col_name] = str(item.get(col_name) or "").strip()
                if any(str(v or "").strip() for k, v in row_out.items() if k != "مسلسل"):
                    rows.append(row_out)

    if not rows and str(fallback_content or "").strip():
        for idx, ln in enumerate([x.strip() for x in str(fallback_content).splitlines() if x.strip()], start=1):
            base_row: Dict[str, Any] = {"مسلسل": idx, "البند": ln, "المقدار": ""}
            for ec in (extra_cols or []):
                if ec not in base_row:
                    base_row[ec] = ""
            rows.append(base_row)

    if not rows:
        empty_r: Dict[str, Any] = {"مسلسل": 1, "البند": "", "المقدار": ""}
        for ec in (extra_cols or []):
            if ec not in empty_r:
                empty_r[ec] = ""
        rows = [empty_r]
    return rows


def _clean_editable_rows(df_edited: pd.DataFrame) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    if df_edited is None or df_edited.empty:
        return cleaned
    cols = list(df_edited.columns)
    auto_seq = 1
    for _, r in df_edited.iterrows():
        has_any = any(str(r.get(c) or "").strip() for c in cols if c != "مسلسل")
        if not has_any:
            continue
        raw_seq = r.get("مسلسل")
        try:
            seq_num = int(float(raw_seq)) if raw_seq is not None and str(raw_seq).strip() != "" else auto_seq
        except (ValueError, TypeError):
            seq_num = auto_seq

        item_dict: Dict[str, Any] = {"مسلسل": seq_num}
        for c in cols:
            if c == "مسلسل":
                continue
            item_dict[str(c)] = str(r.get(c) or "").strip()
        cleaned.append(item_dict)
        auto_seq += 1
    return cleaned


def _save_regulation_record(
    title: str,
    items_rows: List[Dict[str, Any]],
    effective_dt: date,
    current_user: Dict[str, Any],
) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    text_summary = "\n".join(
        " — ".join(str(it.get(k) or "") for k in it.keys() if str(it.get(k) or "").strip())
        for it in items_rows
    )
    payload: Dict[str, Any] = {
        "title": title.strip(),
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
    items_rows: List[Dict[str, Any]],
    effective_dt: date,
    current_user: Dict[str, Any],
) -> None:
    user_id = str(current_user.get("id") or "")
    by_name = str(current_user.get("name_ar") or "")
    text_summary = "\n".join(
        " — ".join(str(it.get(k) or "") for k in it.keys() if str(it.get(k) or "").strip())
        for it in items_rows
    )
    patch: Dict[str, Any] = {
        "title": title.strip(),
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


def _build_regulation_excel_bytes(
    reg: Dict[str, Any],
    org_name: str,
    items_rows: List[Dict[str, Any]],
) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        info_df = pd.DataFrame([
            {
                "المؤسسة": org_name,
                "اسم اللائحة": str(reg.get("title") or ""),
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
    _apply_regulations_rtl_styles()
    st.markdown("### 📜 اللوائح")
    org_name = str((org_settings or {}).get("org_name") or "معامل ا.د أسامة عبد المتعال منصور")
    can_manage = _can_manage_regulations(current_user)
    regulations = _load_regulations_safe()

    if can_manage:
        with st.expander("➕ إضافة لائحة جديدة", expanded=True):
            c1, c2 = st.columns([3.0, 1.5])
            with c1:
                reg_title = st.text_input("اسم اللائحة", placeholder="اكتب اسم اللائحة هنا", key="new_reg_title_inp")
            with c2:
                reg_eff_date = st.date_input("تاريخ السريان", value=date.today(), key="new_reg_dt_inp")

            extra_cols = _get_custom_cols_state("new_reg_extra_cols")
            col_add1, col_add2, col_add3 = st.columns([2.5, 1.2, 1.3])
            with col_add1:
                new_col_name = st.text_input("إضافة عمود جديد للجدول", placeholder="مثال: ملاحظات", key="new_reg_add_col_inp")
            with col_add2:
                st.write("")
                st.write("")
                if st.button("➕ إدراج عمود", key="new_reg_insert_col_btn"):
                    cn = new_col_name.strip()
                    if cn and cn not in BASE_REG_COLUMNS and cn not in extra_cols:
                        st.session_state["new_reg_extra_cols"] = extra_cols + [cn]
                        st.rerun()
            with col_add3:
                if extra_cols:
                    st.write("")
                    st.write("")
                    if st.button("↩️ حذف آخر عمود", key="new_reg_pop_col_btn"):
                        st.session_state["new_reg_extra_cols"] = extra_cols[:-1]
                        st.rerun()

            all_cols = list(BASE_REG_COLUMNS) + extra_cols
            base_seed_row: Dict[str, Any] = {"مسلسل": 1, "البند": "", "المقدار": ""}
            for ec in extra_cols:
                base_seed_row[ec] = ""
            seed_df = pd.DataFrame([base_seed_row])[all_cols]

            st.caption("الجدول: مسلسل — البند — المقدار (مع إمكانية إضافة صفوف أو أعمدة إضافية):")
            edited_new_df = st.data_editor(
                seed_df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key=f"new_reg_items_editor_{len(all_cols)}",
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
                        items_rows=clean_rows,
                        effective_dt=reg_eff_date,
                        current_user=current_user,
                    )
                    st.session_state["new_reg_extra_cols"] = []
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
        if not search_q or search_q in str(r.get("title") or "").lower()
    ]

    if not filtered_regs:
        st.info("لا توجد لوائح مطابقة للبحث.")
        return

    for idx_r, reg in enumerate(filtered_regs):
        r_title = str(reg.get("title") or "لائحة بدون عنوان")
        r_eff = str(reg.get("effective_date") or "")[:10]
        raw_items = reg.get("items") or []
        detected_extras: List[str] = []
        if isinstance(raw_items, list):
            for it in raw_items:
                if isinstance(it, dict):
                    for k in it.keys():
                        ks = str(k).strip()
                        if ks and ks not in BASE_REG_COLUMNS and ks not in detected_extras:
                            detected_extras.append(ks)

        items_rows = _normalize_items_table(
            raw_items,
            fallback_content=str(reg.get("content") or ""),
            extra_cols=detected_extras,
        )
        ordered_view_cols = list(BASE_REG_COLUMNS) + detected_extras
        ordered_rows = [
            {c: row_d.get(c, "") for c in ordered_view_cols}
            for row_d in items_rows
        ]

        with st.container(border=True):
            top_c1, top_c2 = st.columns([3.5, 1.5])
            top_c1.markdown(f"#### 📌 {r_title}")
            top_c2.caption(f"تاريخ السريان: `{r_eff or '—'}`")

            st.dataframe(ordered_rows, use_container_width=True, hide_index=True)

            xl_bytes = _build_regulation_excel_bytes(reg, org_name, ordered_rows)
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
                    u_eff = st.date_input(
                        "تاريخ السريان",
                        value=parse_date_safe(reg.get("effective_date")) or date.today(),
                        key=f"u_reg_d_{idx_r}",
                    )
                    u_edited_df = st.data_editor(
                        pd.DataFrame(ordered_rows),
                        use_container_width=True,
                        hide_index=True,
                        num_rows="dynamic",
                        key=f"u_reg_items_ed_{idx_r}",
                    )
                    if st.button("💾 حفظ التعديل", type="primary", key=f"save_u_reg_{idx_r}"):
                        u_clean_rows = _clean_editable_rows(u_edited_df)
                        if u_title.strip() and u_clean_rows:
                            _update_regulation_record(
                                reg_id=reg.get("id"),
                                title=u_title,
                                items_rows=u_clean_rows,
                                effective_dt=u_eff,
                                current_user=current_user,
                            )
                            st.success("✅ تم تحديث اللائحة.")
                            st.rerun()
                        else:
                            st.error("يرجى إدخال اسم اللائحة وبند واحد على الأقل.")
