from __future__ import annotations

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
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the upload page."""
    return templates.TemplateResponse("index.html", {"request": request})


@router.post("/convert")
async def convert(
    output_format: str = Form(...),
    main_file: UploadFile | None = File(default=None),
    project_zip: UploadFile | None = File(default=None),
    assets: list[UploadFile] = File(default_factory=list),
):
    """Handle upload validation, markdown conversion, and output file response."""
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
        result = run_pandoc(command, temp_dir)

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


@router.get("/health")
async def health() -> dict[str, str]:
    """Health probe that verifies pandoc is available in the runtime."""
    pandoc_path = shutil.which("pandoc")
    if not pandoc_path:
        raise HTTPException(status_code=503, detail="pandoc is not installed or not on PATH.")

    return {"status": "ok", "pandoc": pandoc_path}
