# syntax=docker/dockerfile:1
# ============================================================
# Production image cho agent Part 6 (tiêu chí 1)
#   • Multi-stage: builder cài dependencies vào venv, runtime chỉ copy venv + code
#   • Base slim, chạy bằng user non-root, có HEALTHCHECK
#   • CMD lắng nghe theo $PORT, `exec` để uvicorn là PID 1 → nhận SIGTERM khi `docker stop`
# ============================================================

# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements trước code để tận dụng cache layer: sửa code không phải cài lại thư viện
COPY requirements.txt .
RUN pip install -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    WEB_CONCURRENCY=2

RUN groupadd --system app && useradd --system --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app
EXPOSE 8000

# Image slim không có curl → dùng Python để gọi /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=3)" || exit 1

# exec → uvicorn thay thế shell, trở thành PID 1 và nhận SIGTERM trực tiếp.
# --timeout-graceful-shutdown: chờ request đang chạy tối đa 8s, kịp trước SIGKILL (10s) của docker stop.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout-graceful-shutdown 8"]
