from __future__ import annotations

import os
from pathlib import Path

# Whether PDF conversion support is enabled.
ENABLE_PDF = os.getenv("ENABLE_PDF", "true").lower() in {"1", "true", "yes", "on"}
# Allowed output formats exposed by the API.
ALLOWED_OUTPUTS = {"docx", "pdf"} if ENABLE_PDF else {"docx"}
# Allowed extensions for the user-selected primary markdown document.
ALLOWED_MD_EXTENSIONS = {".md", ".markdown", ".mdown"}
# Maximum size for any single uploaded file.
MAX_FILE_BYTES = 20 * 1024 * 1024
# Maximum combined size for all uploaded request files.
MAX_TOTAL_BYTES = 100 * 1024 * 1024
# Maximum extracted size for ZIP contents after decompression.
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
# Maximum number of entries allowed in a ZIP archive.
MAX_ZIP_ENTRIES = 2000
# Maximum directory nesting depth allowed for extracted ZIP paths.
MAX_ZIP_DEPTH = 12
# Maximum allowed file_size/compress_size ratio to mitigate zip bombs.
MAX_COMPRESSION_RATIO = 100
# Sliding-window duration used by in-memory rate limiting.
RATE_LIMIT_WINDOW_SECONDS = 60
# Max requests allowed from one IP within the rate limit window.
RATE_LIMIT_MAX_REQUESTS = 30
# Whether to trust X-Forwarded-For style proxy headers.
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in {"1", "true", "yes", "on"}
# Comma-separated list of proxy source IPs/hosts trusted to set forwarded headers.
TRUSTED_PROXY_IPS = {
    part.strip()
    for part in os.getenv("TRUSTED_PROXY_IPS", "").split(",")
    if part.strip()
}
# Hard cap on unique IP buckets to limit memory growth under IP churn.
MAX_RATE_LIMIT_BUCKETS = int(os.getenv("MAX_RATE_LIMIT_BUCKETS", "10000"))
# Hard timeout applied to pandoc conversion subprocesses.
REQUEST_TIMEOUT_SECONDS = 120
# Default LaTeX engine used by pandoc for PDF output.
PDF_ENGINE = os.getenv("PDF_ENGINE", "xelatex")
# Minimum log level for application logs.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Whether file logging should be enabled in addition to console logging.
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() in {"1", "true", "yes", "on"}
# File path for rotating application logs.
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "/tmp/md-convert.log")
# Max bytes per log file before rotation.
LOG_FILE_MAX_BYTES = int(os.getenv("LOG_FILE_MAX_BYTES", "10485760"))
# Number of rotated log files to retain.
LOG_FILE_BACKUP_COUNT = int(os.getenv("LOG_FILE_BACKUP_COUNT", "5"))
# Optional template .docx file path for DOCX output styling.
TEMPLATE_DOC_PATH = os.getenv("TEMPLATE_DOC_PATH")


def get_template_file() -> Path | None:
    """Load and validate the template file if configured.
    
    Returns the Path if TEMPLATE_DOC_PATH is set and file exists, None otherwise.
    Raises FileNotFoundError if path is set but file does not exist.
    """
    if not TEMPLATE_DOC_PATH:
        return None
    
    template_path = Path(TEMPLATE_DOC_PATH)
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {TEMPLATE_DOC_PATH}")
    if not template_path.is_file():
        raise ValueError(f"Template path is not a file: {TEMPLATE_DOC_PATH}")
    if template_path.suffix.lower() != ".docx":
        raise ValueError(f"Template must be .docx, got: {template_path.suffix}")
    
    return template_path
