"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401 (không phải 403 hay 422)
"""
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Thiếu hoặc sai API key. Gửi header X-API-Key.",
    headers={"WWW-Authenticate": "ApiKey"},
)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho FastAPI — dùng trong /ask: Depends(verify_api_key)."""
    expected = settings.agent_api_key
    # Server chưa cấu hình key → từ chối tất cả (fail closed)
    if not expected or not api_key:
        raise _UNAUTHORIZED
    # compare_digest: so sánh thời gian hằng, tránh timing attack
    if not hmac.compare_digest(api_key.encode(), expected.encode()):
        raise _UNAUTHORIZED
    return api_key
