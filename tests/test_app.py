import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.middleware import security as security_module
import app.routes.convert as convert_routes
from app.services.conversion import build_pandoc_command
from app.services.uploads import ensure_md_extension, extract_zip_to_dir, sanitize_relative_path


def _fake_pandoc_success(command: list[str], cwd: Path | str, **_: object) -> subprocess.CompletedProcess[str]:
    cwd_path = Path(cwd)
    output_name = command[command.index("-o") + 1]
    (cwd_path / output_name).write_bytes(b"converted")
    return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")


def _fake_run_pandoc(command: list[str], cwd: Path | str) -> subprocess.CompletedProcess[str]:
    return _fake_pandoc_success(command, cwd)


@pytest.fixture(autouse=True)
def clear_rate_limit_state() -> None:
    security_module._rate_limit_store.clear()


def test_sanitize_relative_path_strips_parent_traversal() -> None:
    sanitized = sanitize_relative_path("../pics/../images/plot.png")
    assert sanitized == Path("images/plot.png")


def test_ensure_md_extension_rejects_non_markdown() -> None:
    with pytest.raises(HTTPException):
        ensure_md_extension("notes.txt")


def test_build_pandoc_command_for_docx() -> None:
    command = build_pandoc_command(Path("docs/main.md"), "main.docx", "docx")
    assert command == ["pandoc", "docs/main.md", "-o", "main.docx"]


def test_build_pandoc_command_for_docx_with_template(tmp_path: Path) -> None:
    template_file = tmp_path / "template.docx"
    template_file.write_text("fake docx")
    command = build_pandoc_command(Path("main.md"), "main.docx", "docx", template_file)
    assert command == ["pandoc", "main.md", "-o", "main.docx", "--reference-doc", str(template_file)]


def test_build_pandoc_command_for_pdf_includes_engine() -> None:
    command = build_pandoc_command(Path("main.md"), "main.pdf", "pdf")
    assert command == ["pandoc", "main.md", "-o", "main.pdf", "--pdf-engine", "xelatex"]


def test_build_pandoc_command_for_pdf_ignores_template(tmp_path: Path) -> None:
    template_file = tmp_path / "template.docx"
    template_file.write_text("fake docx")
    command = build_pandoc_command(Path("main.md"), "main.pdf", "pdf", template_file)
    # Template should be ignored for PDF output
    assert command == ["pandoc", "main.md", "-o", "main.pdf", "--pdf-engine", "xelatex"]


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


def test_extract_zip_to_dir_rejects_suspicious_compression_ratio(tmp_path: Path) -> None:
    archive = tmp_path / "bomb.zip"
    destination = tmp_path / "out"
    destination.mkdir()

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.txt", b"A" * (1024 * 1024))

    with pytest.raises(HTTPException) as exc:
        extract_zip_to_dir(archive, destination, max_total_bytes=10 * 1024 * 1024)

    assert exc.value.status_code == 413
    assert "compression ratio" in str(exc.value.detail)


def test_convert_endpoint_returns_docx_with_markdown_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_routes, "run_pandoc", _fake_run_pandoc)
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
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]


def test_convert_endpoint_sanitizes_download_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_routes, "run_pandoc", _fake_run_pandoc)
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("bad name<>.md", b"# title", "text/markdown")},
    )

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "bad_name.docx" in content_disposition


def test_convert_endpoint_accepts_zip_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_routes, "run_pandoc", _fake_run_pandoc)
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


def test_convert_endpoint_accepts_zip_only_with_placeholder_main_part(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some clients send an upload.bin placeholder for empty file inputs."""
    monkeypatch.setattr(convert_routes, "run_pandoc", _fake_run_pandoc)
    client = TestClient(app)

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as zf:
        zf.writestr("docs/main.md", b"# hello")
    archive_bytes.seek(0)

    response = client.post(
        "/convert",
        data={"output_format": "docx"},
        files=[
            ("main_file", ("upload.bin", b"", "application/octet-stream")),
            ("project_zip", ("project.zip", archive_bytes.getvalue(), "application/zip")),
        ],
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.content == b"converted"


def test_convert_endpoint_returns_error_when_pandoc_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fail(command: list[str], cwd: Path | str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=command, returncode=2, stdout="", stderr="boom")

    monkeypatch.setattr(convert_routes, "run_pandoc", fake_fail)
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("main.md", b"# title", "text/markdown")},
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Conversion failed. Contact the administrator with request ID:")


def test_convert_endpoint_requires_markdown_source() -> None:
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Provide a markdown file directly, or provide a ZIP containing one."


def test_convert_endpoint_rejects_path_traversal_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_routes, "run_pandoc", _fake_run_pandoc)
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("/tmp/evil.md", b"# title", "text/markdown")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid upload path."


def test_convert_endpoint_accepts_markdown_with_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], cwd: Path | str) -> subprocess.CompletedProcess[str]:
        cwd_path = Path(cwd)
        observed["asset_exists"] = (cwd_path / "images/plot.png").exists()
        output_name = command[command.index("-o") + 1]
        (cwd_path / output_name).write_bytes(b"converted")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(convert_routes, "run_pandoc", fake_run)
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


def test_convert_endpoint_rate_limits_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_routes, "run_pandoc", _fake_run_pandoc)
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_REQUESTS", 1)
    client = TestClient(app)

    first = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("main.md", b"# first", "text/markdown")},
    )
    second = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("main.md", b"# second", "text/markdown")},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"]


def test_rate_limit_ignores_untrusted_x_forwarded_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_routes, "run_pandoc", _fake_run_pandoc)
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", {"10.10.10.10"})
    client = TestClient(app)

    first = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("main.md", b"# first", "text/markdown")},
        headers={"x-forwarded-for": "1.1.1.1"},
    )
    second = client.post(
        "/convert",
        data={"output_format": "docx"},
        files={"main_file": ("main.md", b"# second", "text/markdown")},
        headers={"x-forwarded-for": "2.2.2.2"},
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_rate_limit_bucket_store_prunes_when_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_RATE_LIMIT_BUCKETS", 2)
    security_module._rate_limit_store.clear()

    # Fill beyond configured cap to trigger pruning.
    security_module.is_rate_limited("ip-1")
    security_module.is_rate_limited("ip-2")
    security_module.is_rate_limited("ip-3")

    assert len(security_module._rate_limit_store) <= 2


def test_convert_endpoint_uses_configured_template_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that pre-configured template is used when use_template checkbox is enabled."""
    observed: dict[str, object] = {}
    template_file = tmp_path / "template.docx"
    template_file.write_text("fake template")

    def fake_run_with_template(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        cwd_path = Path(cwd)
        output_name = command[command.index("-o") + 1]
        (cwd_path / output_name).write_bytes(b"converted")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(convert_routes, "run_pandoc", fake_run_with_template)
    # Set up app to have a pre-configured template
    app.state.template_file = template_file
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx", "use_template": "on"},
        files=[("main_file", ("main.md", b"# title", "text/markdown"))],
    )

    assert response.status_code == 200
    assert "--reference-doc" in observed["command"]
    assert str(template_file) in observed["command"]
    # Cleanup
    app.state.template_file = None


def test_convert_endpoint_skips_template_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that template is not used when use_template checkbox is disabled."""
    observed: dict[str, object] = {}
    template_file = tmp_path / "template.docx"
    template_file.write_text("fake template")

    def fake_run_without_template(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        cwd_path = Path(cwd)
        output_name = command[command.index("-o") + 1]
        (cwd_path / output_name).write_bytes(b"converted")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(convert_routes, "run_pandoc", fake_run_without_template)
    # Set up app to have a pre-configured template
    app.state.template_file = template_file
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"output_format": "docx", "use_template": "off"},
        files=[("main_file", ("main.md", b"# title", "text/markdown"))],
    )

    assert response.status_code == 200
    assert "--reference-doc" not in observed["command"]
    # Cleanup
    app.state.template_file = None
