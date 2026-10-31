import hashlib
import hmac
import logging
import os
import time
from typing import Optional
import requests
from config import config

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
COOKIE_NAME = "jungle_cf_clearance"
COOKIE_MAX_AGE = 900  # 15 minutes

def _get_secret_key() -> bytes:
    key = config.SECRET_KEY or os.getenv("SECRET_KEY", "")
    if config.ENV in ("prod", "production") and not key:
        raise ValueError("SECRET_KEY environment variable is required in production.")
    if not key:
        key = "jungle-default-secret-change-in-prod"
    return key.encode("utf-8")


def generate_clearance_token(client_ip: str) -> str:
    """Generates a signed HMAC clearance token bound to timestamp and client IP."""
    secret = _get_secret_key()
    ts = str(int(time.time()))
    payload = f"{client_ip}:{ts}".encode("utf-8")
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def verify_clearance_token(token: str, client_ip: str, max_age: int = COOKIE_MAX_AGE) -> bool:
    """Verifies that an HMAC clearance token is authentic, unexpired, and IP-bound."""
    secret = _get_secret_key()
    if not token or "." not in token:
        return False
    try:
        ts_str, sig = token.split(".", 1)
        ts = int(ts_str)
        now = int(time.time())
        if now - ts > max_age or ts > now + 60:
            return False  # Expired or from future
        payload = f"{client_ip}:{ts_str}".encode("utf-8")
        expected_sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def verify_turnstile_response(turnstile_token: Optional[str], client_ip: str) -> bool:
    """
    Verifies Cloudflare Turnstile token server-side.
    If TURNSTILE_SECRET is not configured, verification is skipped (dev mode).
    """
    if not config.TURNSTILE_SECRET:
        return True

    if not turnstile_token:
        return False

    try:
        resp = requests.post(
            TURNSTILE_VERIFY_URL,
            data={
                "secret": config.TURNSTILE_SECRET,
                "response": turnstile_token,
                "remoteip": client_ip
            },
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            return bool(data.get("success", False))
        return False
    except Exception as e:
        logger.warning(f"Turnstile verification request failed: {e}")
        return False

