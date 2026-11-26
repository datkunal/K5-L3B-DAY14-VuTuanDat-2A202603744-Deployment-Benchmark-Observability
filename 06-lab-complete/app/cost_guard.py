"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày và theo tháng (UTC)
  • Tổng chi phí đã vượt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD → HTTP 402
  • Budget là của cả service (mọi API key / instance dùng chung), vì nó bảo vệ hoá đơn LLM

Rate limit bảo vệ tốc độ (chặn spam), cost guard bảo vệ tiền (chặn tổng chi tiêu).
"""
import math
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

_DAY_TTL = 2 * 24 * 3600       # giữ key ngày 2 ngày rồi tự xoá
_MONTH_TTL = 32 * 24 * 3600    # giữ key tháng 32 ngày


def estimate_tokens(text: str) -> int:
    """Mock LLM không trả token → ước lượng ~4/3 token mỗi từ (đủ dùng cho guard)."""
    return max(1, math.ceil(len(text.split()) * 4 / 3))


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def current_spend() -> tuple[float, float]:
    daily_key, monthly_key = _keys()
    daily, monthly = r.mget(daily_key, monthly_key)
    return float(daily or 0), float(monthly or 0)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    daily, monthly = current_spend()
    if daily >= settings.daily_budget_usd:
        raise HTTPException(
            status_code=402,
            detail=f"Đã hết ngân sách ngày (${daily:.6f} / ${settings.daily_budget_usd}). Thử lại vào ngày mai (UTC).",
        )
    if monthly >= settings.monthly_budget_usd:
        raise HTTPException(
            status_code=402,
            detail=f"Đã hết ngân sách tháng (${monthly:.6f} / ${settings.monthly_budget_usd}).",
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    pipe = r.pipeline()
    pipe.incrbyfloat(daily_key, cost)
    pipe.expire(daily_key, _DAY_TTL)
    pipe.incrbyfloat(monthly_key, cost)
    pipe.expire(monthly_key, _MONTH_TTL)
    pipe.execute()
    return cost
