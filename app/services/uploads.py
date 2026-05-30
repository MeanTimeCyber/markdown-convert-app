from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, UploadFile

from app.config import (
    ALLOWED_MD_EXTENSIONS,
    MAX_COMPRESSION_RATIO,
    MAX_FILE_BYTES,
    MAX_ZIP_DEPTH,
    MAX_ZIP_ENTRIES,
)


def sanitize_relative_path(raw_name: str) -> Path:
    """Normalize user-provided paths into safe relative filesystem paths."""
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


def ensure_md_extension(file_name: str) -> None:
    """Validate that the uploaded main file has an allowed markdown extension."""
    suffix = Path(file_name).suffix.lower()
    if suffix not in ALLOWED_MD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Main file must be a markdown file (.md, .markdown, .mdown).",
        )


async def save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> int:
    """Stream an uploaded file to disk with per-file byte limit enforcement."""
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


def pick_main_markdown(temp_dir: Path) -> Path:
    """Pick the first markdown file discovered in extracted ZIP content."""
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
    """Extract ZIP content while enforcing anti-abuse and path safety limits."""
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
