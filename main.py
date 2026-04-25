import os, uuid, json, hashlib, tempfile, httpx, traceback
from datetime import date as Date
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Smart Notary Jordan API", version="2.2.7")

API_KEY = os.getenv("API_KEY") or os.getenv("API_key")
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_HERE")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/chat/completions"

REQUIRED_FIELDS = {
    "poa_special": ["user_name", "national_id", "agent_name", "agent_national_id", "poa_details"],
    "complaint": ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "subject", "facts", "demands"],
}

class AgentMessageRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

def _generate_pdf_internal(doc_type: str, data: Dict[str, Any], req_id: str):
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage
    
    # التأكد من وجود الحقول
    missing = [f for f in REQUIRED_FIELDS.get(doc_type, []) if not data.get(f)]
    if missing: raise Exception(f"Missing fields: {', '.join(missing)}")
    
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp: tmp_path = tmp.name
        generate_pdf(f"{doc_type}.html", {**data, "date": Date.today().strftime("%Y/%m/%d")}, tmp_path)
        pdf_url = upload_pdf_to_storage(tmp_path, f"{req_id}.pdf")
        return {"status": "success", "pdf_url": pdf_url}
    except Exception as e:
        print(f"❌ PDF ERROR: {traceback.format_exc()}") # هذا سيظهر لك في Render Logs
        raise e
    finally:
        if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)

@app.post("/agent/message")
async def agent_message(request: AgentMessageRequest, auth=Depends(lambda h: h if h == f"Bearer {API_KEY}" else None)):
    from supabase_client import get_session_history, save_session_history
    session_id = request.session_id or str(uuid.uuid4())
    
    system_prompt = f"""أنت كاتب عدل أردني ذكي. اجمع هذه البيانات: {json.dumps(REQUIRED_FIELDS, ensure_ascii=False)}.
قواعدك: 
1. اسأل سؤالاً واحداً فقط.
2. عند اكتمال البيانات، استدعِ generate_notary_document فوراً.
3. إذا نجحت الأداة، أعطِ المستخدم الرابط النهائي."""

    tools = [{"type": "function", "function": {"name": "generate_notary_document", "description": "توليد الوثيقة", "parameters": {"type": "object", "properties": {"doc_type": {"type": "string", "enum": list(REQUIRED_FIELDS.keys())}, "data": {"type": "object"}}, "required": ["doc_type", "data"]}}}]

    history = get_session_history(session_id) or []
    history.append({"role": "user", "content": request.message})

    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(OPENAI_RESPONSES_URL, headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}, 
                                json={"model": OPENAI_MODEL, "messages": [{"role": "system", "content": system_prompt}] + history, "tools": tools})
        data = res.json()
        msg = data["choices"][0]["message"]
        
        if msg.get("tool_calls"):
            history.append(msg)
            for tc in msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"])
                try:
                    res_pdf = _generate_pdf_internal(args["doc_type"], args["data"], str(uuid.uuid4()))
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(res_pdf)})
                except Exception as e:
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"status": "error", "message": str(e)})})
            
            # رد نهائي للمستخدم
            final = await client.post(OPENAI_RESPONSES_URL, headers={"Authorization": f"Bearer {OPENAI_KEY}"}, json={"model": OPENAI_MODEL, "messages": history})
            msg = final.json()["choices"][0]["message"]

    history.append(msg)
    save_session_history(session_id, history)
    return {"id": session_id, "text": msg.get("content", "")}

@app.get("/")
async def h(): return {"status": "ok"}
