from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_exposes_installable_console_script() -> None:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert data["project"]["scripts"]["whatsapp-collector"] == "whatsapp_collector.cli:main"
    assert data["project"]["readme"] == "README.md"
    assert data["project"]["license"] == "MIT"


def test_packaged_assets_include_chrome_devtools_helper() -> None:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert "assets/chrome_devtools.mjs" in data["tool"]["setuptools"]["package-data"]["whatsapp_collector"]


def test_public_gitignore_excludes_private_runtime_state() -> None:
    patterns = [
        line.strip()
        for line in (PROJECT_ROOT / ".gitignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    private_paths = [
        "chrome-profile/Default/Preferences",
        "output/whatsapp-dashboard-export.json",
        "storage/snapshots/20260419T014238Z.json",
        ".venv/bin/python",
        "docs/live-verification.md",
    ]

    for private_path in private_paths:
        assert any(fnmatch.fnmatch(private_path, pattern) or private_path.startswith(pattern.rstrip("/")) for pattern in patterns), private_path
