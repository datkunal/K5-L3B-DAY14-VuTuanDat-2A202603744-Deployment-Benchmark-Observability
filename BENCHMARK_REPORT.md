# Benchmark & Observability Report — Day 14

**Họ tên:** Vũ Tuấn Đạt
**Ngày chạy:** 26/11/2026
**Môi trường:** Laptop AMD Ryzen 5 5500U (6 nhân / 12 luồng), Windows 10 + WSL2 Ubuntu (giới hạn 4 GB RAM, 6 CPU), Docker Engine 29.9.3 (Docker Desktop, WSL integration), Python 3.11. Part 7 Bước 1 chạy agent trực tiếp trong WSL; Bước 2 chạy stack Docker Compose `-p k5-day14-observability` (Nginx → 1–3 agent, Prometheus, Grafana, Jaeger, Locust).

Mọi số liệu dưới đây lấy từ file đã nộp: `reports/*.json` (load test, eval), `benchmark-results/*.csv` (Locust), ảnh trong `screenshots/`, và output Prometheus/Jaeger API ghi lúc chạy.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | LLM mock trả lời trong ~150–220 ms (p50 ≈ 187 ms) và overhead của agent rất nhỏ, nên 500 ms để dư hơn 2 lần cho mạng và tải bình thường. Với ngưỡng này, cấu hình mặc định (slow 3%) vẫn PASS ở concurrency ≤ 50; khi slow-rate lên 10% hoặc hệ thống quá tải (c=100) thì vi phạm, đúng là lúc người dùng thấy chậm. Đặt theo p95 vì p99 bị 3% request chậm 1,7 s chi phối nên dao động quá mạnh. |
| Error rate `/ask` | < 2 % | Mock LLM có `MOCK_ERROR_RATE=0.01` (1% lỗi 503 upstream) là "nền" không tránh được. Lấy 2% để có biên cho dao động thống kê (300 request: 2–8 lỗi), còn sự cố thật (vd 20% ở thí nghiệm A) thì vượt rất xa. Alert `AgentHighErrorRate` dùng ngưỡng 5% / 1 phút để tránh báo nhầm do nhiễu. |
| Accuracy trên golden dataset | ≥ 85 % (gate trong CI), mục tiêu 100 % | 85% là ngưỡng chặn merge: chấp nhận 3/23 câu sai trong lúc thử prompt/model mới, nhưng chặn ngay một bản như v1 (4,3%). Cộng thêm điều kiện **không có regression** so với baseline, nên câu đã đúng không được phép sai lại. Bản hiện tại đạt 23/23 (100%). |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

`python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.42 | 186.8 | 219.0 | 1696.8 | 2.00 % (6×503) | ✅ PASS |
| 10 | 37.33 | 191.4 | 220.2 | 1684.9 | 0.67 % (2×503) | ✅ PASS |
| 50 | 100.02 | 208.8 | 361.1 | 1752.7 | 1.33 % (4×503) | ✅ PASS |
| 100 | 45.70 | 672.3 | 4128.2 | 4690.7 | 2.67 % (8×503) | ❌ FAIL (p95, error rate) |

**Nhận xét:** RPS gần như tuyến tính ở giai đoạn đầu: c=1 → 10 tăng 8,4 lần (4,42 → 37,33), vì mỗi request phần lớn thời gian là *chờ* LLM (I/O, ~190 ms). Agent async xử lý song song nhiều request mà latency không đổi (p50 ~190 ms), nên RPS ≈ concurrency / latency. Từ c=10 → 50 chỉ tăng 2,7 lần (lẽ ra 5 lần) và p95 bắt đầu tăng (220 → 361 ms): event loop của **một process uvicorn** (1 core Python, cộng JSON, Prometheus và tracing) bắt đầu bão hoà. Ở c=100 hệ thống **quá tải**: request xếp hàng trong event loop và socket backlog, p50 tăng 3,5 lần (672 ms), p95 lên 4,1 s, còn RPS *giảm* xuống 45,7 vì 300 request cùng chờ, cộng thêm chi phí chuyển ngữ cảnh. Kết luận: điểm làm việc an toàn của 1 instance là khoảng 50 request đồng thời (~100 RPS). Muốn hơn thế thì phải scale ngang và giới hạn concurrency ở LB (ví dụ trả 429 hoặc 503 sớm thay vì để latency tăng không kiểm soát).

### 2.2 Tail latency

Concurrency 20, 300 request, khởi động lại v2 với từng giá trị `MOCK_SLOW_RATE`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) | RPS | SLO |
|---|---|---|---|---|---|
| 0 | 191.4 | 221.2 | 242.4 | 101.18 | ✅ PASS |
| 0.03 (mặc định) | 256.1 | 236.7 | 1684.6 | 68.23 | ✅ PASS |
| 0.1 | 380.2 | 1699.1 | 1718.0 | 44.60 | ❌ FAIL (p95) |

**Nhận xét:**
- **p99 nhạy nhất.** Chỉ cần 3% request chậm (~1,7 s) là p99 nhảy từ 242 lên 1685 ms (gấp 7 lần), trong khi avg chỉ tăng 34% (191 → 256) và p95 gần như không đổi (vì 3% < 5%). Khi slow-rate lên 10% (> 5%), đến lượt **p95** nhảy lên 1,7 s.
- **Avg "pha loãng" tail.** Avg = 0,9 × 190 + 0,1 × 1700 ≈ 380 ms: con số này vẫn trông "ổn" (< 500 ms), trong khi thực tế cứ 10 người dùng thì 1 người phải chờ gần 2 giây. SLO đặt theo average sẽ báo xanh trong lúc 10% người dùng có trải nghiệm tệ. Average cũng dễ bị một vài outlier cực lớn kéo lệch theo cả hai chiều. Percentile trả lời đúng câu hỏi "x% người dùng nhận được phản hồi trong bao lâu", nên SLO đặt theo **p95** (hoặc p99 cho luồng quan trọng).
- RPS cũng giảm theo slow-rate (101 → 44,6) vì load test là closed-loop: worker bị giữ bởi request chậm thì không gửi request tiếp được.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | **100 % (23/23)** |
| Avg tokens / câu | 56.3 | 108.7 | 117.2 |
| Tổng cost (USD) | 0.000393 | 0.000756 | 0.000822 |
| Cost / câu đúng (USD) | 0.00039255 | 0.00003599 | 0.00003572 |
| Latency p95 (ms) | 190.1 | 220.0 | 216.1 |
| Quality gate | ❌ FAIL (exit 1, accuracy < 80%) | ✅ PASS (20 câu cải thiện, 0 regression so với v1) | ✅ PASS (`--min-accuracy 1.0`, 2 câu cải thiện, 0 regression so với v2) |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json` (`--baseline reports/eval_v1.json`), `reports/eval_v2_fixed.json` (`--min-accuracy 1.0 --baseline reports/eval_v2.json`).

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` | `token`, `hết hạn`, `chữ ký` | Thêm entry KB với keyword `["jwt", "api key"]`: so sánh API key (chuỗi bí mật cố định, không tự hết hạn, thu hồi bằng cách đổi key) với JWT (token có chữ ký số HS256/RS256, chứa claim user và thời điểm hết hạn `exp`, server xác minh chữ ký mà không cần tra DB). |
| `scaling-03` | `cpu`, `memory`, `custom metrics` | Thêm entry KB với keyword `["hpa", "kubernetes", "autoscal"]`: HPA tự tăng/giảm số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi). **Cố ý không dùng keyword `scale`**, vì nó trùng với câu `scaling-01` và sẽ làm retrieval trả nhầm tài liệu (gây regression). |

Sửa ở `develop/utils/mock_llm.py` và chép sang `production/utils/mock_llm.py`, để stack Docker cũng trả lời đúng. Không sửa `golden_dataset.jsonl`.

**Quality gate chặn merge:** chạy v1 với `--baseline reports/eval_v2.json` cho **exit code 1**: accuracy 4,3% < 80%, và 20 câu regression (`docker-01`, `probe-01`, `security-01`, `obs-01`…). Job CI [`.github/workflows/eval.yml`](.github/workflows/eval.yml) chạy trên mỗi pull request:
1. Bật agent v2 với `MOCK_ERROR_RATE=0` và `MOCK_SLOW_RATE=0`, để kết quả không bị nhiễu ngẫu nhiên.
2. Chạy `python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`.
3. `run_eval.py` thoát với exit code 1 khi accuracy < 85% **hoặc** có câu regression, nên step fail, job fail, và PR bị chặn khi nhánh `main` bật *Require status checks to pass*. Report JSON được upload làm artifact để xem câu nào sai.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- v2 (có retrieval) đưa context tài liệu vào prompt nên tốn **~1,9 lần token mỗi câu** (56 → 109) và **~1,9 lần tổng cost**. Latency p95 tăng khoảng 30 ms (+16%), do bước `retrieve_context` cộng với prompt dài hơn.
- Đổi lại, accuracy tăng từ 4% lên 91% (rồi 100%). Vì vậy **cost / câu đúng của v2 rẻ hơn ~11 lần** (0,000393 → 0,000036 USD). Đó là lý do so sánh theo "cost / câu đúng" chứ không theo "cost / câu": v1 rẻ mỗi câu nhưng gần như mọi câu trả lời đều vô dụng.
- Sửa KB thêm ~8% token (108,7 → 117,2) để lấy 2 câu cuối, và cost / câu đúng còn giảm nhẹ. Latency gần như không đổi.

---

## 4. Observability stack (production)

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png). Lúc chụp: Locust 30 users, 2 instance; dashboard hiện 18,4 req/s, error rate 0,69%, p95 295 ms, saturation 3, 2 instance up, LLM cost $1,80.
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png). `AgentHighErrorRate` và `AgentHighLatencyP95` **cùng FIRING** (sau thí nghiệm A và B).

Trước khi làm thí nghiệm, đã chạy unit test cho alert rules: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### Exercise 7.4 — đọc dashboard (2 agent, Locust 30 users, cửa sổ 1 phút)

| Chỉ số (PromQL) | Giá trị |
|---|---|
| p50 / p95 / p99 `/ask` | 177 ms / 294 ms / 1835 ms |
| avg `/ask` | 235 ms |
| Error rate `/ask` | 0,88 % |
| Req/s theo instance | 9,40 và 9,24 |
| Tokens/s | input 1458, output 778 |
| In-flight | 8 |
| Instances up | 2 |

1. **p95 và avg là bao nhiêu, khoảng cách nói lên điều gì?** p95 = 294 ms, avg = 235 ms, p50 = 177 ms, nhưng p99 = 1835 ms. Avg cao hơn p50 khoảng 60 ms, dù 95% request đều nhanh hơn 294 ms, vì nó bị một nhóm nhỏ request rất chậm (~3% `MOCK_SLOW_RATE`, ~1,7 s) kéo lên. Phân phối latency **lệch phải, có đuôi dài**. Avg không mô tả được trải nghiệm của ai cả: đa số người dùng nhanh hơn avg, còn số ít thì chậm gấp 7 lần. Phải nhìn p95/p99 mới thấy tail.
2. **Nginx có chia đều traffic không?** Có. 9,40 so với 9,24 req/s, lệch < 2%. Nginx dùng round-robin trên upstream `agent:8000 resolve` (Docker DNS trả về IP của cả 2 container). Panel "Requests/s theo instance" là 2 đường gần như chồng lên nhau.
3. **Vì sao input tokens/s lớn hơn output tokens/s?** Input ≈ 1458 tok/s, gần gấp đôi output (778 tok/s). Agent v2 làm RAG: prompt gửi LLM gồm câu hỏi **cộng với context được retrieve từ knowledge base** cộng với system prompt, nên dài hơn câu trả lời ngắn gọn. Ví dụ 1 response mẫu: `usage = {input_tokens: 81, output_tokens: 46}`. Với LLM thật, đây là chỗ tối ưu chi phí (cắt bớt context, cache prompt). Giá output thường đắt hơn input, nên cần theo dõi cả hai.
4. **`curl http://localhost:8080/metrics` trả về gì? Vì sao?** Trả về **`HTTP/1.1 404 Not Found`**. Nginx có rule `location = /metrics { return 404; }`, nên endpoint metrics không lộ ra Internet: nó chứa thông tin nội bộ (route, version, model, cost, số instance) và có thể bị dùng để dò hệ thống hoặc spam làm tốn tài nguyên. Prometheus vẫn scrape được vì gọi **thẳng** từng container `agent:8000/metrics` trong mạng Docker nội bộ (DNS service discovery), không đi qua Nginx.

Ghi chú thêm (Exercise 7.1):
- Ví dụ từng loại metric: Counter `http_requests_total`, Gauge `agent_in_flight_requests` và `agent_info`, Histogram `http_request_duration_seconds`.
- Đã thêm Histogram `agent_question_length_chars` (buckets 20…2000). Dùng histogram vì độ dài câu hỏi có vô số giá trị; nếu đưa nó vào label thì cardinality sẽ bùng nổ.
- Label `route` dùng template `route.path`: khi thử gọi 3 URL "bot" (`/wp-admin`, `/.env`, `/a1b2c3`), tất cả đều gom vào **một** series `route="unmatched", status="404"`. Nếu dùng URL thật thì mỗi URL lạ sinh ra một time series mới, làm Prometheus phình bộ nhớ (cardinality explosion).

### 4.2 Thí nghiệm "phá hệ thống"

Giữ Locust headless 30 users chạy nền suốt bài, 2 instance agent. Mỗi thí nghiệm: ghi `.env` (từ `.env.example`, chỉ sửa biến cần đổi), chạy `docker compose -p k5-day14-observability up -d --no-deps agent`, rồi poll `GET /api/v1/alerts` của Prometheus mỗi 5 giây. T0 là lúc container agent mới đã chạy.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (giá trị ≈ 21,5%) | pending sau **25 s**, firing sau **85 s** (cửa sổ `rate[1m]` cần ~25 s để vượt 5%, rồi `for: 1m`) | Golden Signals → **Error rate** chuyển đỏ (~20%); LLM → *calls ok/error*; Jaeger có trace lỗi với event `exception` |
| B | `MOCK_SLOW_RATE=0.3` (giữ error 0.2) | `AgentHighLatencyP95` (p95 ≈ 1,90 s) | pending sau **25 s**, firing sau **145 s** (`for: 2m`) | Latency & Traffic → **p95/p99** vọt lên ~1,7–1,9 s, trong khi avg chỉ tăng ít; LLM p95 cũng tăng, nên LLM là nguyên nhân |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (cost ≈ **$35,4/giờ** theo cửa sổ 1m, $17,1/giờ theo 5m; trước đó $2,04/giờ) | pending sau **25 s**, firing sau **145 s** (`for: 2m`). Lưu ý: ngay trước thí nghiệm, alert này đã *pending* ở tải nền ($2,04/giờ) | LLM → **Cost USD/giờ** tăng ~17 lần (cost/request 0,0006 USD so với ~0,000035 USD của model small); Golden Signals → Cost 1h |
| D | `docker pause` 1 instance | `AgentInstanceDown` cho `172.18.0.3:8000` (`up` = 0, instance còn lại = 1) | pending sau **10 s**, firing sau **40 s** (`for: 30s`) | Golden Signals → **Instances** giảm 2 → 1; Instances → req/s chỉ còn 1 đường. Panel **Error rate vẫn xanh** (max 1,66%) dù Nginx trả **29 lỗi 504** trong ~40 s |
| E | `AGENT_VERSION=v1` | **Không có alert nào** | — | LLM → **tokens/s** giảm (output 778 → 363 tok/s), **cost/giờ giảm** ($2,04 → $1,16, nên `LLMCostBurnRateHigh` còn *tự tắt*); bảng version đổi sang v1. Error rate 1,36%, p95 199 ms: mọi thứ "xanh" hơn |

Quan sát thêm:
- **Alert chi phí quá nhạy.** Ngay ở tải nền (30 users, v2, model small), `LLMCostBurnRateHigh` đã **firing** từ trước khi làm thí nghiệm (≈ $2,46/giờ > ngưỡng $2/giờ). Ngưỡng $2/giờ được viết cho tải nhẹ hơn. Production thật nên đặt ngưỡng theo ngân sách: daily budget / 24 cộng biên an toàn, hoặc theo *burn rate* so với budget, chứ không dùng một con số cố định.
- **Ở thí nghiệm B, `LLMCostBurnRateHigh` tự tắt** sau ~45 s. Request chậm hơn nên mỗi user gửi ít request hơn (Locust closed-loop), và 20% request lỗi không tốn token. Chi phí giảm không có nghĩa là hệ thống khoẻ hơn.
- **`for:` chống báo động giả.** Lúc recreate container ở thí nghiệm A, `AgentInstanceDown` lên *pending* khoảng 5 s: Prometheus còn giữ IP cũ trong chu kỳ refresh DNS 10 s, nên scrape IP cũ thất bại. Nhờ `for: 30s` nên alert không chuyển sang firing.
- **Thí nghiệm D, 504 ở Nginx:** log Nginx 09:44–09:45 có 881×200, 8×503 và **29×504**, dạng `upstream timed out ... upstream: "http://172.18.0.3:8000/ask"`. Mỗi 504 là một request treo đủ `proxy_read_timeout 10s` ở instance bị pause, sau đó Nginx tạm loại instance đó. Locust thấy p100 ~10 005 ms. Sau `docker unpause` khoảng 15 s, `AgentInstanceDown` tự hết.
- **Thí nghiệm C:** khi recreate container, `AgentInstanceDown` lại *pending* 10 s cho cả 2 IP (Prometheus còn giữ DNS cũ), và `for: 30s` một lần nữa chặn báo động giả.
- **Thí nghiệm E:** sau 6 phút (để cửa sổ cost 5m trôi qua) **không có alert nào active**. Cost giảm xuống dưới ngưỡng nên `LLMCostBurnRateHigh` vốn đang pending còn tắt hẳn: bản *tệ hơn* lại trông *rẻ hơn và khoẻ hơn* trên dashboard.
- Sau thí nghiệm: xoá `.env`, stack trở về v2 mặc định.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Metric hạ tầng (latency, error rate, up, saturation) chỉ trả lời câu hỏi "service có *trả lời* không, có nhanh không". v1 trả lời **nhanh và đều 200 OK**, chỉ là trả lời **sai**. Vì vậy dashboard hạ tầng vẫn xanh, thậm chí trông "tốt hơn" vì token và cost giảm. Muốn bắt lỗi chất lượng phải đo chính chất lượng:
1. **Chặn trước khi deploy:** quality gate trong CI ([`.github/workflows/eval.yml`](.github/workflows/eval.yml)). Chạy golden dataset trên mỗi PR; accuracy < 85% hoặc có regression thì exit 1 và không được merge. Ở bài này, gate chặn v1 với exit code 1 và 20 câu regression.
2. **Giám sát chất lượng khi chạy:**
   - Chạy eval định kỳ (synthetic/canary eval) trên môi trường production, ví dụ mỗi 15 phút gọi 23 câu golden. Đẩy accuracy thành metric `agent_eval_accuracy` rồi đặt alert, ví dụ `agent_eval_accuracy < 0.85 for 15m`.
   - Theo dõi các metric "đại diện" cho chất lượng: retrieval hit ratio, tỉ lệ câu trả lời mặc định hoặc fallback ("Câu hỏi của bạn đã được nhận"), độ dài câu trả lời, phản hồi 👍/👎 của người dùng. Ở thí nghiệm E, output tokens/s **giảm hơn một nửa** (778 → 363 tok/s), cost/giờ giảm ($2,04 → $1,16), và bảng version đổi sang v1 (v1 không retrieval nên retrieval hit ratio cũng mất). Alert theo **độ lệch so với baseline** (anomaly) sẽ bắt được điều này.
   - Gắn nhãn `version` lên mọi metric và chạy canary hoặc A/B: so sánh chất lượng giữa bản cũ và bản mới trước khi chuyển 100% traffic.

**Vì sao lỗi 504 ở Nginx (thí nghiệm D) không hiện trên dashboard của agent?** Request bị treo ở instance đang pause, nên Nginx chờ hết `proxy_read_timeout 10s` rồi tự trả 504 cho client. Agent bị đóng băng nên không bao giờ xử lý xong request đó, và không có dòng nào trong `http_requests_total` của nó. Instance còn lại chỉ thấy các request thành công. Kết quả là panel error rate (tính từ metric của agent) vẫn xanh, dù người dùng đã nhận lỗi. Metric chỉ phản ánh những gì *chính service đó* nhìn thấy. Production cần thêm metric ở tầng load balancer (status code của Nginx/ingress, `upstream_response_time`) và **synthetic check từ bên ngoài** (blackbox probe gọi `/ask` như người dùng thật).

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được (Jaeger API v3, service `observable-agent`, `duration_min=1s`, 40 phút gần nhất):

- `trace_id`: `41e4b3f14c84d69782c3e31a24375b7e`
- Tổng thời gian: **1758.8 ms**
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (**1689.2 ms, 96.0 %**; `gen_ai.usage.input_tokens=76`, `output_tokens=39`). Các span còn lại: `agent.run` 1754.3 ms (span cha), `agent.retrieve_context` 64.8 ms (3.7 %). Kết luận: request chậm do upstream LLM, không phải do code của agent. Hướng xử lý: timeout và retry có hedging, cache câu trả lời, hoặc model nhanh hơn, chứ không phải tối ưu code retrieval.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 41e4b3f14c84d69782c3e31a24375b7e`):

```json
{"ts": "2026-11-26T08:59:30.254498+00:00", "level": "INFO", "service": "observable-agent", "instance": "28e8a422a88b", "event": "llm_call", "trace_id": "41e4b3f14c84d69782c3e31a24375b7e", "span_id": "7b2ddf545cfe9c09", "request_id": "3046ecd89f54", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 76, "output_tokens": 39, "cost_usd": 3.5e-05, "llm_ms": 1687.9}
{"ts": "2026-11-26T08:59:30.255000+00:00", "level": "INFO", "service": "observable-agent", "instance": "28e8a422a88b", "event": "http_request", "trace_id": "41e4b3f14c84d69782c3e31a24375b7e", "span_id": "7b2ddf545cfe9c09", "request_id": "3046ecd89f54", "method": "POST", "route": "/ask", "status": 200, "ms": 1757.9}
```

Dòng `http_request` ghi `ms: 1757.9`, khớp với tổng thời gian của trace. Cùng một `span_id`, nên log và trace là cùng một request. Ảnh: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png).

**Trace lỗi** ([`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)):
- `trace_id`: `19fb809748e2eecc9747acc5f3751d39`, tổng 112.8 ms, HTTP 503.
- Span `chat mock-llm-small` (93.7 ms) có status `ERROR` và event `exception`: `utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`.
- Log tương ứng:

```json
{"ts": "2026-11-26T09:02:27.988045+00:00", "level": "INFO", "service": "observable-agent", "instance": "28e8a422a88b", "event": "llm_error", "trace_id": "19fb809748e2eecc9747acc5f3751d39", "span_id": "b710fb25c574c449", "request_id": "1d9977de39a2", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-26T09:02:27.988360+00:00", "level": "INFO", "service": "observable-agent", "instance": "28e8a422a88b", "event": "http_request", "trace_id": "19fb809748e2eecc9747acc5f3751d39", "span_id": "b710fb25c574c449", "request_id": "1d9977de39a2", "method": "POST", "route": "/ask", "status": 503, "ms": 112.2}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

`docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>` (dòng `Aggregated` trong `benchmark-results/scale_<n>_stats.csv`)

| Số instance | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) | avg (ms) | Failures |
|---|---|---|---|---|---|---|---|
| 1 | 7842 | 66.00 | 190 | 280 | 1700 | 222.5 | 78 (0.99 %, toàn bộ là 503 từ mock LLM) |
| 3 | 7813 | 65.81 | 190 | 240 | 1700 | 219.2 | 82 (1.05 %, toàn bộ là 503 từ mock LLM) |

**Giải thích kết quả:** Scale từ 1 lên 3 instance **gần như không cải thiện gì**: RPS giữ ~66, p50 và p99 không đổi, p95 chỉ giảm 280 → 240 ms. Lý do là **không có nút thắt nào ở agent để scale giải quyết**:
1. **Tải bị giới hạn bởi chính Locust (closed-loop).** Mỗi user chờ `between(0.5, 2)` giây (trung bình 1,25 s) giữa 2 request. Thông lượng tối đa ≈ 100 users / (1,25 s + 0,22 s latency) ≈ **68 req/s**, khớp với 66 đo được. Thêm instance không làm user gửi nhanh hơn.
2. **Latency bị chi phối bởi LLM upstream.** LLM p95 = 198 ms trong khi http `/ask` p95 = 294 ms. Phần chậm của mọi request (kể cả 3% request ~1,7 s tạo ra p99 = 1700 ms) nằm ở lời gọi LLM, không phụ thuộc số instance. Scale agent không làm LLM nhanh hơn.
3. **Agent không bị bão hoà.** CPU ≈ 0,14 core mỗi instance, và ~66 RPS thấp hơn nhiều so với ~100 RPS mà 1 instance chịu được (mục 2.1, c=50). p95 giảm nhẹ khi có 3 instance vì mỗi event loop phải xử lý ít request đồng thời hơn, bớt xếp hàng.

Muốn thấy scaling có tác dụng thì phải tạo tải vượt sức 1 instance: nhiều user hơn hoặc wait time ngắn hơn (ví dụ ≥ 150 user với `constant(0)`). Lúc đó 1 instance sẽ giống c=100 ở mục 2.1 (p95 vài giây), còn 3 instance sẽ chia tải. Với LLM thật, nút thắt thường là **rate limit / TPM của nhà cung cấp LLM**, nên cần cache, batching hoặc tăng quota chứ không chỉ thêm pod.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Đặt SLO theo percentile, đọc kèm cả phân phối.** Chỉ 3% request chậm đã đẩy p99 lên gấp 7 lần trong khi avg gần như không đổi. Benchmark cũng phải hiểu mô hình tải: load test closed-loop (Locust có wait time) tự giới hạn RPS, nên "scale không cải thiện" có thể là do công cụ đo chứ không phải do hệ thống.
  2. **Hạ tầng xanh không có nghĩa là sản phẩm đúng.** Đổi sang v1 làm accuracy rơi từ 100% xuống 4%, nhưng không alert nào kêu. Chất lượng phải có quality gate riêng (eval với golden dataset, exit code chặn merge) và metric chất lượng riêng khi chạy. Tương tự, lỗi 504 ở Nginx không hiện trên metric của agent: mỗi tầng chỉ thấy phần của mình.
  3. **Metrics, logs và traces bổ sung cho nhau, và alert cũng là code.** Metric báo *có* vấn đề (p95 vượt SLO). Trace chỉ ra *ở đâu* (span LLM chiếm 96%). Log, nối với trace bằng `trace_id`, cho chi tiết (tokens, cost, request_id). Alert phải có `for:` để tránh báo nhầm (instance down ~5 s lúc recreate), phải có unit test (promtool), và ngưỡng phải được kiểm chứng trên tải thật: ngưỡng cost $2/giờ đã firing ngay ở tải nền.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** chạy eval định kỳ, đẩy `agent_eval_accuracy` thành metric và alert khi < 0,85; theo dõi retrieval hit ratio, tỉ lệ câu trả lời fallback, và feedback người dùng; alert khi các chỉ số này lệch đột ngột so với baseline (bắt được sự cố kiểu E).
  - **Tầng biên:** metric status code và `upstream_response_time` của Nginx/ingress; blackbox/synthetic probe gọi `/ask` từ bên ngoài; alert theo tỉ lệ 5xx/504 ở LB (bắt được sự cố kiểu D).
  - **SLO burn-rate alert nhiều cửa sổ** (ví dụ 1h/5m và 6h/30m theo error budget 30 ngày) thay cho ngưỡng cố định, để bớt nhiễu mà vẫn bắt nhanh sự cố lớn.
  - **Chi phí theo ngân sách:** cost/giờ so với daily budget (giống `cost_guard` ở Part 6, $1/ngày), cost theo version hoặc model, token/request p95. Alert khi dự báo vượt budget tháng.
  - **Saturation và phụ thuộc:** event loop lag, in-flight so với giới hạn, độ dài hàng đợi; latency và lỗi của Redis; tỉ lệ 429 (rate limit) và 402 (hết budget); thời gian và số lần retry LLM. Cấu hình trace sampling (ví dụ tail-based: giữ 100% trace lỗi hoặc chậm, 10% còn lại) để kiểm soát chi phí lưu trữ.
