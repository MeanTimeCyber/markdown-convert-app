from __future__ import annotations

import logging
import time
import uuid
from collections import deque
import threading

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import config

# Lock guarding concurrent access to in-memory rate-limit buckets.
_rate_limit_lock = threading.Lock()
# Per-IP request timestamps used for sliding-window rate limiting.
_rate_limit_store: dict[str, deque[float]] = {}


logger = logging.getLogger("md_convert")


def get_client_ip(request: Request) -> str:
    """Resolve the best-effort client IP, honoring proxy forwarding headers."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def is_rate_limited(client_ip: str) -> bool:
    """Apply a sliding-window in-memory rate limit per client IP."""
    now = time.time()
    cutoff = now - config.RATE_LIMIT_WINDOW_SECONDS
    with _rate_limit_lock:
        bucket = _rate_limit_store.setdefault(client_ip, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= config.RATE_LIMIT_MAX_REQUESTS:
            return True
        bucket.append(now)
    return False


def register_security_middleware(app: FastAPI) -> None:
    """Attach security headers, enforce rate limits, and emit request logs."""

    @app.middleware("http")
    async def security_and_logging_middleware(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        client_ip = get_client_ip(request)

        if request.url.path != "/health" and is_rate_limited(client_ip):
            logger.warning(
                "rate_limited",
                extra={
                    "event": "rate_limited",
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "method": request.method,
                    "path": request.url.path,
                    "detail": "Too many requests in rate limit window.",
                    "status_code": 429,
                },
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please retry shortly."},
                headers={"Retry-After": str(config.RATE_LIMIT_WINDOW_SECONDS)},
            )

        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        logger.info(
            "request_complete",
            extra={
                "event": "request_complete",
                "request_id": request_id,
                "client_ip": client_ip,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        return response
