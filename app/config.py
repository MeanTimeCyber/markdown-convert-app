from __future__ import annotations

import os

# Allowed output formats exposed by the API.
ALLOWED_OUTPUTS = {"docx", "pdf"}
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
# Hard timeout applied to pandoc conversion subprocesses.
REQUEST_TIMEOUT_SECONDS = 120
# Default LaTeX engine used by pandoc for PDF output.
PDF_ENGINE = os.getenv("PDF_ENGINE", "xelatex")
