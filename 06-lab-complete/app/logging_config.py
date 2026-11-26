"""
Structured logging (tiêu chí 10): mỗi dòng log của app là đúng 1 JSON object.

Dùng logger riêng tên "agent" (propagate=False) để không lẫn với log của uvicorn.
Thông tin thêm truyền qua `extra=`, ví dụ:
    logger.info("request", extra={"path": "/ask", "status": 200, "ms": 12.3})
"""
import json
import logging
import sys
from datetime import datetime, timezone

# Thuộc tính có sẵn của LogRecord — không đưa vào JSON
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "event": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        # json.dumps escape mọi ký tự xuống dòng → luôn đúng 1 dòng
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("agent")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
