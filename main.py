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

load_dotenv()

app = FastAPI(title="Smart Notary Jordan API", version="2.2.0")

# Support both naming styles to avoid config mismatch
API_KEY = os.getenv("API_KEY") or os.getenv("API_key")
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_HERE")
WORKFLOW_ID = os.getenv("WORKFLOW_ID", "wf_69e2fcba978481909bc85ec8878bf6f70ce899adef1c8af4")
WORKFLOW_VER = os.getenv("WORKFLOW_VERSION", "5")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

DocType = Literal[
    "complaint", "lawsuit_civil", "lawsuit_renewal", "poa_special", "poa_irrevocable"
]

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
    "poa_special": ["user_name", "national_id", "agent_name", "poa_details"],
    "poa_irrevocable": [
        "user_name", "national_id", "address", "phone", "agent_name", "agent_national_id",
        "land_area", "apartment_number", "plot_number", "basin_number", "basin_name", "city"
    ],
}


class NotaryRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    doc_type: DocType
    data: Dict[str, Any] = Field(...)
    witnesses: Optional[List[Dict[str, str]]] = Field(default_factory=list)


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


class ManagementDraftCreateRequest(BaseModel):
    doc_type: DocType
    required_fields: List[str] = Field(default_factory=list)
    collected_fields: Dict[str, Any] = Field(default_factory=dict)
    legal_text: str = ""
    status: str = "pending_review"


class ManagementDraftRevisionRequest(BaseModel):
    revision_note: str


# ---------------------------
# Helpers
# ---------------------------

async def verify_api_key(authorization: Optional[str] = Header(None)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server")
    if not authorization or authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized Access")
    return authorization


def _openai_headers() -> Dict[str, str]:
    if not OPENAI_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured on server")
    return {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }


def _db():
    # Lazy import to avoid startup crash if env is missing
    from supabase_client import _get_client
    return _get_client()


def _extract_openai_text(data: Dict[str, Any]) -> str:
    parts: List[str] = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
    return "".join(parts).strip()


def _parse_openai_response(data: Dict[str, Any]) -> Dict[str, Any]:
    text = ""
    function_call = None

    for item in data.get("output", []):
        item_type = item.get("type")

        if item_type == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")

        elif item_type == "function_call":
            raw_args = item.get("arguments", "{}")
            if isinstance(raw_args, dict):
                raw_args = json.dumps(raw_args, ensure_ascii=False)

            function_call = {
                "name": item.get("name"),
                "call_id": item.get("call_id") or item.get("id"),
                "arguments": raw_args,
            }

    payload: Dict[str, Any] = {
        "id": data.get("id", ""),
        "response_id": data.get("id", ""),
        "text": text.strip(),
        "function_call": function_call,
    }

    # Optional structured pass-through if model returns JSON text
    # e.g. {"next_question":"...", "draft_ready":true, ...}
    raw_text = payload["text"]
    if isinstance(raw_text, str) and raw_text.startswith("{") and raw_text.endswith("}"):
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                for k in [
                    "next_question",
                    "draft_ready",
                    "doc_type",
                    "required_fields",
                    "collected_fields",
                    "legal_text",
                    "text",
                ]:
                    if k in parsed:
                        payload[k] = parsed[k]
        except Exception:
            pass

    return payload


def _validate_required_fields(doc_type: str, data: Dict[str, Any]) -> List[str]:
    required = REQUIRED_FIELDS.get(doc_type, [])
    return [f for f in required if not data.get(f)]


def _generate_pdf_internal(request: NotaryRequest) -> Dict[str, Any]:
    from utils.pdf_generator import generate_pdf
    from supabase_client import upload_pdf_to_storage

    missing = _validate_required_fields(request.doc_type, request.data)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"الحقول التالية مطلوبة وغير موجودة: {', '.join(missing)}"
        )

    facts_text = (request.data.get("facts", "") or "") + (request.data.get("poa_details", "") or "")
    if any(w in facts_text for w in ["بيع عقار", "وصية", "طلاق", "زواج"]):
        return {
            "status": "rejected",
            "reason": "المعاملة تتطلب حضوراً وجاهياً بموجب المادة 3/ب."
        }

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
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------
# Public routes
# ---------------------------

@app.get("/")
async def health_check():
    return {
        "status": "Online",
        "version": "2.2.0",
        "law_compliance": "Jordan Notary Law 2026",
        "workflow_id": WORKFLOW_ID,
        "workflow_version": WORKFLOW_VER,
    }


@app.get("/schema/{doc_type}")
async def get_schema(doc_type: DocType, auth=Depends(verify_api_key)):
    return {
        "doc_type": doc_type,
        "required_fields": REQUIRED_FIELDS.get(doc_type, []),
        "optional_fields": ["witnesses"],
    }


@app.post("/agent/message")
async def agent_message(request: AgentMessageRequest, auth=Depends(verify_api_key)):
    """
    Receives workflow_id/workflow_version for compatibility with Flutter.
    Does NOT forward `workflow` object to OpenAI responses API.
    """
    payload: Dict[str, Any] = {
        "model": OPENAI_MODEL,
        "input": request.message,
        "store": True,
        "tool_choice": "auto"  # الأهم: إجبار الذكاء الاصطناعي على استخدام الوظائف إذا لزم الأمر
    }

    if request.previous_response_id:
        payload["previous_response_id"] = request.previous_response_id

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                OPENAI_RESPONSES_URL,
                headers=_openai_headers(),
                json=payload,
            )

        if res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI error {res.status_code}: {res.text[:500]}"
            )

        return _parse_openai_response(res.json())

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="OpenAI request timed out")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agent/function-result")
async def agent_function_result(request: AgentFunctionResultRequest, auth=Depends(verify_api_key)):
    payload: Dict[str, Any] = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "type": "function_call_output",
                "call_id": request.call_id,
                "output": request.result,
            }
        ],
        "store": True,
    }

    if request.previous_response_id:
        payload["previous_response_id"] = request.previous_response_id

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                OPENAI_RESPONSES_URL,
                headers=_openai_headers(),
                json=payload,
            )

        if res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI error {res.status_code}: {res.text[:500]}"
            )

        return _parse_openai_response(res.json())

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="OpenAI request timed out")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-pdf")
async def generate_pdf_endpoint(request: NotaryRequest, auth=Depends(verify_api_key)):
    try:
        return _generate_pdf_internal(request)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------
# Management queue routes
# ---------------------------

@app.post("/management/drafts")
async def create_management_draft(
    request: ManagementDraftCreateRequest,
    auth=Depends(verify_api_key),
):
    """
    Creates/accepts a draft payload and returns a canonical draft id.
    App will upsert to Supabase with user_id on mobile side.
    """
    draft_id = str(uuid.uuid4())
    return {
        "id": draft_id,
        "doc_type": request.doc_type,
        "required_fields": request.required_fields,
        "collected_fields": request.collected_fields,
        "legal_text": request.legal_text,
        "status": "pending_review",
        "created_at": Date.today().isoformat(),
    }


@app.post("/management/drafts/{draft_id}/approve")
async def approve_management_draft(
    draft_id: str,
    auth=Depends(verify_api_key),
):
    """
    Reads draft data from Supabase notary_documents by id, then generates PDF.
    """
    try:
        db = _db()
        row_res = db.table("notary_documents") \
            .select("id, doc_type, collected_fields, legal_text") \
            .eq("id", draft_id) \
            .limit(1) \
            .execute()

        if not row_res.data:
            raise HTTPException(status_code=404, detail="Draft not found")

        row = row_res.data[0]
        doc_type = row.get("doc_type")
        collected_fields = row.get("collected_fields") or {}

        req = NotaryRequest(
            request_id=draft_id,
            doc_type=doc_type,
            data=collected_fields,
            witnesses=collected_fields.get("witnesses", []) if isinstance(collected_fields, dict) else [],
        )

        result = _generate_pdf_internal(req)

        # Update final status in DB
        db.table("notary_documents").update({
            "status": "pdf_ready" if result.get("status") == "success" else "rejected",
            "pdf_url": result.get("pdf_url"),
            "hash_fingerprint": result.get("hash_fingerprint"),
        }).eq("id", draft_id).execute()

        return {
            "id": draft_id,
            "status": "pdf_ready" if result.get("status") == "success" else "rejected",
            "pdf_url": result.get("pdf_url"),
            "hash_fingerprint": result.get("hash_fingerprint"),
            "doc_type": result.get("doc_type", doc_type),
            "message": result.get("message", ""),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/management/drafts/{draft_id}/revise")
async def revise_management_draft(
    draft_id: str,
    request: ManagementDraftRevisionRequest,
    auth=Depends(verify_api_key),
):
    """
    Rewrites legal text from scratch based on revision note,
    updates draft status to revision_requested.
    """
    try:
        db = _db()
        row_res = db.table("notary_documents") \
            .select("id, doc_type, collected_fields, legal_text") \
            .eq("id", draft_id) \
            .limit(1) \
            .execute()

        if not row_res.data:
            raise HTTPException(status_code=404, detail="Draft not found")

        row = row_res.data[0]
        doc_type = row.get("doc_type")
        collected_fields = row.get("collected_fields") or {}
        current_text = row.get("legal_text") or ""

        prompt = f"""
أنت مساعد قانوني أردني.
أعد صياغة الوثيقة القانونية كاملة من البداية بشكل قانوني ومنظم.

نوع الوثيقة: {doc_type}
البيانات المهيكلة: {json.dumps(collected_fields, ensure_ascii=False)}
النص الحالي:
{current_text}

ملاحظة التعديل من المستخدم:
{request.revision_note}

المطلوب:
- أعد كتابة النص القانوني كاملًا.
- صياغة رسمية واضحة.
- لا تشرح، فقط النص القانوني النهائي.
"""

        payload = {
            "model": OPENAI_MODEL,
            "input": prompt,
            "store": False,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                OPENAI_RESPONSES_URL,
                headers=_openai_headers(),
                json=payload,
            )

        if res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI error {res.status_code}: {res.text[:500]}"
            )

        data = res.json()
        new_legal_text = _extract_openai_text(data)
        if not new_legal_text:
            new_legal_text = current_text

        db.table("notary_documents").update({
            "status": "revision_requested",
            "revision_note": request.revision_note,
            "legal_text": new_legal_text,
        }).eq("id", draft_id).execute()

        return {
            "id": draft_id,
            "status": "revision_requested",
            "legal_text": new_legal_text,
            "revision_note": request.revision_note,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
