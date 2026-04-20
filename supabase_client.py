import os
from supabase import create_client, Client
from dotenv import load_dotenv
 
load_dotenv()
 
 
def _get_client() -> Client:
    """
    ينشئ Client عند الطلب فقط — يمنع الكراش عند بدء التشغيل
    إذا كانت متغيرات البيئة غير موجودة.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL و SUPABASE_KEY مطلوبان في ملف .env")
    return create_client(url, key)
 
 
def get_user_profile(user_id: str) -> dict | None:
    """جلب الاسم والرقم الوطني ورقم الهاتف للمستخدم"""
    try:
        supabase = _get_client()
        response = supabase.table("users") \
            .select("full_name, national_id, phone_number") \
            .eq("id", user_id) \
            .execute()
        return response.data[0] if response.data else None
    except Exception as e:
        raise RuntimeError(f"فشل جلب بيانات المستخدم: {e}")
 
 
def get_witnesses(request_id: str) -> list[dict]:
    """جلب قائمة الشهود المرتبطين بالطلب"""
    try:
        supabase = _get_client()
        response = supabase.table("witnesses") \
            .select("full_name, national_id, phone_number") \
            .eq("request_id", request_id) \
            .execute()
        return response.data or []
    except Exception as e:
        raise RuntimeError(f"فشل جلب بيانات الشهود: {e}")
 
 
def upload_pdf_to_storage(file_path: str, file_name: str) -> str:
    """
    يرفع ملف PDF إلى Supabase Storage ويُعيد الرابط العام.
    يحذف الملف المحلي تلقائياً في جميع الأحوال (finally في main.py).
    """
    supabase = _get_client()
    try:
        with open(file_path, "rb") as f:
            supabase.storage.from_("legal-documents").upload(
                path=file_name,
                file=f,
                file_options={"content-type": "application/pdf"}
            )
        public_url = supabase.storage.from_("legal-documents").get_public_url(file_name)
        return public_url
    except Exception as e:
        raise RuntimeError(f"فشل رفع الملف إلى التخزين: {e}")
 





