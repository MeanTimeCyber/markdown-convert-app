from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from app.config import ALLOWED_OUTPUTS, MAX_ARCHIVE_BYTES, MAX_FILE_BYTES, MAX_TOTAL_BYTES
from app.services.conversion import build_pandoc_command, run_pandoc
from app.services.uploads import (
    ensure_md_extension,
    extract_zip_to_dir,
    pick_main_markdown,
    sanitize_relative_path,
    save_upload,
    validate_markdown_image_references,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("md_convert")
SAFE_DOWNLOAD_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_join_under(base_dir: Path, relative_path: Path) -> Path:
    """Join and validate that resulting path stays inside base_dir."""
    base_resolved = base_dir.resolve()
    candidate = (base_dir / relative_path).resolve()
    if base_resolved not in (candidate, *candidate.parents):
        raise HTTPException(status_code=400, detail="Invalid upload path.")
    return candidate


def sanitize_download_stem(stem: str) -> str:
    """Normalize download filename stem to a conservative safe charset."""
    sanitized = SAFE_DOWNLOAD_CHARS.sub("_", stem).strip("._-")
    return sanitized or "converted-output"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the upload page."""
    template_available = request.app.state.template_file is not None
    return templates.TemplateResponse("index.html", {"request": request, "template_available": template_available})


@router.post("/convert")
async def convert(
    request: Request,
    output_format: str = Form(...),
    main_file: UploadFile | None = File(default=None),
    project_zip: UploadFile | None = File(default=None),
    assets: list[UploadFile] = File(default_factory=list),
    use_template: str = Form(default="on"),
):
    """Handle upload validation, markdown conversion, and output file response.
    
    If template is configured and use_template checkbox is checked, and output is DOCX,
    apply the template to pandoc via --reference-doc.
    """
    output_format = output_format.lower().strip()
    if output_format not in ALLOWED_OUTPUTS:
        raise HTTPException(status_code=400, detail="Output format must be docx or pdf.")

    # Browsers may submit empty file parts for unselected file inputs.
    # Normalize those to None so ZIP-only uploads don't trip markdown validation.
    main_filename = (main_file.filename or "").strip() if main_file else ""
    zip_filename = (project_zip.filename or "").strip() if project_zip else ""

    # Some clients send a placeholder main file called upload.bin when nothing is selected.
    if main_filename == "upload.bin" and zip_filename:
        main_file = None
    if not main_file or not main_filename:
        main_file = None
    if not project_zip or not (project_zip.filename or "").strip():
        project_zip = None

    temp_dir = Path(tempfile.mkdtemp(prefix="md-convert-"))
    total_written = 0
    template_path: Path | None = None
    should_use_template = use_template.lower() in {"on", "true", "1", "yes"}
    
    # Use pre-configured template if available and user has enabled it
    if should_use_template and request.app.state.template_file:
        template_path = request.app.state.template_file

    try:
        if not main_file and not project_zip:
            raise HTTPException(
                status_code=400,
                detail="Provide a markdown file directly, or provide a ZIP containing one.",
            )

        if project_zip:
            zip_rel = sanitize_relative_path(project_zip.filename or "project.zip")
            zip_target = safe_join_under(temp_dir, Path(zip_rel.name))
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
            main_path = safe_join_under(temp_dir, main_rel)
            total_written += await save_upload(main_file, main_path, MAX_FILE_BYTES)
            if total_written > MAX_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload exceeds {MAX_TOTAL_BYTES // (1024 * 1024)} MB.",
                )
        else:
            main_rel = pick_main_markdown(temp_dir)
            main_path = safe_join_under(temp_dir, main_rel)

        for upload in assets:
            rel_path = sanitize_relative_path(upload.filename or "asset.bin")
            if rel_path == main_rel:
                continue
            target = safe_join_under(temp_dir, rel_path)
            total_written += await save_upload(upload, target, MAX_FILE_BYTES)

            if total_written > MAX_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload exceeds {MAX_TOTAL_BYTES // (1024 * 1024)} MB.",
                )

        output_name = f"{sanitize_download_stem(main_path.stem)}.{output_format}"
        output_path = temp_dir / output_name

        validate_markdown_image_references(main_path, temp_dir)

        command = build_pandoc_command(main_rel, output_name, output_format, template_path)
        result = run_pandoc(command, temp_dir)

        if result.returncode != 0 or not output_path.exists():
            request_id = getattr(request.state, "request_id", "unknown")
            logger.warning(
                "conversion_failed",
                extra={
                    "event": "conversion_failed",
                    "request_id": request_id,
                    "detail": result.stderr.strip() or "Pandoc conversion failed without stderr output.",
                    "status_code": 400,
                    "path": request.url.path,
                    "method": request.method,
                },
            )
            raise HTTPException(
                status_code=400,
                detail=f"Conversion failed. Contact the administrator with request ID: {request_id}",
            )

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


@router.get("/health")
async def health() -> dict[str, str]:
    """Health probe that verifies pandoc is available in the runtime."""
    pandoc_path = shutil.which("pandoc")
    if not pandoc_path:
        raise HTTPException(status_code=503, detail="pandoc is not installed or not on PATH.")

    return {"status": "ok", "pandoc": pandoc_path}
