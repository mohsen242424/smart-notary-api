import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()


def _get_client() -> Client:
    """
    Create Supabase client lazily.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL و SUPABASE_KEY مطلوبان في ملف .env")
    return create_client(url, key)


# ---------------------------
# Existing helpers
# ---------------------------

def get_user_profile(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch user profile fields used in document generation.
    """
    try:
        supabase = _get_client()
        response = (
            supabase.table("users")
            .select("full_name, national_id, phone_number")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
    except Exception as e:
        raise RuntimeError(f"فشل جلب بيانات المستخدم: {e}")


def get_witnesses(request_id: str) -> List[Dict[str, Any]]:
    """
    Fetch witnesses linked to a request id (legacy path).
    """
    try:
        supabase = _get_client()
        response = (
            supabase.table("witnesses")
            .select("full_name, national_id, phone_number")
            .eq("request_id", request_id)
            .execute()
        )
        return response.data or []
    except Exception as e:
        raise RuntimeError(f"فشل جلب بيانات الشهود: {e}")


def upload_pdf_to_storage(file_path: str, file_name: str) -> str:
    """
    Upload PDF to Supabase Storage and return public URL.
    """
    supabase = _get_client()
    try:
        with open(file_path, "rb") as f:
            supabase.storage.from_("legal-documents").upload(
                path=file_name,
                file=f,
                file_options={"content-type": "application/pdf"},
            )
        return supabase.storage.from_("legal-documents").get_public_url(file_name)
    except Exception as e:
        raise RuntimeError(f"فشل رفع الملف إلى التخزين: {e}")


# ---------------------------
# New helpers for management flow
# ---------------------------

def get_notary_document(draft_id: str) -> Optional[Dict[str, Any]]:
    """
    Read one draft/document row by id from notary_documents.
    """
    try:
        supabase = _get_client()
        response = (
            supabase.table("notary_documents")
            .select("*")
            .eq("id", draft_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
    except Exception as e:
        raise RuntimeError(f"فشل جلب المسودة: {e}")


def update_notary_document(draft_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update notary_documents row and return updated row.
    """
    try:
        supabase = _get_client()
        response = (
            supabase.table("notary_documents")
            .update(updates)
            .eq("id", draft_id)
            .select("*")
            .limit(1)
            .execute()
        )
        if not response.data:
            raise RuntimeError("لم يتم العثور على السجل بعد التحديث")
        return response.data[0]
    except Exception as e:
        raise RuntimeError(f"فشل تحديث المسودة: {e}")


def create_notary_document(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Insert a new row into notary_documents and return inserted row.
    """
    try:
        supabase = _get_client()
        response = (
            supabase.table("notary_documents")
            .insert(payload)
            .select("*")
            .limit(1)
            .execute()
        )
        if not response.data:
            raise RuntimeError("فشل إنشاء المسودة")
        return response.data[0]
    except Exception as e:
        raise RuntimeError(f"فشل إنشاء المسودة: {e}")


# ---------------------------
# Session history (conversation memory)
# ---------------------------

def get_session_history(session_id: str) -> List[Dict[str, Any]]:
    """Fetch conversation history for a session from Supabase."""
    try:
        supabase = _get_client()
        response = (
            supabase.table("conversation_sessions")
            .select("history")
            .eq("id", session_id)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0].get("history") or []
        return []
    except Exception:
        return []


def save_session_history(session_id: str, history: List[Dict[str, Any]]) -> None:
    """Upsert conversation history for a session into Supabase."""
    try:
        supabase = _get_client()
        supabase.table("conversation_sessions").upsert({
            "id": session_id,
            "history": history,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass
