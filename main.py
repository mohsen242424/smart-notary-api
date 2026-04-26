import os, uuid, json, hashlib, tempfile, httpx, traceback
from datetime import date as Date
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field, ConfigDict
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Smart Notary Jordan API", version="2.4.1")

API_KEY = os.getenv("API_KEY") or os.getenv("API_key")
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_HERE")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/chat/completions"

REQUIRED_FIELDS = {
    "poa_special": ["user_name", "national_id", "agent_name", "agent_national_id", "poa_details"],
}

class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra='allow')
    message: str
    session_id: Optional[str] = None

class DocumentActionRequest(BaseModel):
    doc_id: str
    action: str 
    notes: Optional[str] = None

async def verify_api_key(authorization: Optional[str] = Header(None)):
    if not authorization or authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return authorization

def _generate_pdf_internal(doc_type: str, data: Dict[str, Any], session_id: str):
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage, supabase # استيراد supabase بنجاح الآن
    
    missing = [f for f in REQUIRED_FIELDS.get(doc_type, []) if not data.get(f)]
    if missing:
        return {"status": "error", "message": f"ناقص: {', '.join(missing)}"}
    
    tmp_path = None
    req_id = str(uuid.uuid4())
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        
        # بصمة SHA-256
        doc_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        
        template_data = {**data, "date": Date.today().strftime("%Y/%m/%d"), "ai_generated_content": doc_hash}
        generate_pdf(f"{doc_type}.html", template_data, tmp_path)
        pdf_url = upload_pdf_to_storage(tmp_path, f"{req_id}.pdf")
        
        # حفظ السجل في جدول الإدارة
        supabase.table("user_documents").insert({
            "id": req_id, "session_id": session_id, "file_url": pdf_url, 
            "status": "pending_user", "doc_type": doc_type,
            "doc_name": f"وكالة - {data.get('user_name', 'مجهول')}"
        }).execute()
        
        return {"status": "success", "pdf_url": pdf_url, "doc_id": req_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)

@app.post("/document/action")
async def document_action(request: DocumentActionRequest, auth: str = Depends(verify_api_key)):
    from supabase_client import supabase
    status = "pending_notary" if request.action == "approve" else "rejected"
    supabase.table("user_documents").update({"status": status, "edit_notes": request.notes}).eq("id", request.doc_id).execute()
    return {"status": "success"}

@app.post("/agent/message")
async def agent_message(request: AgentMessageRequest, auth: str = Depends(verify_api_key)):
    from supabase_client import get_session_history, save_session_history
    session_id = request.session_id or str(uuid.uuid4())
    
    # برومبت صارم لمنع الهلوسة وإجبار التوليد
    system_prompt = f"""أنت مساعد قانوني أردني.
1. إذا المعلومات كاملة: استدعِ 'generate_notary_document' فوراً وبدون مقدمات.
2. إذا المعلومات ناقصة: اسأل عن معلومة واحدة فقط.
3. حوّل التفاصيل للغة قانونية رصينة عند التوليد.
الحقول: {json.dumps(REQUIRED_FIELDS, ensure_ascii=False)}."""

    tools = [{
        "type": "function", 
        "function": {
            "name": "generate_notary_document", 
            "description": "توليد الملف فوراً.", 
            "parameters": {
                "type": "object", 
                "properties": {
                    "doc_type": {"type": "string", "enum": ["poa_special"]}, 
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
            headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}, 
            json={"model": OPENAI_MODEL, "messages": [{"role": "system", "content": system_prompt}] + history, "tools": tools, "tool_choice": "auto"}
        )
        
        msg = res.json()["choices"][0]["message"]
        
        if msg.get("tool_calls"):
            history.append(msg)
            for tc in msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"])
                doc_data = args.get("data") if args.get("data") else {k: v for k, v in args.items() if k != "doc_type"}
                res_tool = _generate_pdf_internal(args.get("doc_type"), doc_data, session_id)
                history.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(res_tool)})
            
            final_res = await client.post(OPENAI_RESPONSES_URL, headers={"Authorization": f"Bearer {OPENAI_KEY}"}, json={"model": OPENAI_MODEL, "messages": history})
            msg = final_res.json()["choices"][0]["message"]

    history.append(msg)
    save_session_history(session_id, history)
    return {"id": session_id, "text": msg.get("content", "")}

@app.get("/")
async def health(): return {"status": "ok", "version": "2.4.1"}
