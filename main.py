import os
import uuid
import hashlib
import tempfile
from datetime import date as Date
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, Literal, List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Smart Notary Jordan API", version="2.0.0")

API_KEY = os.getenv("API_KEY")

DocType = Literal[
    "complaint",
    "lawsuit_civil",
    "lawsuit_renewal",
    "poa_special",
    "poa_irrevocable"
]

# المتغيرات المطلوبة لكل نوع وثيقة
REQUIRED_FIELDS: Dict[str, List[str]] = {
    "complaint":       ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "subject", "facts", "demands"],
    "lawsuit_civil":   ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "defendant_address", "subject", "claim_value", "facts"],
    "lawsuit_renewal": ["court_name", "plaintiff_name", "case_number", "drop_date"],
    "poa_special":     ["user_name", "national_id", "agent_name", "poa_details"],
    "poa_irrevocable": ["user_name", "national_id", "address", "phone", "agent_name", "agent_national_id", "land_area", "apartment_number", "plot_number", "basin_number", "basin_name", "city"],
}


class NotaryRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    doc_type: DocType
    data: Dict[str, Any] = Field(..., description="متغيرات القالب حسب نوع الوثيقة")
    witnesses: Optional[List[Dict[str, str]]] = Field(default=[], description="قائمة الشهود اختياري")


async def verify_api_key(authorization: Optional[str] = Header(None)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server")
    if not authorization or authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized Access")
    return authorization


@app.get("/")
async def health_check():
    return {"status": "Online", "version": "2.0.0", "law_compliance": "Jordan Notary Law 2026"}


@app.get("/schema/{doc_type}")
async def get_schema(doc_type: DocType, auth=Depends(verify_api_key)):
    """يُرجع الحقول المطلوبة لكل نوع وثيقة — مفيد للمساعد الذكي"""
    return {
        "doc_type": doc_type,
        "required_fields": REQUIRED_FIELDS.get(doc_type, []),
        "optional_fields": ["witnesses"]
    }


@app.post("/generate-pdf")
async def generate_pdf_endpoint(request: NotaryRequest, auth=Depends(verify_api_key)):
    """
    يُولّد وثيقة PDF قانونية مُنسّقة بالعربية بناءً على نوع الوثيقة والبيانات المُدخلة.
    يُعيد رابط التحميل وبصمة التوثيق الرقمي SHA-256.
    """
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage

    # التحقق من اكتمال الحقول المطلوبة
    required = REQUIRED_FIELDS.get(request.doc_type, [])
    missing = [f for f in required if not request.data.get(f)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"الحقول التالية مطلوبة وغير موجودة: {', '.join(missing)}"
        )

    # رفض المعاملات التي تتطلب حضوراً وجاهياً
    facts_text = request.data.get("facts", "") + request.data.get("poa_details", "")
    forbidden = ["بيع عقار", "وصية", "طلاق", "زواج"]
    if any(word in facts_text for word in forbidden):
        return {
            "status": "rejected",
            "reason": "المعاملة تتطلب حضوراً وجاهياً بموجب المادة 3/ب من قانون المعاملات الإلكترونية."
        }

    # إضافة الحقول التلقائية
    template_data = dict(request.data)
    template_data["date"] = Date.today().strftime("%Y/%m/%d")
    template_data["witnesses"] = request.witnesses or []

    # توليد بصمة SHA-256 حقيقية
    hash_content = f"{request.request_id}:{request.doc_type}:{str(sorted(template_data.items()))}"
    sha256_hash = hashlib.sha256(hash_content.encode("utf-8")).hexdigest()
    template_data["ai_generated_content"] = sha256_hash
    template_data.setdefault("qr_code_path", "")

    # توليد PDF في ملف مؤقت
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        template_name = f"{request.doc_type}.html"
        generate_pdf(template_name, template_data, tmp_path)

        file_name = f"{request.request_id}.pdf"
        pdf_url = upload_pdf_to_storage(tmp_path, file_name)

        return {
            "status": "success",
            "request_id": request.request_id,
            "doc_type": request.doc_type,
            "pdf_url": pdf_url,
            "hash_fingerprint": sha256_hash,
            "message": "تم توليد الوثيقة وتوثيقها رقمياً بنجاح بموجب قانون الكاتب العدل 2026."
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
