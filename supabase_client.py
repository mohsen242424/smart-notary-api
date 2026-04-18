import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def get_user_profile(user_id):
    # جلب بيانات المستخدم (الاسم والرقم الوطني) من جدول users
    response = supabase.table('users').select("full_name, national_id").eq("id", user_id).execute()
    return response.data[0] if response.data else None

def upload_pdf_to_storage(file_path, file_name):
    # رفع الملف لـ Supabase Storage
    with open(file_path, 'rb') as f:
        supabase.storage.from_('legal-documents').upload(file_name, f)
    
    # الحصول على رابط الملف العام
    return supabase.storage.from_('legal-documents').get_public_url(file_name)
