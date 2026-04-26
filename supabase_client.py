import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from supabase import create_client, Client
from dotenv import load_dotenv

# تحميل متغيرات البيئة
load_dotenv()

# إعداد المتغيرات
SUPABASE_URL = os.environ.get("SUPABASE_URL")
# دعم أكثر من مسمى للمفتاح لضمان التوافق مع Render
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL و SUPABASE_KEY مطلوبان في إعدادات البيئة (Render/Environment)")

# --- هذا هو التعديل الأساسي الذي سيحل الـ ImportError ---
# تعريف كائن supabase ليكون متاحاً للاستيراد في ملف main.py
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------
# الدوال المعدلة والمحدثة
# ---------------------------

def upload_pdf_to_storage(file_path: str, file_name: str) -> str:
    """
    يرفع ملف PDF إلى Supabase Storage ويرجع الرابط العام.
    """
    try:
        with open(file_path, "rb") as f:
            supabase.storage.from_("legal-documents").upload(
                path=file_name,
                file=f,
                file_options={"content-type": "application/pdf"},
            )
        return supabase.storage.from_("legal-documents").get_public_url(file_name)
    except Exception as e:
        # إذا كان الملف موجوداً مسبقاً، سنكتفي بجلب الرابط (لتجنب أخطاء التكرار)
        if "already exists" in str(e).lower():
            return supabase.storage.from_("legal-documents").get_public_url(file_name)
        raise RuntimeError(f"فشل رفع الملف إلى التخزين: {e}")

def get_session_history(session_id: str) -> List[Dict[str, Any]]:
    """جلب تاريخ المحادثة من Supabase."""
    try:
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
    """حفظ تاريخ المحادثة في Supabase."""
    try:
        supabase.table("conversation_sessions").upsert({
            "id": session_id,
            "history": history,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass

# دالة مساعدة لجلب بيانات المستخدم (إذا لزم الأمر)
def get_user_profile(user_id: str) -> Optional[Dict[str, Any]]:
    try:
        response = supabase.table("users").select("full_name, national_id").eq("id", user_id).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception: return None
