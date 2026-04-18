import os
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Smart Notary Engine API")

# المرجع القانوني: المادة 4 من قانون المعاملات الإلكترونية 2015 
# تفرض حماية أمن وسرية السجلات
API_KEY = os.getenv("API_KEY")

# نموذج البيانات المدخلة
class NotaryRequest(BaseModel):
    request_id: str
    facts: str

# وظيفة التحقق من الهوية (Security Gate)
async def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or authorization != f"Bearer {API_KEY}":
        # المادة 25 من قانون 2015 تعاقب على استغلال المعلومات
        raise HTTPException(
            status_code=401, 
            detail="غير مصرح بالدخول. يرجى توفير مفتاح API صحيح."
        )
    return authorization

@app.get("/")
async def root():
    return {"message": "سيرفر كاتب العدل الذكي يعمل وفق تعديلات 2026"}

# المسار الخاص بتعريف الأدوات للأيجنت (حل مشكلة No tools)
@app.get("/mcp/tools")
async def list_tools():
    """
    هذا الجزء يخبر OpenAI بالأدوات المتاحة برمجياً
    وفقاً للمادة 230 من تعديل 2026 
    """
    return {
        "tools": [
            {
                "name": "generate_notary_document",
                "description": "توليد وثيقة كاتب عدل رسمية بناءً على الصياغة والوقائع القانونية",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "request_id": {
                            "type": "string", 
                            "description": "رقم الطلب الفريد المرتبط بالمستخدم"
                        },
                        "facts": {
                            "type": "string", 
                            "description": "النص القانوني الذي صاغه الأيجنت للواقعة"
                        }
                    },
                    "required": ["request_id", "facts"]
                }
            }
        ]
    }

@app.post("/generate-pdf")
async def generate_pdf(request: NotaryRequest, token: str = Depends(verify_token)):
    """
    توليد السند الرسمي.
    بموجب المادة 17، هذا السند له الحجية المقررة للسند العادي 
    """
    try:
        # هنا يتم دمج بيانات المستخدم من Supabase مع نص الأيجنت
        # وتوليد ملف PDF بختم QR وبصمة مشفرة
        
        # مثال للرد الذي سيرجع للأيجنت:
        return {
            "status": "success",
            "pdf_url": "https://your-storage.com/document_v1.pdf",
            "hash": "sha256_verified_by_smart_notary",
            "legal_notice": "هذه الوثيقة معتمدة لدى كافة الدوائر الرسمية بموجب قانون 2026"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
