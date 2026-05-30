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
    """Resolve client IP, trusting forwarded headers only from trusted proxies."""
    direct_ip = request.client.host if request.client and request.client.host else "unknown"

    if not config.TRUST_PROXY_HEADERS:
        return direct_ip

    if config.TRUSTED_PROXY_IPS and direct_ip not in config.TRUSTED_PROXY_IPS:
        return direct_ip

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip

    return direct_ip


def _prune_rate_limit_store(cutoff: float) -> None:
    """Bound memory usage by pruning expired/oldest rate-limit buckets."""
    stale_keys = [ip for ip, bucket in _rate_limit_store.items() if not bucket or bucket[-1] < cutoff]
    for ip in stale_keys:
        _rate_limit_store.pop(ip, None)

    overflow = len(_rate_limit_store) - config.MAX_RATE_LIMIT_BUCKETS
    if overflow <= 0:
        return

    # Evict the least-recently-seen buckets first.
    oldest_ips = sorted(
        _rate_limit_store,
        key=lambda ip: _rate_limit_store[ip][-1] if _rate_limit_store[ip] else 0,
    )[:overflow]
    for ip in oldest_ips:
        _rate_limit_store.pop(ip, None)
    return "unknown"


def is_rate_limited(client_ip: str) -> bool:
    """Apply a sliding-window in-memory rate limit per client IP."""
    now = time.time()
    cutoff = now - config.RATE_LIMIT_WINDOW_SECONDS
    with _rate_limit_lock:
        _prune_rate_limit_store(cutoff)
        bucket = _rate_limit_store.setdefault(client_ip, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= config.RATE_LIMIT_MAX_REQUESTS:
            return True
        bucket.append(now)
        _prune_rate_limit_store(cutoff)
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
