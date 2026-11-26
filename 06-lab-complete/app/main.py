"""
Production AI Agent — Part 6 Final Project.

Chạy:   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Kiểm:   python check_production_ready.py

Endpoint:
  GET  /health  liveness  — luôn 200 khi process còn sống (không phụ thuộc Redis)
  GET  /ready   readiness — 200 khi Redis kết nối được và không đang tắt; ngược lại 503
  POST /ask     X-API-Key → rate limit (Redis) → cost guard (Redis) → mock LLM
"""
import signal
import time
import uuid
from contextlib import asynccontextmanager

import redis
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.logging_config import setup_logging
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask

logger = setup_logging(settings.log_level)

# Trạng thái của process (mỗi worker một bản). Không chứa dữ liệu người dùng:
# rate limit và chi phí nằm trong Redis để mọi worker / instance dùng chung.
_state = {"started_at": time.time(), "draining": False, "in_flight": 0}


# ─────────────────────────────────────────────────────────
# Graceful shutdown (tiêu chí 11)
# ─────────────────────────────────────────────────────────
def _install_drain_hook() -> None:
    """
    uvicorn đã tự xử lý SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong rồi
    mới chạy phần shutdown của lifespan. Hook này chỉ bật cờ `draining` NGAY khi nhận
    SIGTERM để /ready trả 503 (load balancer ngừng gửi request mới), rồi gọi lại handler
    cũ của uvicorn — không gọi lại thì process sẽ không bao giờ tắt.
    """
    previous = signal.getsignal(signal.SIGTERM)
    if not callable(previous):
        return

    def _on_sigterm(signum, frame):
        _state["draining"] = True   # không log trong signal handler (tránh deadlock lock của logging)
        previous(signum, frame)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:              # không ở main thread (vd chạy trong test client)
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    _install_drain_hook()
    logger.info("startup", extra={
        "environment": settings.environment,
        "port": settings.port,
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "daily_budget_usd": settings.daily_budget_usd,
        "monthly_budget_usd": settings.monthly_budget_usd,
        "api_key_configured": bool(settings.agent_api_key),
        "redis_ok": redis_healthy(),
    })
    if not settings.agent_api_key:
        logger.warning("AGENT_API_KEY chưa được đặt — /ask sẽ trả 401 cho mọi request")
    yield
    # Tới đây uvicorn đã chờ xong các request đang chạy
    _state["draining"] = True
    logger.info("shutdown", extra={"in_flight": _state["in_flight"],
                                   "uptime_s": round(time.time() - _state["started_at"], 1)})
    r.close()


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan)


# ─────────────────────────────────────────────────────────
# Middleware: request id + log JSON mỗi request
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    started = time.perf_counter()
    _state["in_flight"] += 1
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        _state["in_flight"] -= 1
        logger.info("request", extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        })
    response.headers["X-Request-ID"] = request_id
    return response


# ─────────────────────────────────────────────────────────
# Xử lý lỗi: mã lỗi rõ ràng, không lộ stack trace cho client
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"] if p != "body" and not isinstance(p, int)) or "body",
               "msg": e["msg"]}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Dữ liệu gửi lên không hợp lệ", "errors": errors})


@app.exception_handler(redis.RedisError)
async def on_redis_error(request: Request, exc: redis.RedisError):
    logger.error("redis_unavailable", extra={"request_id": getattr(request.state, "request_id", None),
                                             "error_type": type(exc).__name__})
    return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                        content={"detail": "Dịch vụ tạm thời không sẵn sàng, thử lại sau."})


@app.exception_handler(Exception)
async def on_unexpected_error(request: Request, exc: Exception):
    logger.exception("unhandled_error", extra={"request_id": getattr(request.state, "request_id", None)})
    return JSONResponse(status_code=500, content={"detail": "Lỗi nội bộ của server."})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)

    @field_validator("question")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question không được để trống")
        return v.strip()


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "/ask"]}


@app.get("/health")
def health():
    """Liveness: chỉ trả lời 'process còn sống'. KHÔNG kiểm Redis, nếu không orchestrator
    sẽ restart cả agent mỗi khi Redis chập chờn."""
    return {"status": "ok", "uptime_s": round(time.time() - _state["started_at"], 1)}


@app.get("/ready")
def ready():
    """Readiness: có nhận traffic được không. Redis mất → 503 để LB tạm ngừng gửi request."""
    if _state["draining"]:
        return JSONResponse(status_code=503, content={"status": "shutting_down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not_ready", "redis": "unavailable"})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, request: Request, response: Response,
        api_key: str = Depends(verify_api_key)):
    remaining = check_rate_limit(api_key)          # 429 khi vượt giới hạn
    check_budget()                                 # 402 khi hết ngân sách

    started = time.perf_counter()
    answer = llm_ask(body.question)
    llm_ms = round((time.perf_counter() - started) * 1000, 1)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)

    logger.info("llm_call", extra={
        "request_id": request.state.request_id,
        "question_chars": len(body.question),      # không log nội dung câu hỏi / API key
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 8),
        "llm_ms": llm_ms,
    })
    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": round(cost, 8)},
        "request_id": request.state.request_id,
    }
