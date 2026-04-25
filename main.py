import os, uuid, json, hashlib, tempfile, httpx, traceback
from datetime import date as Date
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field, ConfigDict
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Smart Notary Jordan API", version="2.2.9")

API_KEY = os.getenv("API_KEY") or os.getenv("API_key")
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_HERE")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/chat/completions"

REQUIRED_FIELDS = {
    "poa_special": ["user_name", "national_id", "agent_name", "agent_national_id", "poa_details"],
    "complaint": ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "subject", "facts", "demands"],
    "lawsuit_civil": ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "defendant_address", "subject", "claim_value", "facts"],
}

class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra='allow')
    message: str
    session_id: Optional[str] = None

async def verify_api_key(authorization: Optional[str] = Header(None)):
    if not API_KEY: raise HTTPException(status_code=500, detail="API_KEY not set")
    if not authorization or authorization != f"Bearer {API_KEY}": raise HTTPException(status_code=401, detail="Unauthorized")
    return authorization

def _generate_pdf_internal(doc_type: str, data: Dict[str, Any], req_id: str):
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage
    try:
        tmp_path = None
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp: tmp_path = tmp.name
        generate_pdf(f"{doc_type}.html", {**data, "date": Date.today().strftime("%Y/%m/%d")}, tmp_path)
        pdf_url = upload_pdf_to_storage(tmp_path, f"{req_id}.pdf")
        return {"status": "success", "pdf_url": pdf_url}
    except Exception as e:
        print(f"❌ PDF ERROR: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}
    finally:
        if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)

@app.post("/agent/message")
async def agent_message(request: AgentMessageRequest, auth: str = Depends(verify_api_key)):
    from supabase_client import get_session_history, save_session_history
    session_id = request.session_id or str(uuid.uuid4())
    
    # تعليمات صارمة جداً لمنع الأعذار النصية
    system_prompt = f"""أنت مساعد قانوني تقني لكاتب العدل الأردني.
لديك قدرة حقيقية على توليد ملفات PDF عبر أداة 'generate_notary_document'.
قواعدك الصارمة:
1. إذا كانت البيانات التالية مكتملة: {json.dumps(REQUIRED_FIELDS, ensure_ascii=False)}، يجب عليك فوراً وبدون أي عذر استدعاء الأداة.
2. ممنوع تماماً أن تقول 'لا أستطيع إنشاء ملفات'؛ أنت تستطيع ذلك عبر الأدوات المتاحة لك.
3. لا تكتب نص الوكالة كرسالة دردشة؛ هدفك هو توليد الملف فقط.
4. بعد استلام رابط الـ PDF من الأداة، قدمه للمستخدم كأنه إنجاز عظيم بنبرة أردنية فخورة."""
    
    tools = [{
        "type": "function", 
        "function": {
            "name": "generate_notary_document", 
            "description": "استخدم هذه الأداة حصراً لتوليد ملف PDF القانوني عند اكتمال البيانات.", 
            "parameters": {
                "type": "object", 
                "properties": {
                    "doc_type": {"type": "string", "enum": list(REQUIRED_FIELDS.keys())}, 
                    "data": {"type": "object"}
                }, 
                "required": ["doc_type", "data"]
            }
        }
    }]

    history = get_session_history(session_id) or []
    history.append({"role": "user", "content": request.message})

    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(
            OPENAI_RESPONSES_URL, 
            headers={"Authorization": f"Bearer {OPENAI_KEY}"}, 
            json={"model": OPENAI_MODEL, "messages": [{"role": "system", "content": system_prompt}] + history, "tools": tools, "tool_choice": "auto"}
        )
        
        data = res.json()
        msg = data["choices"][0]["message"]
        
        if msg.get("tool_calls"):
            history.append(msg)
            for tc in msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"])
                res_tool = _generate_pdf_internal(args["doc_type"], args["data"], str(uuid.uuid4()))
                history.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(res_tool)})
            
            final = await client.post(OPENAI_RESPONSES_URL, headers={"Authorization": f"Bearer {OPENAI_KEY}"}, json={"model": OPENAI_MODEL, "messages": history})
            msg = final.json()["choices"][0]["message"]

    history.append(msg)
    save_session_history(session_id, history)
    return {"id": session_id, "text": msg.get("content", "") or "جاري تجهيز وثيقتك..."}

@app.get("/")
async def health(): return {"status": "ok", "version": "2.2.9"}
