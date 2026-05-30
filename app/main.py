from __future__ import annotations

import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

app = FastAPI(title="Markdown Convert", version="0.1.0")
templates = Jinja2Templates(directory="app/templates")

ALLOWED_OUTPUTS = {"docx", "pdf"}
ALLOWED_MD_EXTENSIONS = {".md", ".markdown", ".mdown"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_DEPTH = 12
MAX_COMPRESSION_RATIO = 100
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30
REQUEST_TIMEOUT_SECONDS = 120

_rate_limit_lock = threading.Lock()
_rate_limit_store: dict[str, deque[float]] = {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in (
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "client_ip",
            "event",
            "detail",
        ):
            if hasattr(record, field):
                payload[field] = getattr(record, field)

        return json.dumps(payload)


logger = logging.getLogger("md_convert")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


def sanitize_relative_path(raw_name: str) -> Path:
    normalized = raw_name.replace("\\", "/")
    path = PurePosixPath(normalized)

    clean_parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if clean_parts:
                clean_parts.pop()
            continue
        clean_parts.append(part)

    if not clean_parts:
        return Path("upload.bin")

    return Path(*clean_parts)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _rate_limit_lock:
        bucket = _rate_limit_store.setdefault(client_ip, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            return True
        bucket.append(now)
    return False


def pick_main_markdown(temp_dir: Path) -> Path:
    markdown_files = sorted(
        path.relative_to(temp_dir)
        for path in temp_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in ALLOWED_MD_EXTENSIONS
    )

    if not markdown_files:
        raise HTTPException(
            status_code=400,
            detail="No markdown file found in uploaded ZIP.",
        )

    return markdown_files[0]


def extract_zip_to_dir(zip_path: Path, destination: Path, max_total_bytes: int) -> None:
    total_uncompressed = 0
    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise HTTPException(
                status_code=413,
                detail="ZIP contains too many files.",
            )

        for info in entries:
            rel_path = sanitize_relative_path(info.filename)
            if rel_path.name == "upload.bin":
                continue

            if len(rel_path.parts) > MAX_ZIP_DEPTH:
                raise HTTPException(
                    status_code=400,
                    detail="ZIP path depth exceeds allowed limit.",
                )

            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise HTTPException(
                    status_code=400,
                    detail="ZIP symlinks are not allowed.",
                )

            target = destination / rel_path
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            compressed = max(info.compress_size, 1)
            ratio = info.file_size / compressed
            if ratio > MAX_COMPRESSION_RATIO:
                raise HTTPException(
                    status_code=413,
                    detail="ZIP compression ratio is suspiciously high.",
                )

            total_uncompressed += info.file_size
            if total_uncompressed > max_total_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="Extracted ZIP content exceeds total upload limit.",
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def build_pandoc_command(main_rel: Path, output_name: str, output_format: str) -> list[str]:
    command = [
        "pandoc",
        str(main_rel),
        "-o",
        output_name,
    ]

    if output_format == "pdf":
        # If tectonic is available, this avoids depending on full TeXLive.
        command.extend(["--pdf-engine", "tectonic"])

    return command


def ensure_md_extension(file_name: str) -> None:
    suffix = Path(file_name).suffix.lower()
    if suffix not in ALLOWED_MD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Main file must be a markdown file (.md, .markdown, .mdown).",
        )


async def save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with destination.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{upload.filename}' exceeds per-file limit of {MAX_FILE_BYTES // (1024 * 1024)} MB.",
                )
            out.write(chunk)

    await upload.close()
    return written


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
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/convert")
async def convert(
    output_format: str = Form(...),
    main_file: UploadFile | None = File(default=None),
    project_zip: UploadFile | None = File(default=None),
    assets: list[UploadFile] = File(default_factory=list),
):
    output_format = output_format.lower().strip()
    if output_format not in ALLOWED_OUTPUTS:
        raise HTTPException(status_code=400, detail="Output format must be docx or pdf.")

    temp_dir = Path(tempfile.mkdtemp(prefix="md-convert-"))
    total_written = 0

    try:
        if not main_file and not project_zip:
            raise HTTPException(
                status_code=400,
                detail="Upload either a markdown file or a ZIP package.",
            )

        if project_zip:
            zip_rel = sanitize_relative_path(project_zip.filename or "project.zip")
            zip_target = temp_dir / zip_rel.name
            total_written += await save_upload(project_zip, zip_target, MAX_FILE_BYTES)
            if total_written > MAX_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload exceeds {MAX_TOTAL_BYTES // (1024 * 1024)} MB.",
                )
            if zip_target.suffix.lower() != ".zip":
                raise HTTPException(status_code=400, detail="Project package must be a .zip file.")
            try:
                extract_zip_to_dir(zip_target, temp_dir, MAX_ARCHIVE_BYTES)
            except zipfile.BadZipFile as exc:
                raise HTTPException(status_code=400, detail="Uploaded ZIP is invalid.") from exc

        if main_file:
            ensure_md_extension(main_file.filename or "")
            main_rel = sanitize_relative_path(main_file.filename or "main.md")
            if main_rel.name == "upload.bin":
                main_rel = Path("main.md")
            main_path = temp_dir / main_rel
            total_written += await save_upload(main_file, main_path, MAX_FILE_BYTES)
            if total_written > MAX_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload exceeds {MAX_TOTAL_BYTES // (1024 * 1024)} MB.",
                )
        else:
            main_rel = pick_main_markdown(temp_dir)
            main_path = temp_dir / main_rel

        for upload in assets:
            rel_path = sanitize_relative_path(upload.filename or "asset.bin")
            if rel_path == main_rel:
                continue
            target = temp_dir / rel_path
            total_written += await save_upload(upload, target, MAX_FILE_BYTES)

            if total_written > MAX_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload exceeds {MAX_TOTAL_BYTES // (1024 * 1024)} MB.",
                )

        output_name = f"{main_path.stem}.{output_format}"
        output_path = temp_dir / output_name

        command = build_pandoc_command(main_rel, output_name, output_format)

        result = subprocess.run(
            command,
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
            check=False,
        )

        if result.returncode != 0 or not output_path.exists():
            message = result.stderr.strip() or "Pandoc conversion failed."
            raise HTTPException(status_code=400, detail=message)

        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if output_format == "docx"
            else "application/pdf"
        )

        return FileResponse(
            path=output_path,
            media_type=media_type,
            filename=output_name,
            background=BackgroundTask(shutil.rmtree, temp_dir, True),
        )

    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="Pandoc conversion timed out.") from exc
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise


@app.get("/health")
async def health() -> dict[str, str]:
    pandoc_path = shutil.which("pandoc")
    if not pandoc_path:
        raise HTTPException(status_code=503, detail="pandoc is not installed or not on PATH.")

    return {"status": "ok", "pandoc": pandoc_path}
