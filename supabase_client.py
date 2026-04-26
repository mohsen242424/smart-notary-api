import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

# تعريف كائن supabase ليكون متاحاً للاستيراد في main.py
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def upload_pdf_to_storage(file_path: str, file_name: str) -> str:
    try:
        with open(file_path, "rb") as f:
            supabase.storage.from_("legal-documents").upload(
                path=file_name, file=f, file_options={"content-type": "application/pdf"}
            )
        return supabase.storage.from_("legal-documents").get_public_url(file_name)
    except Exception as e:
        if "already exists" in str(e).lower():
            return supabase.storage.from_("legal-documents").get_public_url(file_name)
        raise RuntimeError(f"فشل الرفع: {e}")

def get_session_history(session_id: str):
    try:
        res = supabase.table("conversation_sessions").select("history").eq("id", session_id).maybe_single().execute()
        return res.data["history"] if res.data else []
    except: return []

def save_session_history(session_id: str, history: list):
    try:
        supabase.table("conversation_sessions").upsert({
            "id": session_id, "history": history, "updated_at": datetime.now(timezone.utc).isoformat()
        }).execute()
    except: pass
