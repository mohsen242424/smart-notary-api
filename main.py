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

app = FastAPI(title="Smart Notary Jordan API", version="2.2.4")

# Support both naming styles to avoid config mismatch
API_KEY = os.getenv("API_KEY") or os.getenv("API_key")
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_HERE")
WORKFLOW_ID = os.getenv("WORKFLOW_ID", "wf_69e2fcba978481909bc85ec8878bf6f70ce899adef1c8af4")
WORKFLOW_VER = os.getenv("WORKFLOW_VERSION", "9")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# مسار الدردشة القياسي 
OPENAI_RESPONSES_URL = os.getenv("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/chat/completions")

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
    "poa_special": ["user_name", "national_id", "agent_name", "agent_national_id", "poa_details"],
    "poa_irrevocable": [
        "user_name", "national_id", "address", "phone", "agent_name", "agent_national_id",
        "land_area", "apartment_number", "plot_number", "basin_number", "basin_name", "city"
    ],
}

# ==========================================
# ذاكرة السيرفر لحفظ سياق المحادثات
CONVERSATION_HISTORY: Dict[str, List[Dict[str, Any]]] = {}
# ==========================================

class NotaryRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    doc_type: DocType
    data: Dict[str, Any] = Field(...)
    witnesses: Optional[List[Dict[str, str]]] = Field(default_factory=list)

class AgentMessageRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    workflow_id: Optional[str] = None
    workflow_version: Optional[str] = None
    previous_response_id: Optional[str] = None

class AgentFunctionResultRequest(BaseModel):
    call_id: str
    result: str
    session_id: Optional[str] = None
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
    from supabase_client import _get_client
    return _get_client()

def _extract_openai_text(data: Dict[str, Any]) -> str:
    parts: List[str] = []
    if "choices" in data and len(data["choices"]) > 0:
        msg = data["choices"][0].get("message", {})
        return msg.get("content", "").strip()
        
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
    return "".join(parts).strip()

def _parse_openai_response(data: Dict[str, Any]) -> Dict[str, Any]:
    text = ""
    function_call = None

    if "choices" in data and len(data["choices"]) > 0:
        msg = data["choices"][0].get("message", {})
        text += msg.get("content") or ""
        
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            tc = tool_calls[0]
            if tc.get("type") == "function":
                raw_args = tc["function"].get("arguments", "{}")
                if isinstance(raw_args, dict):
                    raw_args = json.dumps(raw_args, ensure_ascii=False)
                function_call = {
                    "name": tc["function"].get("name"),
                    "call_id": tc.get("id"),
                    "arguments": raw_args,
                }

    elif "output" in data:
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

    raw_text = payload["text"]
    if isinstance(raw_text, str) and raw_text.startswith("{") and raw_text.endswith("}"):
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                for k in ["next_question", "draft_ready", "doc_type", "required_fields", "collected_fields", "legal_text", "text"]:
                    if k in parsed:
                        payload[k] = parsed[k]
        except Exception:
            pass

    return payload

def _clean_orphan_tool_calls(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not history:
        return history
    cleaned = list(history)
    answered_ids = {
        msg.get("tool_call_id")
        for msg in cleaned
        if msg.get("role") == "tool"
    }
    result = []
    for msg in cleaned:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            unanswered = [
                tc for tc in msg["tool_calls"]
                if tc.get("id") not in answered_ids
            ]
            if unanswered:
                continue
        result.append(msg)
    return result

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
        "version": "2.2.4",
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
    tools = [
        {
            "type": "function",
            "function": {
                "name": "generate_notary_document",
                "description": "Generate a Jordanian legal notary document only after collecting all required fields.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doc_type": {
                            "type": "string",
                            "enum": ["complaint", "lawsuit_civil", "lawsuit_renewal", "poa_special", "poa_irrevocable"]
                        },
                        "data": {
                            "type": "object",
                            "description": "All collected fields for the document"
                        },
                        "witnesses": {
                            "type": "array",
                            "items": {"type": "object"}
                        }
                    },
                    "required": ["doc_type", "data", "witnesses"]
                }
            }
        }
    ]

    prompt_instructions = "أنت مساعد قانوني أردني. مهمتك هي جمع البيانات من المستخدم لإنشاء وثيقة قانونية.\n"
    prompt_instructions += "ممنوع كتابة أي قالب جاهز. اسأل سؤالاً واحداً فقط في كل رسالة، وتحدث بلهجة أردنية لطيفة.\n\n"
    prompt_instructions += "الوثائق المتاحة والحقول المطلوبة بالأسماء الإنجليزية الدقيقة لكل منها بالترتيب:\n"
    for doc, fields in REQUIRED_FIELDS.items():
        prompt_instructions += f"- {doc}: {', '.join(fields)}\n"

    prompt_instructions += """
تعليمات مهمة جداً عند استدعاء الأداة:
- يجب أن تُمرر الحقول داخل كائن data بنفس الأسماء الإنجليزية أعلاه حرفياً.
- مثال صحيح لـ poa_special: {"user_name": "...", "national_id": "...", "agent_name": "...", "agent_national_id": "...", "poa_details": "..."}
- لا تخترع أسماء حقول غير موجودة بالقائمة أعلاه.

الخطوات التي يجب عليك اتباعها بدقة:
1. رحب بالمستخدم واسأله عن نوع الوثيقة التي يحتاجها.
2. بعد أن يحدد النوع، ابدأ بسؤاله عن الحقول المطلوبة الخاصة بتلك الوثيقة (سؤال واحد في كل مرة).
3. بمجرد أن تجمع جميع الحقول، استدعِ الأداة `generate_notary_document` ممرراً البيانات بالأسماء الإنجليزية الصحيحة.
4. عندما تستقبل نتيجة الأداة بنجاح، أعطِ المستخدم الرابط النهائي بصيغة ودية.
"""

    system_msg = {
        "role": "system",
        "content": prompt_instructions
    }

    from supabase_client import get_session_history, save_session_history

    # Resolve session_id: use provided one, or create a new one
    session_id = request.session_id or str(uuid.uuid4())

    # Load history: in-memory cache first, then Supabase
    history: List[Dict[str, Any]] = []
    if request.session_id:
        if request.session_id in CONVERSATION_HISTORY:
            history = list(CONVERSATION_HISTORY[request.session_id])
        else:
            history = get_session_history(request.session_id)
            if history:
                CONVERSATION_HISTORY[request.session_id] = history
    elif request.previous_response_id:
        # Backward compatibility
        if request.previous_response_id in CONVERSATION_HISTORY:
            history = list(CONVERSATION_HISTORY[request.previous_response_id])

    history = _clean_orphan_tool_calls(history)
    history.append({"role": "user", "content": request.message})

    payload: Dict[str, Any] = {
        "model": OPENAI_MODEL,
        "messages": [system_msg] + history,
        "tools": tools,
        "tool_choice": "auto",
    }

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            res = await client.post(
                OPENAI_RESPONSES_URL,
                headers=_openai_headers(),
                json=payload,
            )

            if res.status_code != 200:
                raise HTTPException(status_code=502, detail=f"OpenAI error: {res.text[:500]}")

            data = res.json()

            if "choices" in data and len(data["choices"]) > 0:
                assistant_msg = data["choices"][0].get("message", {})
                history.append(assistant_msg)

                tool_calls = assistant_msg.get("tool_calls", [])
                if tool_calls:
                    for tc in tool_calls:
                        result_str = "{}"
                        if tc.get("type") == "function":
                            if tc["function"]["name"] == "generate_notary_document":
                                try:
                                    raw_args = tc["function"].get("arguments", "{}")
                                    args = json.loads(raw_args)

                                    req_doc = NotaryRequest(
                                        request_id=str(uuid.uuid4()),
                                        doc_type=args.get("doc_type"),
                                        data=args.get("data", {}),
                                        witnesses=args.get("witnesses", [])
                                    )
                                    pdf_result = _generate_pdf_internal(req_doc)
                                    result_str = json.dumps(pdf_result, ensure_ascii=False)
                                except Exception as e:
                                    import traceback
                                    print(f"[PDF_ERROR] doc_type={args.get('doc_type')} data={args.get('data')} error={traceback.format_exc()}")
                                    result_str = json.dumps({"status": "error", "message": f"حدث خطأ أثناء التوليد: {str(e)}"}, ensure_ascii=False)
                            else:
                                result_str = json.dumps({"status": "error", "message": "أداة غير معروفة، يرجى التوقف وطلب البيانات المطلوبة فقط."}, ensure_ascii=False)

                        # ضمان إرسال رد الأداة لمنع خطأ 502 من أوبن أيه آي
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_str
                        })

                    payload["messages"] = [system_msg] + history
                    payload.pop("tool_choice", None)

                    res2 = await client.post(
                        OPENAI_RESPONSES_URL,
                        headers=_openai_headers(),
                        json=payload,
                    )

                    if res2.status_code == 200:
                        data = res2.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            history.append(data["choices"][0].get("message", {}))

            # Persist history under stable session_id (cap cache at 500 entries)
            if len(CONVERSATION_HISTORY) >= 500:
                oldest = next(iter(CONVERSATION_HISTORY))
                del CONVERSATION_HISTORY[oldest]
            CONVERSATION_HISTORY[session_id] = history
            save_session_history(session_id, history)

            parsed_response = _parse_openai_response(data)
            parsed_response["id"] = session_id
            parsed_response["response_id"] = session_id
            parsed_response["session_id"] = session_id

            return parsed_response

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="OpenAI request timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agent/function-result")
async def agent_function_result(request: AgentFunctionResultRequest, auth=Depends(verify_api_key)):
    from supabase_client import get_session_history, save_session_history

    session_id = request.session_id or str(uuid.uuid4())

    history: List[Dict[str, Any]] = []
    if request.session_id:
        if request.session_id in CONVERSATION_HISTORY:
            history = list(CONVERSATION_HISTORY[request.session_id])
        else:
            history = get_session_history(request.session_id)
            if history:
                CONVERSATION_HISTORY[request.session_id] = history
    elif request.previous_response_id:
        if request.previous_response_id in CONVERSATION_HISTORY:
            history = list(CONVERSATION_HISTORY[request.previous_response_id])

    history.append({
        "role": "tool",
        "tool_call_id": request.call_id,
        "content": request.result,
    })

    system_msg = {
        "role": "system",
        "content": "أنت مساعد قانوني أردني."
    }

    payload: Dict[str, Any] = {
        "model": OPENAI_MODEL,
        "messages": [system_msg] + history,
    }

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

        data = res.json()
        if "choices" in data and len(data["choices"]) > 0:
            assistant_msg = data["choices"][0].get("message", {})
            history.append(assistant_msg)

        if len(CONVERSATION_HISTORY) >= 500:
            oldest = next(iter(CONVERSATION_HISTORY))
            del CONVERSATION_HISTORY[oldest]
        CONVERSATION_HISTORY[session_id] = history
        save_session_history(session_id, history)

        parsed_response = _parse_openai_response(data)
        parsed_response["id"] = session_id
        parsed_response["response_id"] = session_id
        parsed_response["session_id"] = session_id

        return parsed_response

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

        valid_doc_types = list(REQUIRED_FIELDS.keys())
        if not doc_type or doc_type not in valid_doc_types:
            raise HTTPException(
                status_code=422,
                detail=f"نوع الوثيقة غير صحيح: '{doc_type}'. الأنواع المتاحة: {valid_doc_types}"
            )

        missing = _validate_required_fields(doc_type, collected_fields)
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"الحقول التالية مطلوبة وغير موجودة: {', '.join(missing)}"
            )

        req = NotaryRequest(
            request_id=draft_id,
            doc_type=doc_type,
            data=collected_fields,
            witnesses=collected_fields.get("witnesses", []) if isinstance(collected_fields, dict) else [],
        )

        result = _generate_pdf_internal(req)

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
        import traceback
        print(f"❌ Unhandled approve error for draft {draft_id}:")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/management/drafts/{draft_id}/revise")
async def revise_management_draft(
    draft_id: str,
    request: ManagementDraftRevisionRequest,
    auth=Depends(verify_api_key),
):
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
            "messages": [
                {"role": "user", "content": prompt}
            ],
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
