from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import whatsapp_collector.export_safety as export_safety
from whatsapp_collector.export_safety import (
    ExportAlreadyRunningError,
    ExportChangedDuringRunError,
    ensure_last_good_export,
    protected_export,
    write_atomic_json,
)


def _valid_export(marker: str) -> dict[str, Any]:
    message = {
        "messageId": f"message-{marker}",
        "timestamp": "2026-07-24T12:00:00+00:00",
        "direction": "inbound",
        "sender": "Example",
        "text": f"Readable text for {marker}",
        "textAvailable": True,
        "messageType": "chat",
        "subtype": None,
    }
    return {
        "source": "whatsapp",
        "exportedAt": "2026-07-24T12:00:00+00:00",
        "maxAllViewChats": 15,
        "marker": marker,
        "threads": [
            {
                "threadKey": f"thread-{marker}",
                "chatTitle": f"Thread {marker}",
                "sourceView": "all",
                "recentMessages": [message],
                "messages": [message],
            }
        ],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def test_failed_run_retains_valid_current_export_byte_for_byte(tmp_path: Path) -> None:
    output = tmp_path / "export.json"
    _write_json(output, _valid_export("current-newest"))
    original_bytes = output.read_bytes()
    original_mtime = output.stat().st_mtime_ns
    _write_json(tmp_path / "backup" / "export.20260701-000000.json", _valid_export("older-backup"))

    with pytest.raises(RuntimeError, match="simulated timeout") as exc_info:
        with protected_export(output):
            raise RuntimeError("simulated timeout")

    assert output.read_bytes() == original_bytes
    assert output.stat().st_mtime_ns == original_mtime
    assert exc_info.value.export_recovery["status"] == "retained-current"
    assert json.loads(output.read_text())["marker"] == "current-newest"


def test_failed_run_restores_backup_only_when_current_is_missing(tmp_path: Path) -> None:
    output = tmp_path / "export.json"
    backup = tmp_path / "backup" / "export.20260701-000000.json"
    _write_json(backup, _valid_export("backup"))

    with pytest.raises(RuntimeError, match="readiness timeout") as exc_info:
        with protected_export(output):
            raise RuntimeError("readiness timeout")

    assert json.loads(output.read_text())["marker"] == "backup"
    assert exc_info.value.export_recovery == {
        "status": "restored-backup",
        "outputPath": str(output),
        "currentStatus": "missing",
        "sourcePath": str(backup),
    }


def test_recovery_skips_newer_degraded_backup(tmp_path: Path) -> None:
    output = tmp_path / "export.json"
    output.write_text("{not valid json")
    good = tmp_path / "backup" / "export.20260701-000000.json"
    degraded = tmp_path / "backup" / "export.20260702-000000.json"
    _write_json(good, _valid_export("good"))
    _write_json(degraded, {"threads": []})

    recovery = ensure_last_good_export(output)

    assert recovery.status == "restored-backup"
    assert recovery.current_status == "invalid-json"
    assert recovery.source_path == good
    assert json.loads(output.read_text())["marker"] == "good"


def test_successful_commit_backs_up_valid_current_without_changing_schema(tmp_path: Path) -> None:
    output = tmp_path / "export.json"
    old_payload = _valid_export("old")
    new_payload = _valid_export("new")
    _write_json(output, old_payload)

    with protected_export(output) as current:
        write_atomic_json(new_payload, output, known_current=current)

    assert json.loads(output.read_text()) == new_payload
    assert output.read_text() == json.dumps(new_payload, indent=2, ensure_ascii=False) + "\n"
    backups = list((tmp_path / "backup").glob("export.*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == old_payload
    assert "exportRecovery" not in json.loads(output.read_text())


def test_successful_commit_does_not_promote_degraded_current_to_backup(tmp_path: Path) -> None:
    output = tmp_path / "export.json"
    _write_json(output, {"threads": []})

    with protected_export(output) as current:
        write_atomic_json(_valid_export("new"), output, known_current=current)

    assert json.loads(output.read_text())["marker"] == "new"
    assert not (tmp_path / "backup").exists()


def test_serialization_failure_leaves_valid_current_untouched(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "export.json"
    _write_json(output, _valid_export("current"))
    original_bytes = output.read_bytes()

    def fail_dump(*args, **kwargs):
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(export_safety.json, "dump", fail_dump)
    with pytest.raises(OSError, match="simulated disk write failure") as exc_info:
        with protected_export(output) as current:
            write_atomic_json(_valid_export("new"), output, known_current=current)

    assert output.read_bytes() == original_bytes
    assert exc_info.value.export_recovery["status"] == "retained-current"
    assert not list(tmp_path.glob(".export.json.*.tmp"))
    assert not (tmp_path / "backup").exists()


def test_changed_current_export_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "export.json"
    _write_json(output, _valid_export("original"))
    externally_updated = _valid_export("external-update-with-a-different-size")

    with pytest.raises(ExportChangedDuringRunError) as exc_info:
        with protected_export(output) as current:
            _write_json(output, externally_updated)
            write_atomic_json(_valid_export("collector"), output, known_current=current)

    assert json.loads(output.read_text()) == externally_updated
    assert exc_info.value.export_recovery["status"] == "retained-current"


def test_overlapping_exports_are_rejected_without_touching_output(tmp_path: Path) -> None:
    output = tmp_path / "export.json"
    _write_json(output, _valid_export("current"))
    original_bytes = output.read_bytes()

    with protected_export(output):
        with pytest.raises(ExportAlreadyRunningError, match="already running"):
            with protected_export(output):
                pass

    assert output.read_bytes() == original_bytes


def test_memory_pressure_during_assessment_never_triggers_destructive_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "export.json"
    _write_json(output, _valid_export("current"))
    original_bytes = output.read_bytes()
    _write_json(tmp_path / "backup" / "export.20260701-000000.json", _valid_export("older"))

    def fail_load(*args, **kwargs):
        raise MemoryError

    monkeypatch.setattr(export_safety.json, "load", fail_load)
    with pytest.raises(RuntimeError, match="simulated collection failure") as exc_info:
        with protected_export(output):
            raise RuntimeError("simulated collection failure")

    assert output.read_bytes() == original_bytes
    assert exc_info.value.export_recovery["status"] == "recovery-failed"
    assert exc_info.value.export_recovery["currentStatus"] == "resource-limited"
