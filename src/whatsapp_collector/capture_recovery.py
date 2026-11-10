"""Private, bounded recovery evidence; never substitutes for the committed export."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any


def recovery_path(output: Path) -> Path:
    return output.parent / "Failed Exports" / f"{output.stem}.latest-attempt.json"


def recovery_key(config: dict[str, Any], baseline: dict[str, Any] | None) -> str:
    value = {"config": config, "baseline": baseline, "formatVersion": 1}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def load_captures(path: Path, *, key: str, browser_epoch: float | None) -> list[dict[str, Any]]:
    try:
        record = json.loads(path.read_text())
        age = time.time() - float(record["createdAt"])
        if (not browser_epoch or record.get("browserEpoch") != browser_epoch or record.get("key") != key
                or not 0 <= age <= 600):
            return []
        captures = record.get("captures", [])
        return [capture for capture in captures if isinstance(capture, dict)] if isinstance(captures, list) else []
    except (OSError, ValueError, KeyError, TypeError):
        return []


def retain_candidate(path: Path, *, key: str, browser_epoch: float | None,
                     candidate: dict[str, Any], captures: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".attempt-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump({"status": "rejected-not-published", "createdAt": time.time(), "key": key,
                       "browserEpoch": browser_epoch, "candidate": candidate, "captures": captures}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
