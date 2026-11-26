# Deployment Information — Day 14

## Public URL

https://agent-production-92733a04.up.railway.app

## Platform

**Railway** (gói Trial). Deploy chính agent Part 6 trong thư mục `06-lab-complete/` (CP6), không deploy app mẫu `03-cloud-deployment/`.

| Thành phần | Chi tiết |
|---|---|
| Project | `k5-day14-agent`, environment `production` |
| Service `agent` | Build từ `06-lab-complete/Dockerfile` theo `06-lab-complete/railway.toml` (`builder = "DOCKERFILE"`, `healthcheckPath = "/health"`) |
| Service `Redis` | Redis do Railway quản lý, chỉ truy cập qua mạng nội bộ `redis.railway.internal:6379` |
| Port | Railway cấp biến `PORT`; `CMD` trong Dockerfile chạy `uvicorn ... --port ${PORT:-8000}` |

### Cách deploy

```bash
cd 06-lab-complete
railway login
railway init --name k5-day14-agent
railway add --database redis
railway add --service agent
# đặt biến môi trường (xem mục dưới), secret đặt qua --stdin để không lộ trong lịch sử lệnh
railway up . --path-as-root --service agent --ci
railway domain --service agent
```

## Environment Variables Set

Đặt trên Railway (service `agent` → Variables), **không ghi trong code hay trong repo**:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `AGENT_API_KEY` | *(secret, chuỗi ngẫu nhiên 43 ký tự)* | Sinh bằng `secrets.token_urlsafe(32)` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Reference variable của Railway → `redis://…@redis.railway.internal:6379` |
| `ENVIRONMENT` | `production` | |
| `LOG_LEVEL` | `INFO` | |
| `RATE_LIMIT_PER_MINUTE` | `10` | |
| `DAILY_BUDGET_USD` | `1.0` | |
| `MONTHLY_BUDGET_USD` | `10.0` | |
| `WEB_CONCURRENCY` | `1` | 1 worker uvicorn để tiết kiệm RAM gói Trial |
| `PORT` | *(Railway tự cấp)* | |

## Test Commands

Đặt `BASE` và `AGENT_API_KEY` (key lấy trên dashboard Railway), rồi chạy 4 lệnh tự kiểm của CP6. Mỗi lệnh chỉ in mã HTTP:

```bash
BASE=https://agent-production-92733a04.up.railway.app

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

### Health Check

```bash
curl https://agent-production-92733a04.up.railway.app/health
# {"status":"ok","uptime_s":...}
```

### API Test (with authentication)

```bash
curl -X POST https://agent-production-92733a04.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
```

### Kết quả đo ngày 26/11/2026

| Kiểm tra | Kết quả |
|---|---|
| `GET /health` | 200 |
| `GET /ready` | 200 (Redis trên Railway kết nối được) |
| `POST /ask` không có key | 401 |
| `POST /ask` sai key | 401 |
| `POST /ask` đúng key | 200 — `{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":22,"cost_usd":1.38e-05},"request_id":"b9aa730b387f4732"}` |
| Rate limit, 12 request liên tiếp | `200 ×9, 429 ×3` (request đầu đã tính 1), header `Retry-After: 55` |
| `python check_production_ready.py --public-url <URL>` | `PASS 12/12 — 44/44 checks passed`, có dòng `Public URL: GET …/health → 200` |

Log trên Railway là JSON: Railway tự tách các trường `event`, `request_id`, `path`, `status`, `ms`.

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
