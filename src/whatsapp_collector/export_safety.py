from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from whatsapp_collector.export_quality import ExportQualityError, assess_export_quality


FileFingerprint = tuple[int, int, int, int]


class ExportAlreadyRunningError(RuntimeError):
    pass


class ExportChangedDuringRunError(RuntimeError):
    pass


class ExportCurrentUnreadableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportFileAssessment:
    path: Path
    status: str
    fingerprint: FileFingerprint | None = None
    quality: dict[str, Any] | None = None
    error: str | None = None

    @property
    def acceptable(self) -> bool:
        return self.status == "acceptable"


@dataclass(frozen=True)
class ExportRecoveryResult:
    status: str
    output_path: Path
    current_status: str
    source_path: Path | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "outputPath": str(self.output_path),
            "currentStatus": self.current_status,
        }
        if self.source_path is not None:
            payload["sourcePath"] = str(self.source_path)
        if self.error:
            payload["error"] = self.error
        return payload


def assess_export_file(output_path: Path) -> ExportFileAssessment:
    output_path = output_path.expanduser()
    try:
        before = _fingerprint(output_path)
    except OSError as exc:
        return ExportFileAssessment(output_path, "unreadable", error=str(exc))
    if before is None:
        return ExportFileAssessment(output_path, "missing")

    try:
        with output_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except MemoryError:
        return ExportFileAssessment(
            output_path,
            "resource-limited",
            fingerprint=before,
            error="There was not enough memory to assess the current export safely.",
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _assessment_after_read_error(output_path, before, exc, status="invalid-json")
    except Exception as exc:
        return _assessment_after_read_error(output_path, before, exc)

    try:
        after = _fingerprint(output_path)
    except OSError as exc:
        return ExportFileAssessment(output_path, "unreadable", fingerprint=before, error=str(exc))
    if after != before:
        return ExportFileAssessment(
            output_path,
            "unstable",
            fingerprint=after,
            error="The export changed while it was being assessed.",
        )
    if not isinstance(payload, dict):
        return ExportFileAssessment(
            output_path,
            "invalid-json",
            fingerprint=after,
            error="The export JSON root is not an object.",
        )

    try:
        report = assess_export_quality(payload)
    except MemoryError:
        return ExportFileAssessment(
            output_path,
            "resource-limited",
            fingerprint=after,
            error="There was not enough memory to assess the current export safely.",
        )
    return ExportFileAssessment(
        output_path,
        "acceptable" if report["ok"] else "degraded",
        fingerprint=after,
        quality=report,
    )


def ensure_last_good_export(
    output_path: Path,
    *,
    known_current: ExportFileAssessment | None = None,
) -> ExportRecoveryResult:
    output_path = output_path.expanduser()
    current = _current_assessment(output_path, known_current)
    if current.acceptable:
        return ExportRecoveryResult("retained-current", output_path, current.status)
    if current.status in {"unstable", "resource-limited", "unreadable"}:
        return ExportRecoveryResult(
            "recovery-failed",
            output_path,
            current.status,
            error=current.error or "The current export changed during recovery.",
        )

    backup_dir = output_path.parent / "backup"
    if backup_dir.exists():
        pattern = f"{output_path.stem}.*{output_path.suffix}"
        for candidate in sorted(backup_dir.glob(pattern), reverse=True):
            candidate_assessment = assess_export_file(candidate)
            if candidate_assessment.status == "resource-limited":
                return ExportRecoveryResult(
                    "recovery-failed",
                    output_path,
                    current.status,
                    source_path=candidate,
                    error=candidate_assessment.error,
                )
            if not candidate_assessment.acceptable:
                continue
            try:
                _atomic_copy(candidate, output_path)
            except Exception as exc:
                return ExportRecoveryResult(
                    "recovery-failed",
                    output_path,
                    current.status,
                    source_path=candidate,
                    error=str(exc),
                )
            return ExportRecoveryResult(
                "restored-backup",
                output_path,
                current.status,
                source_path=candidate,
            )

    return ExportRecoveryResult("no-acceptable-export", output_path, current.status)


def write_atomic_json(
    payload: dict[str, Any],
    output_path: Path,
    *,
    known_current: ExportFileAssessment | None = None,
) -> Path:
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    current = _assessment_for_commit(output_path, known_current)
    if current.status == "unstable":
        raise ExportChangedDuringRunError(current.error or "The export changed while it was being assessed.")
    if current.status in {"resource-limited", "unreadable"}:
        raise ExportCurrentUnreadableError(
            current.error or f"The current export at {output_path} could not be assessed safely."
        )

    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not _assessment_still_current(current):
            raise ExportChangedDuringRunError(
                f"The existing export at {output_path} changed while the new export was being staged."
            )
        if current.acceptable:
            backup_path = _next_backup_path(output_path)
            _atomic_copy(output_path, backup_path)
        if not _assessment_still_current(current):
            raise ExportChangedDuringRunError(
                f"The existing export at {output_path} changed before the new export could be published."
            )
        os.replace(temporary_path, output_path)
        _fsync_directory(output_path.parent)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output_path


@contextmanager
def protected_export(output_path: Path) -> Iterator[ExportFileAssessment]:
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_path.with_name(f".{output_path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExportAlreadyRunningError(
                f"Another export is already running for {output_path}. Wait for it to finish before starting another."
            ) from exc

        current = assess_export_file(output_path)
        try:
            yield current
        except Exception as exc:
            try:
                recovery = ensure_last_good_export(output_path, known_current=current)
            except Exception as recovery_error:
                recovery = ExportRecoveryResult(
                    "recovery-failed",
                    output_path,
                    current.status,
                    error=str(recovery_error),
                )
            _attach_recovery(exc, recovery)
            raise
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _assessment_after_read_error(
    output_path: Path,
    before: FileFingerprint,
    error: Exception,
    *,
    status: str = "unreadable",
) -> ExportFileAssessment:
    try:
        after = _fingerprint(output_path)
    except OSError as exc:
        return ExportFileAssessment(output_path, "unreadable", fingerprint=before, error=str(exc))
    if after != before:
        return ExportFileAssessment(
            output_path,
            "unstable",
            fingerprint=after,
            error="The export changed while it was being assessed.",
        )
    return ExportFileAssessment(output_path, status, fingerprint=after, error=str(error))


def _current_assessment(
    output_path: Path,
    known_current: ExportFileAssessment | None,
) -> ExportFileAssessment:
    if known_current is not None and _assessment_still_current(known_current):
        return known_current
    return assess_export_file(output_path)


def _assessment_for_commit(
    output_path: Path,
    known_current: ExportFileAssessment | None,
) -> ExportFileAssessment:
    if known_current is None:
        return assess_export_file(output_path)
    if _assessment_still_current(known_current):
        return known_current
    raise ExportChangedDuringRunError(
        f"The existing export at {output_path} changed while collection was running; the new export was not published."
    )


def _assessment_still_current(assessment: ExportFileAssessment) -> bool:
    try:
        return _fingerprint(assessment.path) == assessment.fingerprint
    except OSError:
        return False


def _fingerprint(path: Path) -> FileFingerprint | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _next_backup_path(output_path: Path) -> Path:
    backup_dir = output_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{output_path.stem}.{timestamp}{output_path.suffix}"
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{output_path.stem}.{timestamp}-{suffix}{output_path.suffix}"
        suffix += 1
    return backup_path


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copy2(source, temporary_path)
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        _fsync_directory(destination.parent)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _attach_recovery(exc: Exception, recovery: ExportRecoveryResult) -> None:
    recovery_payload = recovery.to_dict()
    try:
        setattr(exc, "export_recovery", recovery_payload)
    except Exception:
        pass
    if not isinstance(exc, ExportQualityError):
        return
    exc.report["exportRecovery"] = recovery_payload
    if recovery.status == "restored-backup" and recovery.source_path is not None:
        exc.report["restoredLastGood"] = str(recovery.source_path)
