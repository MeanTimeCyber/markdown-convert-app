from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import PDF_ENGINE, REQUEST_TIMEOUT_SECONDS


def build_pandoc_command(main_rel: Path, output_name: str, output_format: str) -> list[str]:
    """Build a strict pandoc command without exposing user-controlled flags."""
    command = [
        "pandoc",
        str(main_rel),
        "-o",
        output_name,
    ]

    if output_format == "pdf":
        command.extend(["--pdf-engine", PDF_ENGINE])

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
