import os
import uuid
import json
import hashlib
import tempfile
import httpx
from datetime import date as Date
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Smart Notary Jordan API", version="2.2.6")

# دعم الاسمين لتجنب مشاكل الإعدادات في Render
API_KEY = os.getenv("API_KEY") or os.getenv("API_key")
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_HERE")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/chat/completions"

REQUIRED_FIELDS: Dict[str, List[str]] = {
    "complaint": ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "subject", "facts", "demands"],
    "lawsuit_civil": ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "defendant_address", "subject", "claim_value", "facts"],
    "lawsuit_renewal": ["court_name", "plaintiff_name", "case_number", "drop_date"],
    "poa_special": ["user_name", "national_id", "agent_name", "agent_national_id", "poa_details"],
    "poa_irrevocable": ["user_name", "national_id", "address", "phone", "agent_name", "agent_national_id", "land_area", "apartment_number", "plot_number", "basin_number", "basin_name", "city"],
}

class NotaryRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    doc_type: str
    data: Dict[str, Any]
    witnesses: Optional[List[Dict[str, str]]] = []

class AgentMessageRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

# Helpers
async def verify_api_key(authorization: Optional[str] = Header(None)):
    if not authorization or authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return authorization

def _openai_headers():
    if not OPENAI_KEY: raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")
    return {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}

def _parse_openai_response(data: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    text = ""
    function_call = None
    if "choices" in data and len(data["choices"]) > 0:
        msg = data["choices"][0].get("message", {})
        text = msg.get("content") or ""
        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            function_call = {"name": tc["function"]["name"], "call_id": tc["id"], "arguments": tc["function"]["arguments"]}
    
    # إعادة الحقول التي تتوقعها واجهة Lovable/Cursor
    return {
        "id": session_id,
        "response_id": session_id,
        "session_id": session_id,
        "text": text.strip(),
        "function_call": function_call
    }

def _generate_pdf_internal(request: NotaryRequest):
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage
    
    missing = [f for f in REQUIRED_FIELDS.get(request.doc_type, []) if not request.data.get(f)]
    if missing: raise HTTPException(status_code=422, detail=f"الحقول التالية مطلوبة: {', '.join(missing)}")
    
    template_data = dict(request.data)
    template_data.update({"date": Date.today().strftime("%Y/%m/%d"), "witnesses": request.witnesses or []})
    
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp: tmp_path = tmp.name
        generate_pdf(f"{request.doc_type}.html", template_data, tmp_path)
        pdf_url = upload_pdf_to_storage(tmp_path, f"{request.request_id}.pdf")
        return {"status": "success", "pdf_url": pdf_url, "message": "تم التوليد بنجاح."}
    finally:
        if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)

@app.get("/")
async def health(): return {"status": "Online", "version": "2.2.6"}

@app.post("/agent/message")
async def agent_message(request: AgentMessageRequest, auth=Depends(verify_api_key)):
    from supabase_client import get_session_history, save_session_history
    session_id = request.session_id or str(uuid.uuid4())
    
    tools = [{
        "type": "function",
        "function": {
            "name": "generate_notary_document",
            "description": "Generate a legal document",
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

    system_prompt = f"أنت مساعد قانوني أردني. اجمع الحقول التالية: {json.dumps(REQUIRED_FIELDS, ensure_ascii=False)}. اسأل سؤالاً واحداً فقط."
    history = get_session_history(session_id) or []
    history.append({"role": "user", "content": request.message})

    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(OPENAI_RESPONSES_URL, headers=_openai_headers(), json={"model": OPENAI_MODEL, "messages": [{"role": "system", "content": system_prompt}] + history, "tools": tools})
        data = res.json()
        assistant_msg = data["choices"][0]["message"]
        history.append(assistant_msg)

        if assistant_msg.get("tool_calls"):
            for tc in assistant_msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"])
                try:
                    result = _generate_pdf_internal(NotaryRequest(doc_type=args["doc_type"], data=args["data"]))
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})
                except Exception as e:
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"status": "error", "message": str(e)})})

            final_res = await client.post(OPENAI_RESPONSES_URL, headers=_openai_headers(), json={"model": OPENAI_MODEL, "messages": [{"role": "system", "content": system_prompt}] + history})
            data = final_res.json()
            history.append(data["choices"][0]["message"])

    save_session_history(session_id, history)
    return _parse_openai_response(data, session_id)
