import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import (
    app,
    build_pandoc_command,
    ensure_md_extension,
    extract_zip_to_dir,
    sanitize_relative_path,
)


def _fake_pandoc_success(command: list[str], cwd: Path | str, **_: object) -> subprocess.CompletedProcess[str]:
    cwd_path = Path(cwd)
    output_name = command[command.index("-o") + 1]
    (cwd_path / output_name).write_bytes(b"converted")
    return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")


def test_sanitize_relative_path_strips_parent_traversal() -> None:
    sanitized = sanitize_relative_path("../pics/../images/plot.png")
    assert sanitized == Path("images/plot.png")


def test_ensure_md_extension_rejects_non_markdown() -> None:
    with pytest.raises(HTTPException):
        ensure_md_extension("notes.txt")


def test_build_pandoc_command_for_docx() -> None:
    command = build_pandoc_command(Path("docs/main.md"), "main.docx", "docx")
    assert command == ["pandoc", "docs/main.md", "-o", "main.docx"]


def test_build_pandoc_command_for_pdf_includes_engine() -> None:
    command = build_pandoc_command(Path("main.md"), "main.pdf", "pdf")
    assert command == ["pandoc", "main.md", "-o", "main.pdf", "--pdf-engine", "tectonic"]


def test_extract_zip_to_dir_sanitizes_member_paths(tmp_path: Path) -> None:
    archive = tmp_path / "project.zip"
    destination = tmp_path / "out"
    destination.mkdir()

    import zipfile

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../unsafe/../../images/figure.png", b"png")
        zf.writestr("docs/main.md", b"# hello")

    extract_zip_to_dir(archive, destination, max_total_bytes=1024 * 1024)

    assert (destination / "images/figure.png").exists()
    assert (destination / "docs/main.md").exists()


def test_convert_endpoint_returns_docx_with_markdown_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.subprocess, "run", _fake_pandoc_success)
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("main.md", b"# title", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.content == b"converted"


def test_convert_endpoint_accepts_zip_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.subprocess, "run", _fake_pandoc_success)
    client = TestClient(app)

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as zf:
        zf.writestr("docs/main.md", b"# hello")
        zf.writestr("docs/images/figure.png", b"png")
    archive_bytes.seek(0)

    response = client.post(
        "/convert",
        data={"output_format": "pdf"},
        files={"project_zip": ("project.zip", archive_bytes.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content == b"converted"


def test_convert_endpoint_returns_error_when_pandoc_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fail(command: list[str], cwd: Path | str, **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=command, returncode=2, stdout="", stderr="boom")

    monkeypatch.setattr(main_module.subprocess, "run", fake_fail)
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("main.md", b"# title", "text/markdown")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "boom"


def test_convert_endpoint_accepts_markdown_with_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], cwd: Path | str, **_: object) -> subprocess.CompletedProcess[str]:
        cwd_path = Path(cwd)
        observed["asset_exists"] = (cwd_path / "images/plot.png").exists()
        output_name = command[command.index("-o") + 1]
        (cwd_path / output_name).write_bytes(b"converted")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(main_module.subprocess, "run", fake_run)
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx"},
        files=[
            ("main_file", ("main.md", b"![plot](images/plot.png)", "text/markdown")),
            ("assets", ("images/plot.png", b"fake-png", "image/png")),
        ],
    )

    assert response.status_code == 200
    assert response.content == b"converted"
    assert observed["asset_exists"] is True
