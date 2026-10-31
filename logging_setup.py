import json
import logging
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def mask_token(text: str) -> str:
    """
    Mask tokens and credentials in paths, query strings, and log messages.
    Converts token values to tok_*** and strips userinfo from URLs.
    """
    if not text:
        return ""
    # 1. Mask query parameter ?token=... or &token=...
    s = re.sub(r'([?&]token=)[^&]+', r'\1tok_***', text)
    # 2. Mask path segments like /api/stream/<hex_token> or /api/preview/<hex_token>
    s = re.sub(r'(/api/(?:stream|preview)/)[0-9a-fA-F]{16,}', r'\1tok_***', s)
    # 3. Mask any lingering standalone hex tokens >= 32 chars
    s = re.sub(r'\b[0-9a-fA-F]{32,64}\b', 'tok_***', s)
    # 4. Mask credentials in URLs (e.g. http://user:pass@host)
    s = re.sub(r'://[^:@\s]+:[^@\s]+@', r'://***:***@', s)
    return s


class JSONFormatter(logging.Formatter):
    """
    Standard-library based JSON log formatter for structured output to stdout.
    Honors request_id, extractor, and duration context.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_token(record.getMessage()),
        }

        # Contextual request ID
        req_id = getattr(record, "request_id", None)
        extractor = getattr(record, "extractor", None)

        try:
            from flask import g, has_request_context
            if has_request_context():
                if not req_id:
                    req_id = getattr(g, "request_id", None)
                if not extractor:
                    extractor = getattr(g, "extractor", None)
        except Exception:
            pass

        if req_id:
            log_obj["request_id"] = req_id
        if extractor:
            log_obj["extractor"] = extractor

        # Optional access-log / metrics fields
        for field in ("method", "path", "status", "duration_ms", "client_ip", "extractor_counts"):
            val = getattr(record, field, None)
            if val is not None:
                if field == "path" and isinstance(val, str):
                    val = mask_token(val)
                log_obj[field] = val

        # Exception information
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, ensure_ascii=False)


def setup_logging(log_level: Optional[str] = None):
    """
    Configure the root logger with the JSONFormatter streaming to stdout.
    """
    level_str = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_str, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove pre-existing handlers to prevent duplicated output
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Suppress verbose third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
