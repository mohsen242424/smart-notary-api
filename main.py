import os, uuid, json, hashlib, tempfile, httpx, traceback
from datetime import date as Date
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field, ConfigDict
from dotenv import load_dotenv

# تحميل متغيرات البيئة
load_dotenv()

app = FastAPI(title="Smart Notary Jordan API", version="2.3.0")

# إعدادات الربط - تدعم المسميات المختلفة لتجنب أخطاء الإعدادات
API_KEY = os.getenv("API_KEY") or os.getenv("API_key")
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_HERE")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/chat/completions"

# الحقول المطلوبة لكل وثيقة (مطابقة لقوالب HTML)
REQUIRED_FIELDS = {
    "poa_special": ["user_name", "national_id", "agent_name", "agent_national_id", "poa_details"],
    "complaint": ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "subject", "facts", "demands"],
    "lawsuit_civil": ["court_name", "plaintiff_name", "national_id", "address", "defendant_name", "defendant_address", "subject", "claim_value", "facts"],
    "lawsuit_renewal": ["court_name", "plaintiff_name", "case_number", "drop_date"],
    "poa_irrevocable": ["user_name", "national_id", "address", "phone", "agent_name", "agent_national_id", "land_area", "apartment_number", "plot_number", "basin_number", "basin_name", "city"],
}

# موديل استقبال الرسائل - يدعم الحقول الإضافية لتجنب خطأ 422
class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra='allow')
    message: str
    session_id: Optional[str] = None

# التحقق من مفتاح API
async def verify_api_key(authorization: Optional[str] = Header(None)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY not set on server")
    if not authorization or authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized Access")
    return authorization

# المنطق الداخلي لتوليد الـ PDF والرفع لـ Supabase
def _generate_pdf_internal(doc_type: str, data: Dict[str, Any], req_id: str):
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage
    
    # التحقق من اكتمال البيانات قبل التوليد
    missing = [f for f in REQUIRED_FIELDS.get(doc_type, []) if not data.get(f)]
    if missing:
        raise Exception(f"Missing fields: {', '.join(missing)}")
    
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        
        # دمج التاريخ والبيانات
        template_data = {**data, "date": Date.today().strftime("%Y/%m/%d"), "witnesses": data.get("witnesses", [])}
        
        generate_pdf(f"{doc_type}.html", template_data, tmp_path)
        pdf_url = upload_pdf_to_storage(tmp_path, f"{req_id}.pdf")
        
        return {"status": "success", "pdf_url": pdf_url}
    except Exception as e:
        print(f"❌ PDF ERROR: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.get("/")
async def health():
    return {"status": "Online", "version": "2.3.0"}

@app.post("/agent/message")
async def agent_message(request: AgentMessageRequest, auth: str = Depends(verify_api_key)):
    from supabase_client import get_session_history, save_session_history
    session_id = request.session_id or str(uuid.uuid4())
    
    system_prompt = f"""أنت مساعد قانوني تقني لكاتب العدل الأردني.
مهمتك الأساسية هي استدعاء 'generate_notary_document' فور اكتمال الحقول التالية: {json.dumps(REQUIRED_FIELDS, ensure_ascii=False)}.
لا تعتذر عن صنع الملفات، استخدم الأداة المتاحة لك دائماً عند اكتمال البيانات."""
    
    tools = [{
        "type": "function", 
        "function": {
            "name": "generate_notary_document", 
            "description": "استخدم هذه الأداة حصراً لتوليد ملف PDF القانوني.", 
            "parameters": {
                "type": "object", 
                "properties": {
                    "doc_type": {"type": "string", "enum": list(REQUIRED_FIELDS.keys())}, 
                    "data": {"type": "object", "description": "الحقول المجموعة"}
                }, 
                "required": ["doc_type"]
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
        
        if res.status_code != 200:
            return {"id": session_id, "text": "عذراً، واجهت مشكلة في الاتصال بمزود الذكاء الاصطناعي."}

        data = res.json()
        msg = data["choices"][0]["message"]
        
        if msg.get("tool_calls"):
            history.append(msg)
            for tc in msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"])
                
                # معالجة مرنة للبيانات لتجنب KeyError
                doc_type = args.get("doc_type")
                doc_data = args.get("data") if args.get("data") else {k: v for k, v in args.items() if k != "doc_type"}
                
                res_tool = _generate_pdf_internal(doc_type, doc_data, str(uuid.uuid4()))
                history.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(res_tool)})
            
            # الحصول على الرد النهائي بعد تنفيذ الأداة
            final_res = await client.post(
                OPENAI_RESPONSES_URL, 
                headers={"Authorization": f"Bearer {OPENAI_KEY}"}, 
                json={"model": OPENAI_MODEL, "messages": history}
            )
            msg = final_res.json()["choices"][0]["message"]

    history.append(msg)
    save_session_history(session_id, history)
    
    return {
        "id": session_id,
        "response_id": session_id,
        "text": msg.get("content", "") or "جاري معالجة طلبك..."
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
