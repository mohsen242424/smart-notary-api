import os
import uuid
import json
import hashlib
import tempfile
import httpx
from datetime import date as Date
from typing import Optional, Literal, List, Dict, Any

from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# تحميل متغيرات البيئة
load_dotenv()

app = FastAPI(title="Smart Notary Jordan API", version="2.2.5")

# الإعدادات الأساسية من متغيرات البيئة
API_KEY = os.getenv("API_KEY") or os.getenv("API_key")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
WORKFLOW_ID = os.getenv("WORKFLOW_ID")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/chat/completions"

# الحقول المطلوبة لكل نوع وثيقة (مطابقة تماماً لقوالب HTML)
REQUIRED_FIELDS: Dict[str, List[str]] = {
    "complaint": [
        "court_name", "plaintiff_name", "national_id", "address",
        "defendant_name", "subject", "facts", "demands"
    ],
    "lawsuit_civil": [
        "court_name", "plaintiff_name", "national_id", "address",
        "defendant_name", "defendant_address", "subject", "claim_value", "facts"
    ],
    "lawsuit_renewal": ["court_name", "plaintiff_name", "case_number", "drop_date"],
    "poa_special": ["user_name", "national_id", "agent_name", "agent_national_id", "poa_details"],
    "poa_irrevocable": [
        "user_name", "national_id", "address", "phone", "agent_name", "agent_national_id",
        "land_area", "apartment_number", "plot_number", "basin_number", "basin_name", "city"
    ],
}

# ذاكرة السيرفر المؤقتة لجلسات الدردشة
CONVERSATION_HISTORY: Dict[str, List[Dict[str, Any]]] = {}

# ---------------------------
# النماذج (Pydantic Models)
# ---------------------------

class NotaryRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    doc_type: str
    data: Dict[str, Any]
    witnesses: Optional[List[Dict[str, str]]] = []

class AgentMessageRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ManagementDraftCreateRequest(BaseModel):
    doc_type: str
    collected_fields: Dict[str, Any] = {}
    legal_text: str = ""

class ManagementDraftRevisionRequest(BaseModel):
    revision_note: str

# ---------------------------
# الوظائف المساعدة (Helpers)
# ---------------------------

async def verify_api_key(authorization: Optional[str] = Header(None)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Server API_KEY not set")
    if not authorization or authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized Access")
    return authorization

def _openai_headers() -> Dict[str, str]:
    if not OPENAI_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    return {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }

def _db():
    from supabase_client import _get_client
    return _get_client()

def _validate_required_fields(doc_type: str, data: Dict[str, Any]) -> List[str]:
    required = REQUIRED_FIELDS.get(doc_type, [])
    return [f for f in required if not data.get(f)]

def _parse_openai_response(data: Dict[str, Any]) -> Dict[str, Any]:
    text = ""
    function_call = None
    if "choices" in data and len(data["choices"]) > 0:
        msg = data["choices"][0].get("message", {})
        text = msg.get("content") or ""
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            tc = tool_calls[0]
            function_call = {
                "name": tc["function"].get("name"),
                "call_id": tc.get("id"),
                "arguments": tc["function"].get("arguments", "{}"),
            }
    return {"text": text, "function_call": function_call}

def _generate_pdf_internal(request: NotaryRequest) -> Dict[str, Any]:
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage

    # 1. التحقق من الحقول
    missing = _validate_required_fields(request.doc_type, request.data)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"الحقول التالية مطلوبة وغير موجودة: {', '.join(missing)}"
        )

    # 2. فحص المحتوى الحساس (قانوني)
    content_to_check = str(request.data.get("facts", "")) + str(request.data.get("poa_details", ""))
    restricted = ["بيع عقار", "وصية", "طلاق", "زواج"]
    if any(word in content_to_check for word in restricted):
        return {
            "status": "rejected",
            "reason": "هذه المعاملة تتطلب حضوراً وجاهياً لدى دائرة كاتب العدل ولا تتم رقمياً."
        }

    # 3. التحضير للتوليد
    template_data = dict(request.data)
    template_data["date"] = Date.today().strftime("%Y/%m/%d")
    template_data["witnesses"] = request.witnesses or []
    
    # بصمة رقمية للوثيقة
    fingerprint = hashlib.sha256(f"{request.request_id}:{json.dumps(template_data)}".encode()).hexdigest()
    template_data["ai_generated_content"] = fingerprint

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        generate_pdf(f"{request.doc_type}.html", template_data, tmp_path)
        pdf_url = upload_pdf_to_storage(tmp_path, f"{request.request_id}.pdf")

        return {
            "status": "success",
            "pdf_url": pdf_url,
            "hash_fingerprint": fingerprint,
            "message": "تم إصدار الوثيقة وتوثيقها رقمياً بنجاح."
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

# ---------------------------
# المسارات الأساسية (API Routes)
# ---------------------------

@app.get("/")
async def health():
    return {"status": "Online", "version": "2.2.5"}

@app.post("/agent/message")
async def agent_message(request: AgentMessageRequest, auth=Depends(verify_api_key)):
    from supabase_client import get_session_history, save_session_history

    session_id = request.session_id or str(uuid.uuid4())
    
    # تعريف الأداة للذكاء الاصطناعي
    tools = [{
        "type": "function",
        "function": {
            "name": "generate_notary_document",
            "description": "استدعِ هذه الأداة فقط بعد جمع كل الحقول المطلوبة وتأكيد المستخدم.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_type": {"type": "string", "enum": list(REQUIRED_FIELDS.keys())},
                    "data": {"type": "object", "description": "يجب أن تحتوي المفاتيح على الأسماء الإنجليزية للحقول."},
                    "witnesses": {"type": "array", "items": {"type": "object"}}
                },
                "required": ["doc_type", "data"]
            }
        }
    }]

    # تعليمات النظام الصارمة
    system_prompt = f"""أنت مساعد قانوني أردني خبير. مهمتك جمع معلومات لكاتب العدل.
قواعدك:
1. اسأل سؤالاً واحداً فقط في كل مرة بلهجة محترمة.
2. لا تستدعي الأداة إلا بعد اكتمال هذه الحقول تماماً وتأكيد المستخدم:
- poa_special: user_name, national_id, agent_name, agent_national_id, poa_details
- complaint: court_name, plaintiff_name, national_id, address, defendant_name, subject, facts, demands
- lawsuit_civil: court_name, plaintiff_name, national_id, address, defendant_name, defendant_address, subject, claim_value, facts
- lawsuit_renewal: court_name, plaintiff_name, case_number, drop_date
- poa_irrevocable: user_name, national_id, address, phone, agent_name, agent_national_id, land_area, apartment_number, plot_number, basin_number, basin_name, city

عند استدعاء الأداة، يجب أن يكون كائن 'data' يحتوي على المفاتيح الإنجليزية المذكورة أعلاه حصراً."""

    # جلب السجل
    history = get_session_history(session_id) or []
    history.append({"role": "user", "content": request.message})

    async with httpx.AsyncClient(timeout=90.0) as client:
        # الطلب الأول لـ OpenAI
        response = await client.post(
            OPENAI_RESPONSES_URL,
            headers=_openai_headers(),
            json={"model": OPENAI_MODEL, "messages": [{"role": "system", "content": system_prompt}] + history, "tools": tools}
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="OpenAI API Error")

        data = response.json()
        assistant_msg = data["choices"][0]["message"]
        history.append(assistant_msg)

        # التعامل مع استدعاء الأداة
        if assistant_msg.get("tool_calls"):
            for tc in assistant_msg["tool_calls"]:
                if tc["function"]["name"] == "generate_notary_document":
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        req = NotaryRequest(doc_type=args["doc_type"], data=args["data"], witnesses=args.get("witnesses", []))
                        result = _generate_pdf_internal(req)
                        tool_result = json.dumps(result, ensure_ascii=False)
                    except Exception as e:
                        tool_result = json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
                    
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_result})

            # طلب الرد النهائي بعد الأداة
            final_res = await client.post(
                OPENAI_RESPONSES_URL,
                headers=_openai_headers(),
                json={"model": OPENAI_MODEL, "messages": [{"role": "system", "content": system_prompt}] + history}
            )
            data = final_res.json()
            history.append(data["choices"][0]["message"])

    # حفظ السجل وإرجاع النتيجة
    save_session_history(session_id, history)
    parsed = _parse_openai_response(data)
    parsed["session_id"] = session_id
    return parsed

# ---------------------------
# مسارات الإدارة (Management)
# ---------------------------

@app.post("/management/drafts/{draft_id}/approve")
async def approve_draft(draft_id: str, auth=Depends(verify_api_key)):
    try:
        db = _db()
        res = db.table("notary_documents").select("*").eq("id", draft_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Draft not found")
        
        doc = res.data
        req = NotaryRequest(
            request_id=draft_id,
            doc_type=doc["doc_type"],
            data=doc["collected_fields"],
            witnesses=doc["collected_fields"].get("witnesses", [])
        )
        
        result = _generate_pdf_internal(req)
        
        db.table("notary_documents").update({
            "status": "pdf_ready" if result["status"] == "success" else "rejected",
            "pdf_url": result.get("pdf_url"),
            "hash_fingerprint": result.get("hash_fingerprint")
        }).eq("id", draft_id).execute()

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
