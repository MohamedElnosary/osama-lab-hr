from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import streamlit as st
from supabase import Client, create_client


@st.cache_resource
def get_supabase_client() -> Client:
    """إنشاء عميل اتصال بـ Supabase باستخدام Streamlit Secrets مع التخزين المؤقت."""
    url = st.secrets.get("SUPABASE_URL", "").strip()
    key = st.secrets.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError(
            "يرجى ضبط SUPABASE_URL و SUPABASE_KEY داخل إعدادات Secrets في Streamlit."
        )
    return create_client(url, key)


def get_storage_bucket() -> str:
    """إرجاع اسم حاوية المرفقات في Supabase Storage."""
    return st.secrets.get("SUPABASE_STORAGE_BUCKET", "hr-attachments").strip() or "hr-attachments"


@st.cache_data(ttl=60, show_spinner=False)
def fetch_table(
    table_name: str,
    columns: str = "*",
    order_by: Optional[str] = None,
    desc: bool = False,
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    """قراءة بيانات جدول من Supabase مع دعم عدد كبير من السجلات حتى 1,000+ موظف."""
    client = get_supabase_client()
    query = client.table(table_name).select(columns).limit(limit)
    if order_by:
        query = query.order(order_by, desc=desc)
    response = query.execute()
    return response.data or []


def clear_data_cache() -> None:
    """مسح الكاش المؤقت بعد أي عملية إضافة أو تعديل أو حذف."""
    st.cache_data.clear()


def insert_record(table_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """إضافة سجل جديد في جدول محدد."""
    client = get_supabase_client()
    response = client.table(table_name).insert(payload).execute()
    clear_data_cache()
    return response.data[0] if response.data else {}


def update_record(table_name: str, record_id: Any, updates: Dict[str, Any]) -> Dict[str, Any]:
    """تحديث سجل قائم عبر المعرف id."""
    updates_with_ts = {
        **updates,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    client = get_supabase_client()
    response = client.table(table_name).update(updates_with_ts).eq("id", record_id).execute()
    clear_data_cache()
    return response.data[0] if response.data else {}


def delete_record(table_name: str, record_id: Any) -> bool:
    """حذف سجل عبر المعرف id."""
    client = get_supabase_client()
    client.table(table_name).delete().eq("id", record_id).execute()
    clear_data_cache()
    return True


def write_audit_log(
    table_name: str,
    action: str,
    op_type: str,
    details: str,
    by_name: str = "النظام",
    by_user_id: Optional[str] = None,
) -> None:
    """تسجيل حركة في جدول audit_log للمراجعة والرقابة."""
    try:
        client = get_supabase_client()
        payload = {
            "table_name": table_name,
            "action": action,
            "op_type": op_type,
            "details": details,
            "by_name": by_name,
            "by_user_id": by_user_id,
            "created_by": by_user_id,
        }
        client.table("audit_log").insert(payload).execute()
        clear_data_cache()
    except Exception:
        # لا نوقف العملية الأساسية إذا تعذر تسجيل السجل في لحظة معينة
        pass
