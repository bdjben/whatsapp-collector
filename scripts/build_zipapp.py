from __future__ import annotations

import argparse
import os
import shutil
import stat
import tempfile
import zipapp
from pathlib import Path

MAIN = """from whatsapp_collector.cli import main\nraise SystemExit(main())\n"""


def build_zipapp(project_root: Path, output_path: Path) -> Path:
    project_root = project_root.resolve()
    output_path = output_path.resolve()
    source_package = project_root / "src" / "whatsapp_collector"
    if not source_package.exists():
        raise FileNotFoundError(f"Package source not found: {source_package}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="whatsapp-collector-pyz-") as tmp:
        app_root = Path(tmp) / "app"
        shutil.copytree(
            source_package,
            app_root / "whatsapp_collector",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        (app_root / "__main__.py").write_text(MAIN)
        zipapp.create_archive(
            app_root,
            target=output_path,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    mode = output_path.stat().st_mode
    output_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build no-install whatsapp-collector.pyz")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="dist/whatsapp-collector.pyz")
    args = parser.parse_args()
    output = build_zipapp(Path(args.project_root), Path(args.output))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
