from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import PDF_ENGINE, REQUEST_TIMEOUT_SECONDS


def build_pandoc_command(main_rel: Path, output_name: str, output_format: str, template_path: Path | None = None) -> list[str]:
    """Build a strict pandoc command without exposing user-controlled flags.
    
    If a template_path is provided and output is DOCX, add --reference-doc.
    """
    command = [
        "pandoc",
        str(main_rel),
        "-o",
        output_name,
    ]

    if output_format == "pdf":
        command.extend(["--pdf-engine", PDF_ENGINE])
    elif output_format == "docx" and template_path and template_path.exists():
        command.extend(["--reference-doc", str(template_path)])

    return command


def run_pandoc(command: list[str], work_dir: Path) -> subprocess.CompletedProcess[str]:
    """Execute pandoc conversion command in a controlled subprocess."""
    return subprocess.run(
        command,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
        check=False,
    )
