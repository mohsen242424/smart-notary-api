from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from utils.pdf_generator import generate_pdf
from datetime import datetime
import uuid

app = FastAPI()

class DocumentRequest(BaseModel):
    template_name: str  # مثل complaint أو lawsuit_civil
    data: dict          # القيم المتغيرة (form_data)

@app.get("/")
def home():
    return {"message": "Smart Notary API is running!"}

@app.post("/generate-pdf/")
async def create_document(request: DocumentRequest):
    try:
        # توليد اسم فريد للملف
        file_id = str(uuid.uuid4())[:8]
        output_name = f"doc_{file_id}.pdf"
        output_path = f"generated_files/{output_name}"
        
        # التأكد من وجود مجلد للملفات
        import os
        os.makedirs("generated_files", exist_ok=True)

        # إضافة التاريخ تلقائياً
        request.data['date'] = datetime.now().strftime("%Y/%m/%d")
        
        # مناداة المحرك
        generate_pdf(f"{request.template_name}.html", request.data, output_path)
        
        return {
            "status": "success",
            "file_name": output_name,
            "message": "PDF generated successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
