from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from utils.pdf_generator import generate_pdf
from supabase_client import supabase, get_user_profile, get_witnesses, upload_pdf_to_storage
import os
import uuid
from datetime import datetime

app = FastAPI()

class GenerateRequest(BaseModel):
    request_id: str # نعتمد على الـ ID فقط لضمان أمان البيانات وسحبها من المصدر

@app.get("/")
def home():
    return {"message": "Smart Notary AI Engine is Running!"}

@app.post("/generate-pdf/")
async def create_document(req: GenerateRequest):
    try:
        # 1. جلب بيانات الطلب من جدول user_requests
        request_res = supabase.table('user_requests').select("*").eq("id", req.request_id).single().execute()
        db_request = request_res.data
        if not db_request:
            raise HTTPException(status_code=404, detail="Request not found")

        # 2. جلب بيانات المستخدم والشهود
        user_info = get_user_profile(db_request['user_id'])
        witnesses_info = get_witnesses(req.request_id)

        # 3. تجميع البيانات النهائية لدمجها في القالب
        # ندمج بيانات الـ form_data مع معلومات المستخدم والشهود
        final_payload = {
            **db_request['form_data'],
            "user_info": user_info,
            "witnesses": witnesses_info,
            "date": datetime.now().strftime("%Y/%m/%d")
        }

        # 4. إعداد الملف وتوليده
        file_id = str(uuid.uuid4())[:8]
        file_name = f"doc_{file_id}.pdf"
        temp_path = f"temp_{file_name}"
        
        # نستخدم الـ request_type كاسم لقالب الـ HTML (مثلاً: complaint)
        template_name = f"{db_request['request_type']}.html"
        
        generate_pdf(template_name, final_payload, temp_path)

        # 5. رفع الملف لـ Supabase Storage وتحديث السجل
        public_url = upload_pdf_to_storage(temp_path, file_name)
        
        # تحديث جدول user_requests برابط الملف وحالته
        supabase.table('user_requests').update({
            "pdf_url": public_url,
            "status": "completed"
        }).eq("id", req.request_id).execute()

        # 6. تنظيف الملف المؤقت من السيرفر
        if os.path.exists(temp_path):
            os.remove(temp_path)

        return {"status": "success", "pdf_url": public_url}

    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
