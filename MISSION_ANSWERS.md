# Day 14 Lab — Mission Answers

> Môi trường chạy: Windows 10 + WSL2 Ubuntu 26.04, Python 3.11.18 (venv), Docker Engine 29.9.3, Compose v5.7.1. Ngày chạy 26/11/2026.
> Mọi output dưới đây là output thật của lệnh đã chạy (đã rút gọn phần lặp lại, che token).

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Vì sao nguy hiểm |
|---|---|---|---|
| 1 | Hardcode secret: `OPENAI_API_KEY = "sk-hardcoded-..."` | 17 | Push lên GitHub là lộ key; muốn đổi key phải sửa code và deploy lại |
| 2 | Hardcode credentials DB: `postgresql://admin:password123@...` | 18 | Như trên, lại còn lộ cả user và mật khẩu DB |
| 3 | Log ra secret: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key nằm trong log, ai đọc được log (dashboard, log aggregator) là có key. Khi chạy thật, stdout in ra: `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this` |
| 4 | `print()` thay vì logging có cấu trúc, log cả nội dung câu hỏi | 33, 38 | Không có level, timestamp, không parse/filter được; log nội dung người dùng là rủi ro dữ liệu cá nhân |
| 5 | Config cứng: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Không đổi được theo môi trường (dev/staging/prod) nếu không sửa code |
| 6 | Không có `/health` | — | Platform không biết app còn sống để restart. Gọi thử: `GET /health` → **404** |
| 7 | `host="localhost"` | 51 | Trong container chỉ nghe loopback → từ ngoài container không gọi vào được |
| 8 | `port=8000` cứng | 52 | Railway/Render cấp port qua biến `PORT`; port cứng thì health check của platform fail |
| 9 | `reload=True` | 53 | Chế độ dev: theo dõi file, tốn tài nguyên, không dùng nhiều worker được |
| 10 | Không xử lý shutdown | — | Không có lifespan/cleanup; khi platform tắt container, request dở dang có thể bị cắt |

### Exercise 1.2: Chạy bản develop

Bản develop nhận `question` qua **query string** (gửi JSON sẽ 422):

```text
$ curl -X POST "http://localhost:8000/ask?question=Hello"
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
→ 422

$ curl http://localhost:8000/health
→ 404   (không có health check)
```

stdout của server trong lúc đó (lộ secret ra log):

```text
Starting agent on localhost:8000...
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

**Kết luận:** chạy được, nhưng chưa sẵn sàng cho production: lộ secret, không health check, chỉ chạy được trên localhost:8000.

### Exercise 1.3: So sánh develop và production

Output bản production:

```text
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:20:39.056913+00:00"}
$ curl http://localhost:8000/ready
{"ready":true}
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{}'
{"detail":"question field is required"}
$ PORT=9001 python app.py   → GET :9001/health = 200
$ kill -TERM <pid>           → exit code 0 (Shutting down → Application shutdown complete)
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (key, DB URL, DEBUG, port) | `config.py` đọc env (`os.getenv`), có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một image chạy được ở dev/staging/prod chỉ bằng đổi env; secret không vào Git; đổi key không cần build lại |
| Secrets | Key thật trong code, còn bị in ra log | Đọc từ env, không log secret (chỉ log `question_length`) | Lộ key = mất tiền/mất dữ liệu; log thường được nhiều người đọc hơn code |
| Host / Port | `localhost:8000` cứng | `HOST=0.0.0.0`, `PORT` từ env (đã thử `PORT=9001`) | Container cần bind 0.0.0.0; platform cloud cấp port động |
| Health check | Không có (`/health` → 404) | `/health` (liveness) + `/ready` (readiness) | Platform/LB biết khi nào restart container, khi nào được gửi traffic |
| Logging | `print()` | `logging` với format JSON | Log có cấu trúc thì tìm, lọc, đếm được trong Loki/Datadog; có level để tắt bớt log debug |
| Shutdown | Đột ngột, không cleanup | `lifespan` + handler SIGTERM, uvicorn chờ request xong | Không cắt ngang request khi deploy/scale; đóng kết nối sạch |
| Reload / Debug | `reload=True` luôn bật | Chỉ reload khi `DEBUG=true` | Reload trong production tốn tài nguyên và không dùng được multi-worker |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ cho frontend được phép gọi API từ trình duyệt |

**Hai điểm mình phát hiện khi chạy thật bản production (bản "đúng" vẫn có lỗi):**

1. **`.env` không được đọc.** `requirements.txt` có `python-dotenv` nhưng code không gọi `load_dotenv()`. Thử đổi `APP_NAME=FromDotEnv` trong `.env` thì `settings.app_name` vẫn là `AI Agent`. Nghĩa là bước `cp .env.example .env` trong hướng dẫn không có tác dụng; biến phải được export vào môi trường thật (như cách Docker/Railway làm).
2. **Log JSON không bao giờ được in ra.** `config.py` gọi `logging.warning("OPENAI_API_KEY not set ...")` lúc import, việc này tự cấu hình root logger ở mức WARNING, nên `logging.basicConfig(format=JSON...)` trong `app.py` sau đó không còn tác dụng. Log thật của server chỉ có `WARNING:root:OPENAI_API_KEY not set — using mock LLM` và log của uvicorn, không có dòng `{"event": "startup"...}` nào. Bài học: phải kiểm tra log thật, không tin vào code. Ở Part 6 mình dùng logger riêng (`propagate=False`) có handler riêng để tránh lỗi này.

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`, bản Debian đầy đủ (có gcc, header, công cụ build). Tiện nhưng nặng: image develop build ra là 1,67 GB.
2. **Working directory:** `/app` (`WORKDIR /app`). Mọi lệnh `COPY`/`RUN`/`CMD` sau đó chạy tương đối từ thư mục này.
3. **Vì sao COPY `requirements.txt` trước:** Docker cache theo từng layer, layer nào thay đổi thì layer đó và mọi layer sau phải build lại. Copy `requirements.txt` rồi `pip install` trước, sau đó mới `COPY` code: sửa code chỉ làm mất cache từ bước copy code trở đi, bước `pip install` (chậm nhất) vẫn dùng cache. Nếu `COPY . .` trước thì sửa một dòng code cũng phải cài lại toàn bộ thư viện.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị ghi đè khi chạy `docker run image <lệnh khác>`. `ENTRYPOINT` là chương trình cố định của container, tham số của `docker run` được nối vào sau nó (muốn đổi phải dùng `--entrypoint`). Hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", "--port", "8000"]`. Ở dạng exec (mảng JSON) thì tiến trình là PID 1 và nhận được SIGTERM; dạng shell (`CMD uvicorn ...`) chạy qua `/bin/sh -c`, shell làm PID 1 và không chuyển SIGTERM cho app.

### Exercise 2.2: Build và chạy bản develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .     (build 159 s)
$ docker run -p 8000:8000 my-agent:develop
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.2,"container":true}
$ docker exec <container> whoami
root
```

Image develop: **1,67 GB**. Container chạy bằng **root**.

### Exercise 2.3: Multi-stage build (`02-docker/production/Dockerfile`)

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev` rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chứa công cụ build, **không** được deploy.
- **Stage 2 (`runtime`, `python:3.11-slim`):** tạo user `appuser`, chỉ `COPY --from=builder /root/.local` (thư viện đã cài) + `main.py` + `utils/`, chạy bằng `USER appuser`, có `HEALTHCHECK`, `CMD` dạng exec với 2 worker.
- **Vì sao nhỏ hơn:** image cuối chỉ có các layer của stage cuối. gcc, header, apt cache, pip cache ở stage builder bị bỏ lại. Base cũng là `slim` (189 MB) thay vì bản đầy đủ.

Số đo (`docker images` + `docker image inspect -f {{.Size}}`):

```text
REPOSITORY   TAG        SIZE
my-agent     advanced   240MB
my-agent     develop    1.67GB

develop = 1667.8 MB, advanced = 239.8 MB  →  giảm 85.6% (nhỏ hơn 7.0 lần)
python:3.11-slim (base) = 189MB
user trong container advanced: appuser
```

Layer lớn nhất của image advanced: base Debian slim 87,7 MB, Python runtime 48,9 MB, **thư viện copy từ builder 40,8 MB**. Phần code của mình chỉ vài KB.

### Exercise 2.4: Docker Compose stack (`02-docker/production/docker-compose.yml`)

```text
                         (host)
   Client ──HTTP :80──▶ ┌────────┐
                        │ nginx  │  reverse proxy + rate limit 10 r/s/IP + security headers
                        └───┬────┘
          network "internal" (bridge, không expose ra host)
                            │ proxy_pass http://agent:8000
                        ┌───▼────┐
                        │ agent  │  FastAPI, 2 uvicorn worker, healthcheck /health
                        └─┬────┬─┘
            redis:6379    │    │   http://qdrant:6333
                  ┌───────▼┐  ┌▼────────┐
                  │ redis  │  │ qdrant  │   (volume redis_data, qdrant_data)
                  └────────┘  └─────────┘
```

- Service được start: `agent`, `redis`, `qdrant`, `nginx`. Agent `depends_on` redis + qdrant với `condition: service_healthy`, nên chỉ start sau khi 2 service đó healthy.
- Giao tiếp: cùng network `internal`, gọi nhau bằng **tên service** qua DNS nội bộ của Docker (`redis://redis:6379/0`, `http://qdrant:6333`, `proxy_pass http://agent_backend` → `agent:8000`). Chỉ nginx publish port ra host; agent không publish port nào (`docker compose port agent 8000` → `:0`).

```text
$ docker compose -p k5-day14-docker up -d --build --wait
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 5 seconds (healthy)    8000/tcp
nginx     nginx:alpine            Up 5 seconds              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 16 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 16 seconds (healthy)   6379/tcp

$ curl http://localhost/health
{"status":"ok","uptime_seconds":3.5,"version":"2.0.0","timestamp":"2026-11-26T08:28:21.643919"}
$ curl http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway (nền tảng chính)

- **URL:** https://agent-production-92733a04.up.railway.app
- Deploy **chính agent Part 6** (`06-lab-complete/`) theo CP6, kèm Redis của Railway trên mạng nội bộ (`redis.railway.internal`). `AGENT_API_KEY` là chuỗi ngẫu nhiên đặt trên Railway, không có trong code.
- Kết quả 4 lệnh tự kiểm CP6: `/health` **200**, `/ready` **200**, `/ask` không key **401**, `/ask` có key **200**. Thêm: sai key 401; 12 request liên tiếp → `200×10` (tính cả request kiểm tra đầu) rồi `429`, header `Retry-After: 55`.
- `python check_production_ready.py --public-url <URL>` → `PASS 12/12 — 44/44 checks`.
- Chi tiết, lệnh test và biến môi trường: [DEPLOYMENT.md](DEPLOYMENT.md). Ảnh: [dashboard](screenshots/dashboard.png), [running](screenshots/running.png), [test](screenshots/test.png).

### Exercise 3.2: So sánh `railway.toml` với `render.yaml`

So sánh cả bản mẫu trong `03-cloud-deployment/` lẫn bản mình viết cho Part 6 trong `06-lab-complete/`:

| Khía cạnh | `railway.toml` | `render.yaml` |
|---|---|---|
| Phạm vi | Cấu hình **một service** (build + deploy). Database/Redis và biến môi trường tạo trên dashboard/CLI | **Blueprint** mô tả **cả hạ tầng**: nhiều service (web + Redis/Key Value), plan, region, env vars |
| Định dạng | TOML, 2 bảng `[build]`, `[deploy]` | YAML, danh sách `services:` |
| Cách build | `builder = "NIXPACKS"` (mẫu 03: tự đoán ngôn ngữ, chạy `startCommand`) hoặc `"DOCKERFILE"` (Part 6) | `runtime: python` + `buildCommand`/`startCommand` (mẫu 03) hoặc `runtime: docker` + `dockerfilePath`/`dockerContext` (Part 6) |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Không khai báo, Render tự restart |
| Secret | Không nằm trong file; đặt bằng `railway variable set` (Part 6: qua `--stdin`) | Khai báo **tên** trong file: `sync: false` (nhập tay trên dashboard) hoặc `generateValue: true` (Render tự sinh) |
| Redis | Thêm bằng `railway add --database redis`, nối bằng reference variable `${{Redis.REDIS_URL}}` | Khai báo service `type: keyvalue` (mẫu 03 dùng tên cũ `type: redis`) và nối bằng `fromService: … property: connectionString` |
| Vị trí / region / plan | Chọn trên dashboard | Trong file: `region: singapore`, `plan: free` |
| Auto deploy | `railway up` đẩy thư mục từ máy (không cần Git) hoặc nối GitHub | `autoDeploy: true`: deploy lại mỗi lần push GitHub; bắt buộc có repo |

**Vì sao khác nhau:** Railway đi theo hướng "CLI + dashboard trước, file config chỉ cho phần build/deploy của từng service", nên `railway.toml` ngắn. Render đi theo hướng **Infrastructure as Code**: một file Blueprint dựng lại được cả hệ thống từ repo, nên phải mô tả cả service phụ, region, plan và *khai báo* (không ghi giá trị) secret. Ghi chú: khi deploy, Railway CLI báo `railway.toml` (Config as Code) đã deprecated, được giữ đến 01/12/2026 và khuyến nghị chuyển sang `.railway/railway.ts`. Bài vẫn giữ `railway.toml` vì script chấm yêu cầu.

### Exercise 3.3: Pipeline Cloud Run (`cloudbuild.yaml` + `service.yaml`)

Mỗi lần push, Cloud Build chạy 4 bước nối tiếp nhau bằng `waitFor`:
1. **test:** trong container `python:3.11-slim`, cài requirements + pytest rồi chạy `pytest tests/`. Fail là dừng pipeline, không build.
2. **build:** `docker build` với 2 tag `ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **push:** đẩy cả 2 tag lên Container Registry (`gcr.io/$PROJECT_ID/ai-agent`).
4. **deploy:** `gcloud run deploy` image theo **commit SHA** (rollback được về đúng commit) ở `asia-southeast1`, với min 1 / max 10 instance, 512Mi/1 CPU, timeout 60s, secret lấy từ **Secret Manager** (`--set-secrets`).

`service.yaml` là khai báo Knative tương đương: autoscale theo `containerConcurrency: 80`, `minScale: 1` để tránh cold start, `livenessProbe` gọi `/health` và `startupProbe` gọi `/ready`, `AGENT_API_KEY`/`OPENAI_API_KEY` lấy qua `secretKeyRef`. (Lưu ý: repo mẫu chưa có thư mục `tests/`, nên bước test sẽ fail nếu chạy thật.)

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop`, chạy `AGENT_API_KEY=secret-key-123 python app.py`)

- **Key được check ở đâu:** dependency `verify_api_key()` (dùng `APIKeyHeader(name="X-API-Key")`), gắn vào `/ask` bằng `Depends(verify_api_key)`. `/` và `/health` không cần key.
- **Sai key thì sao:** thiếu key → **401** `Missing API key`; có key nhưng sai → **403** `Invalid API key`.
- **Rotate key:** key đọc từ biến `AGENT_API_KEY`, nên đổi biến trên platform rồi restart/redeploy, không sửa code. Muốn rotate không downtime thì cho phép tạm thời 2 key (cũ + mới), báo client chuyển sang key mới, rồi gỡ key cũ.

```text
$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 401 Unauthorized
$ curl -i -X POST localhost:8000/ask -H "X-API-Key: wrong-key" ...
HTTP/1.1 403 Forbidden
$ curl -i -X POST localhost:8000/ask -H "X-API-Key: secret-key-123" ...
HTTP/1.1 200 OK
```

Nhận xét: bản này dùng `!=` (không so sánh thời gian hằng), có key mặc định `demo-key-change-in-production` khi thiếu env, và `print` key ra stdout lúc start. Part 6 mình sửa cả ba: `hmac.compare_digest`, không có key mặc định (thiếu là từ chối hết), không log key.

### Exercise 4.2: JWT (`04-api-gateway/production`)

**Luồng JWT:** `POST /auth/token` với username/password → server kiểm tra `DEMO_USERS` → ký token HS256 bằng `JWT_SECRET`, payload gồm `sub` (user), `role`, `iat`, `exp` (60 phút) → client gửi `Authorization: Bearer <token>` → `verify_token()` kiểm tra chữ ký + hạn và lấy `username`/`role` từ token, **không cần tra DB mỗi request** (stateless).

```text
$ curl -X POST localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
{"access_token": "eyJhbGciOiJI...<che>", "token_type": "bearer", "expires_in_minutes": 60, ...}
$ (sai mật khẩu)                         → 401
$ curl -i -X POST localhost:8000/ask      (không có token)
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
$ curl -i ... -H "Authorization: Bearer abc.def.ghi"   (token giả)
HTTP/1.1 403 Forbidden
$ curl -i ... -H "Authorization: Bearer $TOKEN"
HTTP/1.1 200 OK
```

### Exercise 4.3: Rate limiting (`rate_limiter.py`)

- **Thuật toán:** **sliding window log**. Mỗi user có 1 `deque` lưu timestamp các request; mỗi lần gọi thì xoá timestamp cũ hơn 60 giây rồi đếm số còn lại.
- **Giới hạn:** user thường **10 request/60 giây** (`rate_limiter_user`), admin **100 request/60 giây** (`rate_limiter_admin`).
- **Bypass cho admin:** `app.py` chọn limiter theo `role` trong JWT: `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user`. Admin không bị bỏ qua hẳn, mà dùng limiter ngưỡng cao hơn.

20 request liên tiếp với token `student` (request đầu tiên đã dùng ở bài 4.2, nên chỉ còn 9 lượt):

```text
Request  1 -> 200   {"requests_remaining": 8, ...}
...
Request  9 -> 200   {"requests_remaining": 0, ...}
Request 10 -> 429   {"error": "Rate limit exceeded", "limit": 10, "window_seconds": 60, "retry_after_seconds": 59}
...
Request 20 -> 429   {"error": "Rate limit exceeded", "limit": 10, "window_seconds": 60, "retry_after_seconds": 58}

$ curl -i (request thứ 21)
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795681344
retry-after: 58

teacher (admin) 15 request liên tiếp: 200 ×15 (limiter 100/phút)
```

**Hạn chế của bản mẫu:** state nằm trong RAM của process, nên chạy 2 worker/3 instance thì mỗi process có bộ đếm riêng (giới hạn thật thành 10 × số process) và restart là mất. Part 6 mình chuyển sang **Redis sorted set** (ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE trong một Lua script cho atomic), có test "restart vẫn bị 429".

### Exercise 4.4: Cost guard

**Bản mẫu (`cost_guard.py`)** đếm token theo user/ngày trong RAM: vượt $1/ngày/user → **402**, tổng toàn hệ thống vượt $10/ngày → **503**, cảnh báo khi dùng 80%. Chạy thử:

```text
$ curl localhost:8000/me/usage   (student, sau 10 request thành công)
{"user_id":"student","date":"2026-11-26","requests":10,"input_tokens":40,"output_tokens":300,"cost_usd":0.000186,"budget_usd":1.0,"budget_remaining_usd":0.999814,"budget_used_pct":0.0}
$ curl localhost:8000/admin/stats   (teacher)
{"total_users":"N/A (in-memory demo)","global_cost_usd":0.00046290000000000014,"global_budget_usd":10.0}
$ curl localhost:8000/admin/stats   (student)   → 403
```

**Lỗi tìm được trong bản mẫu:** response của `/ask` trả `"budget_remaining_usd": usage.total_cost_usd` (`app.py` dòng 160), tức **số đã tiêu**, không phải số còn lại; khi chạy thấy giá trị **tăng dần** 3.7e-05 → 0.000186 thay vì giảm. Ngoài ra state trong RAM nên mất khi restart và không dùng chung giữa các instance.

**Cách mình implement (theo đề: $10/tháng/user, lưu Redis, reset đầu tháng):**

```python
import os
from datetime import datetime, timezone
import redis

r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

def check_budget(user_id: str, estimated_cost: float) -> bool:
    """True nếu còn budget (và giữ chỗ luôn số tiền này), False nếu vượt."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")   # key theo tháng → tự "reset" đầu tháng
    key = f"budget:{user_id}:{month}"
    new_total = r.incrbyfloat(key, estimated_cost)          # cộng atomic trước, tránh race giữa các instance
    r.expire(key, 32 * 24 * 3600)                           # tự xoá sau ~1 tháng
    if new_total > MONTHLY_BUDGET_USD:
        r.incrbyfloat(key, -estimated_cost)                 # hoàn lại phần vừa giữ chỗ
        return False
    return True
```

Chạy thử với Redis thật (`redis:7-alpine`):

```text
alice 4 lần x $3: [True, True, True, False] -> tổng 9      (lần 4 sẽ thành $12 > $10 → từ chối, đã hoàn lại)
bob $9.5: True | bob thêm $0.5: True | bob thêm $0.01: False   (đúng $10 vẫn được, vượt là chặn)
TTL ~32 ngày: 2764800
```

Điểm khác so với lời giải gợi ý (`get` rồi mới `incrbyfloat`): lời giải gợi ý có race condition, 2 instance cùng đọc `current` rồi cùng cộng là vượt budget. Ở đây `INCRBYFLOAT` cộng trước (atomic trong Redis), vượt thì trừ lại.

**Trong Part 6** (`06-lab-complete/app/cost_guard.py`): budget là của **cả service** (bảo vệ hoá đơn LLM), lưu 2 key `cost:daily:YYYY-MM-DD` và `cost:monthly:YYYY-MM` (TTL 2 ngày / 32 ngày); `check_budget()` gọi **trước** LLM, vượt `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` → **402**; `record_cost()` gọi **sau** LLM với số token thật (ước lượng từ số từ) và `INCRBYFLOAT` trong một pipeline.

**Vì sao cần cả rate limit lẫn cost guard:** rate limit chặn **tốc độ** (spam, 1 key gọi dồn dập), còn cost guard chặn **tổng tiền**. Một client gọi đều 9 request/phút vẫn qua rate limit nhưng có thể đốt hết budget trong ngày, nhất là với câu hỏi dài/model đắt. Ngược lại cost guard một mình không ngăn được 1 user chiếm hết tài nguyên trong vài giây.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

```text
$ curl localhost:8000/health
{"status":"ok","uptime_seconds":0.5,"version":"1.0.0","environment":"development","timestamp":"...","checks":{"memory":{"status":"ok","used_percent":23.8}}}  [HTTP 200]
$ curl localhost:8000/ready
{"ready":true,"in_flight_requests":1}  [HTTP 200]
```

- **Liveness (`/health`):** "process còn sống không?". Fail thì platform **restart** container. Chỉ nên kiểm tra chính process, **không** kiểm tra Redis/DB; nếu không, Redis chập chờn sẽ làm platform restart hàng loạt agent dù agent không hỏng.
- **Readiness (`/ready`):** "có nhận traffic được không?". Fail thì LB **tạm ngừng gửi request** nhưng không restart. Phù hợp để kiểm tra dependency (Redis, DB) và trạng thái startup/shutdown.
- Part 6 làm đúng như vậy: `/health` luôn 200; `/ready` ping Redis, mất Redis → 503, đang tắt → 503 (đã test: dừng Redis → `/health` 200, `/ready` 503, `/ask` 503 có `Retry-After`; bật lại → `/ready` 200).

### Exercise 5.2: Graceful shutdown

Mock LLM của bài này cố tình chậm **3 giây**. Gửi request rồi `kill -TERM` sau 0,05 giây:

```text
Request đang chạy khi SIGTERM -> {"answer":"Tôi là AI agent được deploy lên cloud. ..."}  [HTTP 200, 3.04s]
Process thoát với exit code 0 sau 3.2s
curl /health sau khi tắt -> 000 (không còn kết nối)

--- log:
INFO:     Shutting down
INFO:     Waiting for application shutdown.
2026-11-26 08:21:54,402 INFO 🔄 Graceful shutdown initiated...
2026-11-26 08:21:54,402 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [3625]
2026-11-26 08:21:54,402 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Nhận xét:**
- Request 3 giây vẫn hoàn thành (200) dù SIGTERM đến sau 0,05 giây; process tự thoát với **exit code 0**.
- uvicorn tự xử lý SIGTERM: ngừng nhận kết nối mới → chờ request đang chạy → chạy phần shutdown của `lifespan`. Vì vậy vòng lặp "Waiting for N in-flight requests" trong `lifespan` không in lần nào (lúc đó đã hết request).
- Dòng `Received signal 15` của `handle_sigterm` in **sau cùng**: uvicorn thay handler SIGTERM bằng của nó khi chạy, tới lúc tắt xong mới khôi phục handler cũ và phát lại tín hiệu. Nên nếu tự viết handler SIGTERM thì phải gọi lại handler của uvicorn, nếu không process sẽ không tắt. Part 6 mình dùng cách này: hook chỉ bật cờ `draining` (để `/ready` trả 503 ngay) rồi gọi handler cũ của uvicorn.

### Exercise 5.3: Stateless design

- Anti-pattern: `conversation_history = {}` trong RAM. Khi có 3 instance, mỗi instance có dict riêng: request 1 vào instance A, request 2 vào B thì B không thấy history của A; instance restart/scale down là mất dữ liệu.
- `05-scaling-reliability/production/app.py` lưu session trong Redis (`session:{id}`, `SETEX` TTL 1 giờ, giữ 20 message gần nhất), nên instance nào cũng phục vụ được bất kỳ session nào.
- Part 6 của mình stateless theo cùng nguyên tắc: rate limit (sorted set) và chi phí (counter) nằm trong Redis; trong RAM chỉ có trạng thái của chính process (`draining`, số request đang xử lý). Script chấm xác nhận: restart agent vẫn bị 429.

### Exercise 5.4: Load balancing (3 instance + Nginx)

```text
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-3   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-nginx-1   Up 5 seconds              0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 17 seconds (healthy)   6379/tcp

10 request POST /chat qua Nginx :8080, đếm served_by:
      3 instance-073658
      4 instance-d72e39
      3 instance-f054a1
```

Nginx (`upstream agent_cluster { server agent:8000; }` + `resolver 127.0.0.11`) phân phối round-robin qua 3 IP mà Docker DNS trả về cho tên `agent`; `proxy_next_upstream error timeout http_503` cho phép thử instance khác khi 1 instance lỗi.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 312cd584-f426-4fad-a053-08d5a6d4610d
Request 1: [instance-f054a1]  Q: What is Docker?
Request 2: [instance-073658]  Q: Why do we need containers?
Request 3: [instance-d72e39]  Q: What is Kubernetes?
Request 4: [instance-f054a1]  Q: How does load balancing work?
Request 5: [instance-073658]  Q: What is Redis used for?
Total requests: 5
Instances used: {'instance-073658', 'instance-f054a1', 'instance-d72e39'}
✅ All requests served despite different instances!
--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

Script không tự tắt instance nào, nên mình thử thêm: tạo session (1 câu), **`docker stop k5-day14-scaling-agent-1`**, rồi gửi tiếp 6 câu cùng `session_id`:

```text
HTTP codes sau khi kill: 200 200 200 200 200 200
served_by: instance-d72e39 instance-f054a1 instance-d72e39 instance-f054a1 instance-d72e39 instance-f054a1
history count = 14   (1 câu trước kill + 6 câu sau kill, mỗi câu 2 message)
agent-1 log: "Instance instance-073658 shutting down" → "Application shutdown complete"
```

**Giải thích:** 5 câu đi qua cả 3 instance nhưng history vẫn đủ 10 message, vì không instance nào giữ history trong RAM, tất cả đọc/ghi `session:{id}` trên Redis dùng chung. Khi tắt 1 instance, Nginx chỉ còn chia cho 2 instance còn lại, không request nào lỗi và history vẫn liền mạch (14 message). Nếu lưu history trong RAM, đến request thứ 2 (khác instance) là history đã "mất".

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy đầy đủ (có Docker) trong WSL2, thêm `--public-url` của Railway:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số con số chi tiết của lần chạy: image 273 MB; `docker stop` thoát với exit code 0 sau 0,8 s; SIGTERM khi đang có request → request vẫn 200, process exit code 0; rate limit 5/phút → 429 sau đúng 5 request với 2 worker (state chung trong Redis).

---

## AI log

Theo [RULES.md](RULES.md) mục 1. Công cụ: **Claude Code** (trợ lý lập trình AI chạy trong VS Code). Claude Code là *agent*: nó không chỉ gợi ý mà **tự viết code và tự chạy lệnh** trong terminal trên máy mình (WSL/Docker/Railway CLI). Vì vậy bảng dưới tách rõ phần AI làm và phần mình làm. Mọi số đo đều lấy từ lệnh chạy thật trên máy mình; log thô được lưu lại.

| # | Mình nhờ AI làm gì | AI đã làm / đưa ra gì | Mình làm gì và kiểm tra thế nào |
|---|---|---|---|
| 1 | Đọc toàn bộ đề, tóm tắt việc phải làm, kiểm tra máy còn thiếu gì | Tóm tắt các checkpoint và cách tính điểm; phát hiện máy thiếu WSL2 và Docker; giải thích WSL là gì và vì sao cần nó (SIGTERM, graceful shutdown) | • Tự hỏi lại "WSL Ubuntu là gì" để hiểu vì sao lab cần Linux (SIGTERM, graceful shutdown) trước khi cài.<br>• Hỏi lại "có được sửa README không", rồi đối chiếu với [RULES.md](RULES.md) mục 3: chỉ cấm sửa `check_production_ready.py` và `golden_dataset.jsonl`.<br>• Dùng bản tóm tắt làm lộ trình, làm lần lượt Part 6 → deploy → Part 1–5 → Part 7. |
| 2 | Cài môi trường: WSL2 Ubuntu, Docker Desktop, Python 3.11 | Viết và chạy script cài đặt, sắp xếp vào ổ D; phát hiện Python 3.14 của Ubuntu không cài được `pydantic==2.9.0` → dùng `uv` cài Python 3.11; tạo venv riêng cho Part 4 (`pydantic>=2.11`) | • Khi tải bản cập nhật WSL bị lỗi mạng (`0x80072efe`), tự reset mạng rồi báo để AI tải tiếp.<br>• Tự mở Ubuntu bằng `wsl -d Ubuntu` và tạo user Linux `datvt` kèm mật khẩu. Lần đầu gõ nhầm `! wsl -d Ubuntu` trong PowerShell bị báo lỗi, chụp màn hình hỏi lại rồi gõ đúng.<br>• Khi Docker Desktop hiện popup về WSL integration, hỏi lại rồi tự bấm bật integration cho Ubuntu.<br>• Nhờ kiểm tra lại toàn bộ ("check xem ổn chưa"), xem kết quả: `docker` chạy được trong WSL, Python 3.11 và 2 venv đã sẵn sàng. |
| 3 | Viết code Part 6 theo các TODO | Viết toàn bộ `06-lab-complete/app/*` (auth `compare_digest`, rate limit sliding window bằng Lua trong Redis, cost guard `INCRBYFLOAT`, `/health` và `/ready`, log JSON, hook SIGTERM), Dockerfile multi-stage, compose, `.env.example`, `railway.toml`, `render.yaml`; tự chạy `check_production_ready.py`; tự test thêm (input sai → 422, tắt Redis → 503) và tự sửa lỗi tên trường `"0"` → `"body"` | • Ra lệnh bắt đầu Part 6 sau khi môi trường đã xong.<br>• Xem output script chấm `check_production_ready.py` (bản đầy đủ có Docker): `PASS 12/12`, 43/43 check.<br>• Quyết định commit Part 6 thành một commit riêng (`e087d37`) trước khi deploy. |
| 4 | Chọn nền tảng deploy và deploy | So sánh Railway và Render (miễn phí, không cần thẻ); chạy Railway CLI, đặt biến môi trường; viết `DEPLOYMENT.md` và script tự kiểm `cp6_selftest.py`; phát hiện `/ask` có key vẫn 401 do PowerShell chèn ký tự BOM (U+FEFF) vào key → đặt lại key bằng Python | • Hỏi "deploy bằng cái gì xịn hơn" và "có mất tiền không" → **tự chọn Railway** vì có gói dùng thử miễn phí, không cần nhập thẻ.<br>• Tự đăng nhập Railway trên trình duyệt (`railway login`), báo lại khi xong.<br>• Tự kiểm tra trên dashboard Railway: service `agent` và `Redis` đều **Online**, deployment `Part 6 e087d37` **ACTIVE** → chụp `dashboard.png` và `running.png`.<br>• Tự chạy `python D:\DevTools\scripts\cp6_selftest.py` trong terminal:<br>&nbsp;&nbsp;– Lần 1 chạy ở thư mục khác thì báo lỗi JSON → chụp màn hình gửi lại, AI sửa script chạy đúng thư mục đã `railway link`.<br>&nbsp;&nbsp;– Lần 2 thấy 429 ngay từ đầu vì test trước đó đã dùng hết rate limit → đợi 1 phút.<br>&nbsp;&nbsp;– Lần 3 đúng như mong đợi: `/health` 200, `/ready` 200, không key 401, key sai 401, có key 200, gọi tới lần thứ 10 thì 429 kèm `Retry-After` → chụp `test.png`. |
| 5 | Chạy Part 1, 2, 4, 5 và soạn câu trả lời | Viết và chạy script cho từng exercise, lưu output thật; soạn câu trả lời trong file này từ output đó; phát hiện 2 lỗi ở Part 1 (`.env` không được đọc, log JSON không in) và lỗi `budget_remaining_usd` ở Part 4 | • Ra lệnh push phần deploy rồi làm Part 1–5.<br>• Câu trả lời dựa trên output thật lưu ở `D:\DevTools\_logs\day14\` (part1.txt, part2.txt, part4.txt, part5-dev.txt, part5-prod.txt), không ước lượng số. |
| 6 | Docker Hub báo 429 khi build | Kiểm tra hạn mức (100 pull/giờ khi chưa đăng nhập) → kéo image qua `mirror.gcr.io` rồi tag lại | • Hỏi lại "có cần đăng nhập Docker không" → không cần, vì kéo image qua mirror vẫn build được; giữ nguyên không đăng nhập Docker Hub.<br>• Thấy Part 5 build và chạy lại thành công. |
| 7 | Part 7 Bước 1: metric mới, load test, eval, sửa knowledge base | Thêm Histogram `agent_question_length_chars`; chạy 4 mức concurrency và 3 mức `MOCK_SLOW_RATE`, eval v1/v2; viết 2 entry KB (JWT so với API key, HPA), cố ý không dùng keyword `scale` vì trùng câu `scaling-01`; eval lại → 23/23, 0 regression; chạy v1 với baseline v2 → exit code 1 | • Ra lệnh làm Part 7.<br>• Kết quả kiểm chứng nằm trong `reports/`: `eval_v2_fixed.json` đạt 23/23 (100%), quality gate PASS, 0 regression; v1 so với baseline v2 bị FAIL với exit code 1. |
| 8 | Part 7 Bước 2: bật stack Docker (Prometheus/Grafana/Jaeger/Locust) | Phát hiện agent crash-loop `No module named 'uvicorn'` (user `agent` có HOME=`/app` nên Python không thấy gói trong `/home/agent/.local`) → thêm `ENV PYTHONUSERBASE`; Jaeger 2.x không còn `/api/traces` → dùng API v3; chạy Locust headless scale 1 so với 3; tìm trace chậm nhất và trace lỗi, ghép `trace_id` với log | • Tự mở Grafana (http://localhost:3000) lúc Locust đang chạy 30 user, chụp `grafana-dashboard.png`: thấy 18,4 req/s, error rate 0,69%, p95 ~295 ms, 2 instance.<br>• Tự mở Jaeger (http://localhost:16686), service `observable-agent`:<br>&nbsp;&nbsp;– Mở trace chậm `41e4b3f1…` (~1,76 s), thấy span `chat mock-llm-small` chiếm ~1,69 s → chụp `jaeger-slow-trace.png`.<br>&nbsp;&nbsp;– Mở trace lỗi `19fb8097…` (HTTP 503), thấy event `exception` → chụp `jaeger-error-trace.png`. |
| 9 | Thí nghiệm "phá hệ thống", viết `BENCHMARK_REPORT.md` và job CI | Viết script đổi `.env`, recreate agent, poll `/api/v1/alerts` mỗi 5 s để đo thời gian tới firing (A–E); đếm lỗi 504 trong log Nginx; **soạn toàn bộ lời văn phân tích** trong `BENCHMARK_REPORT.md`; viết `.github/workflows/eval.yml` | • Giữ nguyên cấu hình "hỏng" (lỗi 20% + chậm 30%), tự mở trang Alerts của Prometheus (http://localhost:9090/alerts). Thấy `AgentHighErrorRate` và `AgentHighLatencyP95` cùng **FIRING** → chụp `prometheus-alert-firing.png`. |
| 10 | Rà soát toàn bộ repo trước khi nộp | Chạy lại script chấm (PASS 12/12, cả `--public-url`), self-test URL Railway, mô phỏng job CI (v2 → exit 0, v1 → exit 1), quét `.env` và key thật trong lịch sử git, xem lại 7 ảnh xem có lộ thông tin cá nhân không; chỉ ra rằng bảng AI log cũ ghi chưa rõ phần AI làm, nên viết lại bảng này | • Yêu cầu rà soát toàn bộ repo theo SUBMISSION/RUBRIC trước khi nộp.<br>• Tự hỏi lại về mục AI log; yêu cầu ghi tách rõ phần AI làm và phần mình làm, rồi ghi chi tiết cột của mình hơn.<br>• Đọc và xác nhận bảng này đúng với những gì đã diễn ra. |
