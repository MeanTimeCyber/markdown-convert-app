from __future__ import annotations

from fastapi import FastAPI

from app import config
from app.logging_setup import configure_logging
from app.middleware import security as security_middleware
from app.middleware.security import register_security_middleware
from app.routes.convert import router
from app.services.conversion import build_pandoc_command
from app.services.uploads import (
    ensure_md_extension,
    extract_zip_to_dir,
    pick_main_markdown,
    sanitize_relative_path,
)

app = FastAPI(title="Markdown Convert", version="0.1.0")
logger = configure_logging()
register_security_middleware(app)
app.include_router(router)

# Load and validate template file if configured
try:
    app.state.template_file = config.get_template_file()
except (FileNotFoundError, ValueError) as e:
    raise RuntimeError(f"Template file configuration error: {e}") from e

logger.info(
    "app_startup",
    extra={
        "event": "app_startup",
        "detail": "Application initialized.",
        "template_configured": bool(app.state.template_file),
    },
)

# Re-export shared helpers/constants to keep external imports stable.
RATE_LIMIT_MAX_REQUESTS = config.RATE_LIMIT_MAX_REQUESTS
_rate_limit_store = security_middleware._rate_limit_store
