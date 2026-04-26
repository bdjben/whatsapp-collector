from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PUBLIC_FILES = [
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "pyproject.toml",
    PROJECT_ROOT / "scripts" / "scheduled_export.sh",
    PROJECT_ROOT / "scripts" / "build_zipapp.py",
]


def test_public_branding_uses_whatsapp_collector_not_business_collector() -> None:
    for path in PUBLIC_FILES:
        text = path.read_text()
        assert "WhatsApp Business Collector" not in text, path
        assert "wa-business-collector" not in text, path
        assert "wa_business_collector" not in text, path
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    assert 'name = "whatsapp-collector"' in pyproject
    assert 'whatsapp-collector = "whatsapp_collector.cli:main"' in pyproject


def test_readme_mentions_both_whatsapp_web_and_business_web() -> None:
    text = (PROJECT_ROOT / "README.md").read_text()
    assert "WhatsApp Web" in text
    assert "WhatsApp Business Web" in text
    assert "works with both" in text.lower()
