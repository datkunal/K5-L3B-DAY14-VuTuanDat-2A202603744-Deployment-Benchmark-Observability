"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set) để mọi worker / instance dùng chung và vẫn còn
    sau khi restart agent

Cách làm: mỗi request là 1 phần tử trong sorted set, score = thời điểm gọi.
ZREMRANGEBYSCORE xoá request cũ hơn 60s → ZCARD đếm → còn chỗ thì ZADD → EXPIRE.
Bốn bước chạy trong 1 Lua script để atomic: 2 worker gọi cùng lúc không vượt được giới hạn.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60

_SLIDING_WINDOW = r.register_script("""
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, count, oldest[2]}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, window)
return {1, count + 1, ''}
""")


def _bucket_key(api_key: str) -> str:
    # Không lưu API key thô trong Redis, chỉ lưu hash
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return f"ratelimit:{digest}"


def check_rate_limit(key: str) -> int:
    """Hợp lệ → trả số request còn lại trong cửa sổ. Vượt giới hạn → HTTP 429."""
    limit = settings.rate_limit_per_minute
    now = time.time()
    allowed, count, oldest = _SLIDING_WINDOW(
        keys=[_bucket_key(key)],
        args=[now, WINDOW_SECONDS, limit, f"{now}:{uuid.uuid4().hex}"],
    )
    if int(allowed) == 1:
        return max(limit - int(count), 0)

    retry_after = max(1, math.ceil(float(oldest) + WINDOW_SECONDS - now)) if oldest else WINDOW_SECONDS
    raise HTTPException(
        status_code=429,
        detail=f"Vượt giới hạn {limit} request / {WINDOW_SECONDS} giây. Thử lại sau {retry_after} giây.",
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
        },
    )
