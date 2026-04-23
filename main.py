import os
import uuid
import hashlib
import tempfile
import httpx
from datetime import date as Date
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, Literal, List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Smart Notary Jordan API", version="2.0.0")

API_KEY      = os.getenv("API_key")
OPENAI_KEY   = os.getenv("OPENAI_API_KEY_HERE")
WORKFLOW_ID  = os.getenv("WORKFLOW_ID", "wf_69e2fcba978481909bc85ec8878bf6f70ce899adef1c8af4")
WORKFLOW_VER = os.getenv("WORKFLOW_VERSION", "2")  # رقم الإصدار المنشور

# ✅ v1 وليس v2
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

DocType = Literal[
    "complaint", "lawsuit_civil", "lawsuit_renewal", "poa_special", "poa_irrevocable"
]

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
    data: Dict[str, Any] = Field(...)
    witnesses: Optional[List[Dict[str, str]]] = Field(default=[])


class AgentMessageRequest(BaseModel):
    message: str
    workflow_id: Optional[str] = None
    workflow_version: Optional[str] = None
    previous_response_id: Optional[str] = None


class AgentFunctionResultRequest(BaseModel):
    call_id: str
    result: str
    workflow_id: Optional[str] = None
    workflow_version: Optional[str] = None
    previous_response_id: Optional[str] = None


async def verify_api_key(authorization: Optional[str] = Header(None)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server")
    if not authorization or authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized Access")
    return authorization


def _openai_headers() -> Dict[str, str]:
    if not OPENAI_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured on server")
    return {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}


def _build_workflow(wf_id: Optional[str], wf_ver: Optional[str]) -> Dict:
    """يبني كائن الـ workflow مع الإصدار الصحيح"""
    wf: Dict[str, Any] = {"id": wf_id or WORKFLOW_ID}
    ver = wf_ver or WORKFLOW_VER
    if ver:
        wf["version"] = ver
    return wf


def _parse_openai_response(data: Dict) -> Dict:
    import json as _json
    text = ""
    function_call = None
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
        elif item.get("type") == "function_call":
            raw = item.get("arguments", "{}")
            try:
                args = _json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                args = {}
            function_call = {
                "name": item.get("name"),
                "call_id": item.get("call_id") or item.get("id"),
                "arguments": args,
            }
    return {
        "text": text.strip(),
        "response_id": data.get("id", ""),
        "function_call": function_call,
    }


@app.get("/")
async def health_check():
    return {"status": "Online", "version": "2.0.0", "law_compliance": "Jordan Notary Law 2026"}


@app.get("/schema/{doc_type}")
async def get_schema(doc_type: DocType, auth=Depends(verify_api_key)):
    return {
        "doc_type": doc_type,
        "required_fields": REQUIRED_FIELDS.get(doc_type, []),
        "optional_fields": ["witnesses"],
    }


@app.post("/agent/message")
async def agent_message(request: AgentMessageRequest, auth=Depends(verify_api_key)):
    """يمرر رسالة المستخدم لـ OpenAI Agent ويرجع الرد"""
    payload: Dict[str, Any] = {
        "model": "gpt-4.1",
        "input": request.message,
        "workflow": _build_workflow(request.workflow_id, request.workflow_version),
        "store": True,
    }
    if request.previous_response_id:
        payload["previous_response_id"] = request.previous_response_id

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(OPENAI_RESPONSES_URL, headers=_openai_headers(), json=payload)
        if res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"OpenAI error {res.status_code}: {res.text[:400]}")
        return _parse_openai_response(res.json())
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="OpenAI request timed out")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agent/function-result")
async def agent_function_result(request: AgentFunctionResultRequest, auth=Depends(verify_api_key)):
    """يرسل نتيجة الـ function للـ Agent ويستقبل رده النهائي"""
    payload: Dict[str, Any] = {
        "model": "gpt-4.1",
        "input": [
            {
                "type": "function_call_output",
                "call_id": request.call_id,
                "output": request.result,
            }
        ],
        "workflow": _build_workflow(request.workflow_id, request.workflow_version),
        "store": True,
    }
    if request.previous_response_id:
        payload["previous_response_id"] = request.previous_response_id

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(OPENAI_RESPONSES_URL, headers=_openai_headers(), json=payload)
        if res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"OpenAI error {res.status_code}: {res.text[:400]}")
        return _parse_openai_response(res.json())
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="OpenAI request timed out")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-pdf")
async def generate_pdf_endpoint(request: NotaryRequest, auth=Depends(verify_api_key)):
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage

    required = REQUIRED_FIELDS.get(request.doc_type, [])
    missing = [f for f in required if not request.data.get(f)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"الحقول التالية مطلوبة وغير موجودة: {', '.join(missing)}"
        )

    facts_text = request.data.get("facts", "") + request.data.get("poa_details", "")
    if any(w in facts_text for w in ["بيع عقار", "وصية", "طلاق", "زواج"]):
        return {"status": "rejected", "reason": "المعاملة تتطلب حضوراً وجاهياً بموجب المادة 3/ب."}

    template_data = dict(request.data)
    template_data["date"] = Date.today().strftime("%Y/%m/%d")
    template_data["witnesses"] = request.witnesses or []
    sha256_hash = hashlib.sha256(
        f"{request.request_id}:{request.doc_type}:{str(sorted(template_data.items()))}".encode()
    ).hexdigest()
    template_data["ai_generated_content"] = sha256_hash
    template_data.setdefault("qr_code_path", "")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        generate_pdf(f"{request.doc_type}.html", template_data, tmp_path)
        pdf_url = upload_pdf_to_storage(tmp_path, f"{request.request_id}.pdf")
        return {
            "status": "success",
            "request_id": request.request_id,
            "doc_type": request.doc_type,
            "pdf_url": pdf_url,
            "hash_fingerprint": sha256_hash,
            "message": "تم توليد الوثيقة وتوثيقها رقمياً بنجاح.",
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
