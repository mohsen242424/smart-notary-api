from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from utils.pdf_generator import generate_pdf
from supabase_client import upload_pdf_to_storage
import os
import uuid
from datetime import datetime

app = FastAPI()

# هيكلية الطلب اللي رح يبعته الـ Agent
class LegalRequest(BaseModel):
    template_name: str  # مثل complaint أو poa_special
    data: dict          # كل البيانات المتغيرة (اسم، رقم وطني، وقائع...)

@app.get("/")
def root():
    return {"message": "Smart Notary API is Live!"}

@app.post("/generate-pdf/")
async def create_legal_doc(request: LegalRequest):
    try:
        # 1. تجهيز أسماء الملفات
        unique_id = str(uuid.uuid4())[:8]
        file_name = f"legal_doc_{unique_id}.pdf"
        local_path = f"temp_{file_name}"

        # 2. إضافة التاريخ الحالي للبيانات
        request.data['date'] = datetime.now().strftime("%Y/%m/%d")

        # 3. توليد ملف الـ PDF محلياً
        generate_pdf(f"{request.template_name}.html", request.data, local_path)

        # 4. رفع الملف إلى Supabase Storage
        # ملاحظة: تأكد أنك أنشأت Bucket اسمه 'legal-documents' في سوبابيس
        public_url = upload_pdf_to_storage(local_path, file_name)

        # 5. مسح الملف المؤقت من السيرفر بعد الرفع
        if os.path.exists(local_path):
            os.remove(local_path)

        return {
            "status": "success",
            "document_url": public_url,
            "message": "Document generated and uploaded successfully"
        }
    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
