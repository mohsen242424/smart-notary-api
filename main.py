from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from utils.pdf_generator import generate_pdf
from supabase_client import supabase, get_user_profile, get_witnesses, upload_pdf_to_storage
import os
import uuid
import hashlib
import qrcode
from datetime import datetime

app = FastAPI()

class GenerateRequest(BaseModel):
    request_id: str

def calculate_pdf_hash(file_path):
    """حساب بصمة SHA-256 للملف لضمان عدم التلاعب"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def create_qr_code(data, file_id):
    """توليد QR Code يحتوي على رابط التحقق"""
    qr_path = f"qr_{file_id}.png"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(qr_path)
    return qr_path

@app.post("/generate-pdf/")
async def create_document(req: GenerateRequest):
    try:
        # 1. جلب البيانات من سوبابيس
        request_res = supabase.table('user_requests').select("*").eq("id", req.request_id).single().execute()
        db_request = request_res.data
        if not db_request: raise HTTPException(status_code=404, detail="Request not found")

        user_info = get_user_profile(db_request['user_id'])
        witnesses_info = get_witnesses(req.request_id)

        # 2. توليد QR Code (الرابط يوجه لصفحة التحقق في تطبيقك مستقبلاً)
        verification_url = f"https://smart-notary.jo/verify/{req.request_id}"
        qr_image_path = create_qr_code(verification_url, req.request_id[:8])

        # 3. تجميع البيانات للـ PDF
        final_payload = {
            **db_request['form_data'],
            "user_info": user_info,
            "witnesses": witnesses_info,
            "qr_code_path": os.path.abspath(qr_image_path), # مسار صورة الـ QR
            "date": datetime.now().strftime("%Y/%m/%d")
        }

        # 4. توليد الـ PDF
        file_id = str(uuid.uuid4())[:8]
        file_name = f"notary_doc_{file_id}.pdf"
        temp_pdf_path = f"temp_{file_name}"
        template_name = f"{db_request['request_type']}.html"
        
        generate_pdf(template_name, final_payload, temp_pdf_path)

        # 5. حساب البصمة الرقمية (Digital Hash)
        pdf_hash = calculate_pdf_hash(temp_pdf_path)

        # 6. الرفع لـ Storage
        public_url = upload_pdf_to_storage(temp_pdf_path, file_name)

        # 7. تحديث جدول التوقيعات الرقمية (Digital Signatures)
        supabase.table('digital_signatures').insert({
            "request_id": req.request_id,
            "digital_hash": pdf_hash,
            "signer_role": "system_verified",
            "ip_address": "0.0.0.0" # يمكن تحديثه لجلب IP المستخدم الحقيقي
        }).execute()

        # 8. تحديث الطلب بالرابط الجديد
        supabase.table('user_requests').update({
            "pdf_url": public_url,
            "status": "completed",
            "ai_generated_content": f"SHA-256: {pdf_hash}" # تخزين الهاش كمرجع سريع
        }).execute()

        # 9. تنظيف الملفات المؤقتة
        for p in [temp_pdf_path, qr_image_path]:
            if os.path.exists(p): os.remove(p)

        return {"status": "success", "pdf_url": public_url, "hash": pdf_hash}

    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
