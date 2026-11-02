"""Exercise hidden native startup and recurrence using an isolated app and fake bridge."""
from __future__ import annotations

import datetime as dt
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from build_macos_app import PRODUCT_NAME, _copy_sparkle_framework, _sign_app, build_macos_app


BRIDGE_FIXTURE = r'''
import datetime, json, os, sys
from pathlib import Path

root = Path(os.environ["WHATSAPP_COLLECTOR_QA_DIAGNOSTICS_PATH"]).parent
request = json.load(sys.stdin)
command = sys.argv[1]
assert Path(request["profileDir"]).is_relative_to(root)
assert Path(request["outputPath"]).is_relative_to(root)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
if command == "run-export":
    counter = root / "fixture-run-count.txt"
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    if count == 1:
        print(json.dumps({"ok": False, "error": "Simulated QA capture failure", "command": command}))
        sys.exit(1)
    output = Path(request["outputPath"])
    output.write_text(json.dumps({"exportedAt": now, "threads": []}))
    print(json.dumps({"ok": True, "command": command, "threadCount": 0,
        "export": {"path": str(output), "exists": True, "threadCount": 0, "exportedAt": now},
        "window": {"closedAfterExport": True}}))
else:
    print(json.dumps({"ok": True, "command": command}))
'''

WINDOW_CHECK = r'''
import CoreGraphics
import Foundation
let pid = Int(CommandLine.arguments.last!)!
let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []
let visible = windows.filter {
    ($0[kCGWindowOwnerPID as String] as? Int) == pid &&
    ($0[kCGWindowLayer as String] as? Int) == 0
}
print(visible.count)
'''


def iso_date(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    subprocess.run(["swift", "build", "--package-path", str(project / "native-macos")], check=True)
    products = Path(subprocess.check_output(
        ["swift", "build", "--package-path", str(project / "native-macos"), "--show-bin-path"], text=True
    ).strip())
    root = Path(tempfile.mkdtemp(prefix="whatsapp-collector-hidden-startup-"))
    domain = "studio.bdjben.whatsapp-collector.qa.hidden-startup-" + uuid.uuid4().hex[:8]
    app = build_macos_app(project, root, compile_app=False, sign_app=False)
    executable = app / "Contents/MacOS" / PRODUCT_NAME
    shutil.copy2(products / PRODUCT_NAME, executable)
    subprocess.run(["install_name_tool", "-add_rpath", "@executable_path/../Frameworks", str(executable)], check=True)
    _copy_sparkle_framework(products, app / "Contents/Frameworks")
    info_path = app / "Contents/Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info.update(CFBundleIdentifier=domain, CFBundleDisplayName="WhatsApp Collector QA", SUEnableAutomaticChecks=False)
    info_path.write_bytes(plistlib.dumps(info))
    (app / "Contents/Resources/native_bridge.py").write_text(BRIDGE_FIXTURE)
    _sign_app(app)
    due = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=10)
    preferences = {
        "hasSavedConfiguration": True,
        "loginSchedulingInitialized": True,
        "profileDir": str(root / "Chrome Profile"),
        "outputPath": str(root / "fixture-export.json"),
        "debugPort": 19323,
        "scheduleIntervalMinutes": 1,
        "pendingChromeOwnershipPath": str(root / "chrome-ownership.json"),
        "SUHasLaunchedBefore": True,
        "SUAutomaticallyUpdate": False,
        "automationScheduleRecord.v1": json.dumps({
            "enabled": True, "intervalMinutes": 1, "nextRecurringRunAt": iso_date(due), "currentRunTriggers": []
        }).encode(),
    }
    preferences_path = root / "qa-preferences.plist"
    preferences_path.write_bytes(plistlib.dumps(preferences))
    subprocess.run(["defaults", "import", domain, str(preferences_path)], check=True)
    diagnostics_path = root / "native-automation.json"
    environment = dict(os.environ)
    environment.update({
        "WHATSAPP_COLLECTOR_QA_MODE": "1",
        "WHATSAPP_COLLECTOR_QA_HIDE_DASHBOARD": "1",
        "WHATSAPP_COLLECTOR_QA_DIAGNOSTICS_PATH": str(diagnostics_path),
    })
    process = None
    first_snapshot = None
    try:
        with (root / "app.log").open("w") as log:
            process = subprocess.Popen([str(executable)], env=environment, stdout=log, stderr=log)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                assert process.poll() is None, "QA app exited before verification completed"
                if diagnostics_path.exists():
                    snapshot = json.loads(diagnostics_path.read_text())
                    if first_snapshot is None:
                        first_snapshot = snapshot
                        assert snapshot["schedulerActive"], "Hidden startup did not initialize automation"
                        assert snapshot["schedule"]["enabled"]
                        assert not snapshot["schedule"].get("recentAttempts"), "First check should precede the due time"
                        assert subprocess.check_output(["swift", "-e", WINDOW_CHECK, str(process.pid)], text=True).strip() == "0", "Dashboard was visible during hidden-startup test"
                    attempts = snapshot["schedule"].get("recentAttempts", [])
                    if len(attempts) >= 2 and attempts[-1]["outcome"] == "succeeded":
                        assert [attempt["outcome"] for attempt in attempts] == ["failed", "succeeded"]
                        assert all(attempt["triggers"] == ["recurring"] for attempt in attempts)
                        assert snapshot["schedule"]["nextRecurringRunAt"] > attempts[-1]["completedAt"]
                        assert snapshot["lastSchedulerCheckAt"] > first_snapshot["lastSchedulerCheckAt"]
                        assert subprocess.check_output(["swift", "-e", WINDOW_CHECK, str(process.pid)], text=True).strip() == "0", "Dashboard became visible during recurrence"
                        print(json.dumps({
                            "qaDirectory": str(root), "schedulerInitializedWhileHidden": True,
                            "firstAttempt": attempts[0], "secondAttempt": attempts[1],
                            "nextRunAt": snapshot["schedule"]["nextRecurringRunAt"],
                            "dashboardRemainedHidden": True, "usedOnlyFixtureBridge": True,
                        }, indent=2), flush=True)
                        return
                time.sleep(1)
            raise RuntimeError(f"Hidden-startup verification timed out. See {root}")
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        subprocess.run(["defaults", "delete", domain], check=False, capture_output=True)


if __name__ == "__main__":
    main()
