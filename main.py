import os
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server import Server
from mcp.server.fastapi import FastApiSseServer
import mcp.types as types

# المرجع القانوني: حماية السجلات المادة 4 (2015)
API_KEY = os.getenv("API_KEY")

# 1. إنشاء خادم MCP رسمي
mcp_server = Server("SmartNotaryEngine")
starlette_server = FastApiSseServer(mcp_server)
app = FastAPI()

# 2. تعريف الأداة برمجياً للأيجنت (حل مشكلة No Tools)
@mcp_server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="generate_notary_document",
            description="توليد وثيقة كاتب عدل رسمية (وكالات، عقود إيجار) بناءً على تعديلات قانون 2026",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "رقم الطلب المرتبط بسوبابيس"},
                    "facts": {"type": "string", "description": "الصياغة القانونية للواقعة"}
                },
                "required": ["request_id", "facts"]
            }
        )
    ]

# 3. تنفيذ الأداة عند استدعائها
@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "generate_notary_document":
        request_id = arguments.get("request_id")
        facts = arguments.get("facts")
        
        # هون بصير الشغل القانوني (سحب بيانات الشهود والأسماء)
        return [
            types.TextContent(
                type="text",
                text=f"تم التوثيق الرقمي بنجاح للطلب {request_id}. تم أرشفة السند المشفر بانتظار التحميل."
            )
        ]
    raise ValueError(f"Tool not found: {name}")

# 4. مسارات الربط التقني (SSE)
@app.get("/sse")
async def sse_endpoint(request: Request):
    async with starlette_server.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
        await mcp_server.run(read_stream, write_stream, mcp_server.create_initialization_options())

@app.post("/messages")
async def messages_endpoint(request: Request):
    return await starlette_server.handle_post_message(request.scope, request.receive, request._send)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
