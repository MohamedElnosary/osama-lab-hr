from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import streamlit as st
from supabase import Client, create_client


def _clean_supabase_url(raw_url: str) -> str:
    """تنظيف رابط Supabase لضمان خلوه من مسارات REST الإضافية."""
    cleaned = (raw_url or "").strip().rstrip("/")
    if "/rest/v1" in cleaned:
        cleaned = cleaned.split("/rest/v1")[0].rstrip("/")
    return cleaned


@st.cache_resource
def get_supabase_client() -> Client:
    """إنشاء عميل اتصال بـ Supabase باستخدام Streamlit Secrets مع التخزين المؤقت."""
    url = _clean_supabase_url(st.secrets.get("SUPABASE_URL", ""))
    key = (st.secrets.get("SUPABASE_KEY", "") or "").strip()
    if not url or not key:
        raise RuntimeError(
            "يرجى ضبط SUPABASE_URL و SUPABASE_KEY داخل إعدادات Secrets في Streamlit."
        )
    return create_client(url, key)


def get_storage_bucket() -> str:
    """إرجاع اسم حاوية المرفقات في Supabase Storage."""
    bucket = (st.secrets.get("SUPABASE_STORAGE_BUCKET", "hr-attachments") or "").strip()
    return bucket or "hr-attachments"


@st.cache_data(ttl=60, show_spinner=False)
def fetch_table(
    table_name: str,
    columns: str = "*",
    order_by: Optional[str] = None,
    desc: bool = False,
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    """قراءة بيانات جدول من Supabase مع دعم حتى 1,000+ موظف وسجلات كثيفة."""
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


def upsert_record(
    table_name: str,
    payload: Dict[str, Any],
    on_conflict: str = "id",
) -> Dict[str, Any]:
    """إضافة أو تحديث سجل بناءً على عمود التعارض المحدد."""
    payload_with_ts = {
        **payload,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    client = get_supabase_client()
    response = (
        client.table(table_name)
        .upsert(payload_with_ts, on_conflict=on_conflict)
        .execute()
    )
    clear_data_cache()
    return response.data[0] if response.data else {}


def upload_attachment(file_bytes: bytes, filename: str, content_type: str = "application/octet-stream") -> Optional[str]:
    """رفع مرفق (مثل تقرير إجازة مرضية) إلى Supabase Storage وإرجاع الرابط العام."""
    if not file_bytes or not filename:
        return None
    client = get_supabase_client()
    bucket = get_storage_bucket()
    safe_name = filename.replace(" ", "_")
    storage_path = f"leaves/{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    client.storage.from_(bucket).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": content_type, "upsert": "true"},
    )
    return client.storage.from_(bucket).get_public_url(storage_path)


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
        pass
