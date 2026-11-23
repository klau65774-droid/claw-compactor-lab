"""Paths and atomic writes for generated Context Lab artifacts."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"


def write_artifact(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=path.parent, mode="w", encoding="utf-8", newline="\n", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text + "\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
