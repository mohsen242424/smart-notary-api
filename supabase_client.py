import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# إعدادات الربط
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def get_user_profile(user_id):
    """جلب بيانات المستخدم الأساسية (الاسم والرقم الوطني)"""
    response = supabase.table('users').select("full_name, national_id, phone_number").eq("id", user_id).execute()
    return response.data[0] if response.data else None

def get_witnesses(request_id):
    """جلب قائمة الشهود المرتبطين بهذا الطلب"""
    response = supabase.table('witnesses').select("full_name, national_id, phone_number").eq("request_id", request_id).execute()
    return response.data # تعيد قائمة (List)

def upload_pdf_to_storage(file_path, file_name):
    """رفع الملف لـ Bucket وتوليد رابط عام"""
    with open(file_path, 'rb') as f:
        # تأكد من إنشاء Bucket باسم 'legal-documents' في سوبابيس
        supabase.storage.from_('legal-documents').upload(file_name, f)
    
    # الحصول على الرابط العام للملف
    public_url = supabase.storage.from_('legal-documents').get_public_url(file_name)
    return public_url
