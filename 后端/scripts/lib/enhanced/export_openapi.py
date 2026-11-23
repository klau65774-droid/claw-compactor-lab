"""Export the OpenAPI schema from the app without starting a server."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

from ._artifacts import DOCS_DIR, write_artifact
from .api import app


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DOCS_DIR / "openapi.json")
    args = parser.parse_args()
    write_artifact(args.output, json.dumps(app.openapi(), ensure_ascii=False, indent=2))
    print(f"Exported OpenAPI to {args.output.name} ({len(app.openapi()['paths'])} routes)")


if __name__ == "__main__":
    main()
