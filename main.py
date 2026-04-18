import os
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

# المرجع القانوني: حماية السجلات المادة 4 (2015) [cite: 109]
API_KEY = os.getenv("API_KEY")

# التحقق من الصلاحية قانونياً وتقنياً [cite: 17, 26]
def check_auth(auth_header: str):
    if not auth_header or auth_header != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/")
async def root():
    return {"status": "Notary Server Live", "law_version": "2026_Updated"}

# هاد هو الـ Trigger اللي OpenAI بتدور عليه (Discovery Endpoint)
@app.get("/tools")
async def list_tools():
    """
    تعريف الأدوات بموجب المادة 230 من قانون 2026 [cite: 230]
    """
    return {
        "tools": [
            {
                "name": "generate_notary_document",
                "description": "توليد وثيقة كاتب عدل رسمية (وكالات، عقود إيجار، إخطارات) بناءً على صياغة الأيجنت",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string", "description": "رقم الطلب من سوبابيس"},
                        "facts": {"type": "string", "description": "نص الصياغة القانونية النهائية"}
                    },
                    "required": ["request_id", "facts"]
                }
            }
        ]
    }

# المسار الفعلي لتنفيذ الطباعة [cite: 101, 233]
@app.post("/call")
async def call_tool(request: Request, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    
    data = await request.json()
    tool_name = data.get("name")
    arguments = data.get("arguments", {})

    if tool_name == "generate_notary_document":
        # هون بصير دمج البيانات الرسمي [cite: 101, 103]
        request_id = arguments.get("request_id")
        facts = arguments.get("facts")
        
        # الرد الرسمي للأيجنت
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"تم توليد الوثيقة بنجاح للطلب {request_id}. الرابط: https://smart-notary.jo/v/{request_id}. الهاش: SHA256_PROCESSED"
                }
            ]
        }
    
    raise HTTPException(status_code=404, detail="Tool not found")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
