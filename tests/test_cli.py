from pathlib import Path

from whatsapp_collector.cli import main
from whatsapp_collector.models import ChatRow, IndexedDBThread, LabelStat, NormalizedEvent, RecentMessage, Snapshot


class StubCollector:
    def collect_snapshot(self) -> Snapshot:
        return Snapshot(
            page_title="(2) WhatsApp Business",
            page_url="https://web.whatsapp.com/",
            labels=[LabelStat(name="Important", chat_count=3)],
            chat_list=[
                ChatRow(
                    chat_name="Example Contact",
                    timestamp_label="Tuesday",
                    preview="Need to push to beginning of may",
                    unread_count=0,
                    unread_flag=False,
                )
            ],
        )

    def collect_labeled_threads(self, allow_labels=None, exclude_labels=None, max_messages=15):
        return [
            IndexedDBThread(
                jid="141394635137028@lid",
                display_name="Example Contact",
                phone_or_history_id="12017046817@c.us",
                labels=allow_labels or ["Important"],
                last_message_timestamp=1776500000,
                unread_count=0,
                preview="Need to push to beginning of may",
                timestamp_label="Tuesday",
                visible_in_chat_list=True,
                recent_messages=[
                    RecentMessage(
                        message_id="false_141394635137028@lid_msg3",
                        timestamp=1776500000,
                        iso_timestamp="2026-04-18T08:13:20+00:00",
                        direction="inbound",
                        sender="Example Contact",
                        text="Need to push to beginning of may",
                        text_available=True,
                        message_type="chat",
                        subtype=None,
                    )
                ][:max_messages],
            )
        ]

    def collect_events(self, allow_labels=None, exclude_labels=None, max_messages=15):
        return [
            NormalizedEvent(
                source="whatsapp_business",
                external_thread_id="141394635137028@lid",
                display_name="Example Contact",
                labels=allow_labels or ["Important"],
                summary="Need to push to beginning of may",
                importance="high",
                status_hint="active",
                unread_count=0,
                last_message_timestamp=1776500000,
                timestamp_label="Tuesday",
                visible_in_chat_list=True,
                recent_message_count=max_messages if max_messages < 1 else 1,
                recent_message_text_available_count=max_messages if max_messages < 1 else 1,
            )
        ]

    def collect_full_snapshot(self, allow_labels=None, exclude_labels=None, max_messages=15):
        return self.collect_snapshot().to_dict(
            allowed_labels=allow_labels or [],
            excluded_labels=exclude_labels or ["Excluded Label"],
            max_recent_messages=max_messages,
            labeled_threads=self.collect_labeled_threads(
                allow_labels=allow_labels,
                exclude_labels=exclude_labels,
                max_messages=max_messages,
            ),
            events=self.collect_events(
                allow_labels=allow_labels,
                exclude_labels=exclude_labels,
                max_messages=max_messages,
            ),
        )

    def collect_dashboard_export(self, account_label="WhatsApp", allow_labels=None, exclude_labels=None, max_messages=15):
        return {
            "source": "whatsapp",
            "exportedAt": "2026-04-19T00:00:00+00:00",
            "account": {
                "platform": "whatsapp-web",
                "accountLabel": account_label,
            },
            "allowLabels": allow_labels or [],
            "excludeLabels": exclude_labels or ["Excluded Label"],
            "maxRecentMessages": max_messages,
            "threads": [
                {
                    "threadKey": "141394635137028@lid",
                    "chatTitle": "Example Contact",
                    "chatType": "direct",
                    "participants": [{"name": "Example Contact", "phone": "12017046817@c.us"}],
                    "labelsRaw": allow_labels or ["Important"],
                    "labelsNormalized": ["important"],
                    "unread": False,
                    "starred": False,
                    "requiresResponse": True,
                    "lastMessageAt": "2026-04-19T00:00:00+00:00",
                    "lastMessageDirection": "inbound",
                    "lastMessageSender": "Example Contact",
                    "lastMessageText": "Need to push to beginning of may",
                    "recentMessages": [
                        {
                            "messageId": "false_141394635137028@lid_msg3",
                            "timestamp": "2026-04-19T00:00:00+00:00",
                            "direction": "inbound",
                            "sender": "Example Contact",
                            "text": "Need to push to beginning of may",
                            "textAvailable": True,
                            "messageType": "chat",
                            "subtype": None,
                        }
                    ][:max_messages],
                    "messages": [
                        {
                            "messageId": "false_141394635137028@lid_msg3",
                            "timestamp": "2026-04-19T00:00:00+00:00",
                            "direction": "inbound",
                            "sender": "Example Contact",
                            "text": "Need to push to beginning of may",
                            "textAvailable": True,
                            "messageType": "chat",
                            "subtype": None,
                        }
                    ][:max_messages],
                }
            ],
        }


def test_cli_snapshot_prints_json(capsys, tmp_path: Path) -> None:
    exit_code = main(["snapshot", "--storage-dir", str(tmp_path)], collector=StubCollector())

    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"labels"' in out
    assert '"Important"' in out
    assert '"excluded_labels": [' in out


def test_cli_snapshot_writes_file(tmp_path: Path) -> None:
    exit_code = main(
        ["snapshot", "--write", "--storage-dir", str(tmp_path)],
        collector=StubCollector(),
    )

    assert exit_code == 0
    files = list((tmp_path / "snapshots").glob("*.json"))
    assert len(files) == 1
    assert 'Important' in files[0].read_text()


def test_cli_snapshot_filters_labels_and_bounded_lookback(capsys, tmp_path: Path) -> None:
    exit_code = main(
        [
            "snapshot",
            "--storage-dir",
            str(tmp_path),
            "--allow-label",
            "Important",
            "--allow-label",
            "Business",
            "--exclude-label",
            "Excluded Label",
            "--max-messages",
            "9",
        ],
        collector=StubCollector(),
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"allowed_labels": [' in out
    assert '"Business"' in out
    assert '"excluded_labels": [' in out
    assert '"max_recent_messages": 9' in out


def test_cli_labeled_threads_prints_joined_thread_records(capsys, tmp_path: Path) -> None:
    exit_code = main(["labeled-threads", "--storage-dir", str(tmp_path)], collector=StubCollector())
    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"display_name": "Example Contact"' in out
    assert '"jid": "141394635137028@lid"' in out
    assert '"recent_messages"' in out


def test_cli_events_prints_normalized_dashboard_events(capsys, tmp_path: Path) -> None:
    exit_code = main(
        ["events", "--storage-dir", str(tmp_path), "--allow-label", "Important"],
        collector=StubCollector(),
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"source": "whatsapp_business"' in out
    assert '"importance": "high"' in out
    assert '"recent_message_count": 1' in out


def test_cli_dashboard_export_preserves_default_coller_exclusion_when_custom_exclusion_is_added(tmp_path: Path) -> None:
    output = tmp_path / "whatsapp-dashboard-export.json"
    exit_code = main(
        [
            "dashboard-export",
            "--output",
            str(output),
            "--exclude-label",
            "Business",
        ],
        collector=StubCollector(),
    )
    assert exit_code == 0
    text = output.read_text()
    assert '"excludeLabels": [' in text
    assert '"Excluded Label"' in text
    assert '"Business"' in text


def test_cli_dashboard_export_writes_atomic_contract_file(tmp_path: Path) -> None:
    output = tmp_path / "whatsapp-dashboard-export.json"
    exit_code = main(
        [
            "dashboard-export",
            "--output",
            str(output),
            "--allow-label",
            "Important",
            "--exclude-label",
            "Excluded Label",
            "--max-messages",
            "5",
        ],
        collector=StubCollector(),
    )
    assert exit_code == 0
    assert output.exists()
    text = output.read_text()
    assert '"source": "whatsapp"' in text
    assert '"threadKey": "141394635137028@lid"' in text
    assert '"excludeLabels": [' in text
    assert '"maxRecentMessages": 5' in text
    assert '"recentMessages"' in text
    assert '"messages"' in text


def test_cli_dashboard_export_backs_up_existing_output_before_replacing_it(tmp_path: Path) -> None:
    output = tmp_path / "whatsapp-dashboard-export.json"
    output.write_text('{"source":"whatsapp","threads":[{"threadKey":"old-thread"}]}\n')

    exit_code = main(
        [
            "dashboard-export",
            "--output",
            str(output),
            "--allow-label",
            "Important",
        ],
        collector=StubCollector(),
    )

    assert exit_code == 0
    assert output.exists()
    backups = list((tmp_path / "backup").glob("whatsapp-dashboard-export.*.json"))
    assert len(backups) == 1
    backup_text = backups[0].read_text()
    assert '"old-thread"' in backup_text
    assert '"141394635137028@lid"' in output.read_text()


def test_cli_ensure_window_prints_window_payload(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_ensure_dedicated_whatsapp_window(**kwargs):
        captured.update(kwargs)
        return {
            "windowId": 4321,
            "display": {"name": "WHATSAPPMONITOR", "x": 0, "y": 0, "width": 1920, "height": 1080},
            "profileDir": str(tmp_path),
            "markerTitle": kwargs["marker_title"],
            "markerUrlSubstring": kwargs["marker_url_substring"],
            "targetUrl": "https://web.whatsapp.com/",
            "placementMode": kwargs["placement_mode"],
            "settleSeconds": kwargs["settle_seconds"],
            "launched": True,
        }

    monkeypatch.setattr(
        "whatsapp_collector.cli.ensure_dedicated_whatsapp_window",
        fake_ensure_dedicated_whatsapp_window,
    )

    exit_code = main(
        [
            "ensure-window",
            "--profile-dir",
            str(tmp_path),
            "--display-name",
            "WhatsAppMonitor",
            "--placement-mode",
            "visible",
            "--settle-seconds",
            "15",
        ],
        collector=StubCollector(),
    )

    assert exit_code == 0
    assert captured["display_name"] == "WhatsAppMonitor"
    assert captured["marker_title"] == "WhatsApp Collector"
    assert captured["marker_url_substring"] == "whatsapp-collector"
    assert captured["placement_mode"] == "visible"
    assert captured["settle_seconds"] == 15
    out = capsys.readouterr().out
    assert '"windowId": 4321' in out
    assert '"placementMode": "visible"' in out
    assert '"settleSeconds": 15' in out
    assert '"markerTitle": "WhatsApp Collector"' in out


def test_cli_quit_profile_terminates_dedicated_instance(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_terminate(profile_dir):
        captured["profile_dir"] = profile_dir

    monkeypatch.setattr("whatsapp_collector.cli.terminate_profile_processes", fake_terminate)

    exit_code = main(["quit-profile", "--profile-dir", str(tmp_path)], collector=StubCollector())

    assert exit_code == 0
    assert str(captured["profile_dir"]) == str(tmp_path)
    out = capsys.readouterr().out
    assert '"mode": "quit_profile"' in out
    assert f'"profileDir": "{tmp_path}"' in out


def test_cli_status_reports_dedicated_profile_readiness(monkeypatch, capsys, tmp_path: Path) -> None:
    export_path = tmp_path / "whatsapp-dashboard-export.json"
    export_path.write_text('{"exportedAt":"2026-04-19T00:00:00+00:00","threads":[{"threadKey":"t1"}]}')
    monkeypatch.setattr(
        "whatsapp_collector.cli._status_payload",
        lambda **kwargs: {
            "mode": "dedicated_profile_status",
            "profileDir": str(kwargs["profile_dir"]),
            "debugPort": kwargs["debug_port"],
            "devtoolsReady": True,
            "collectorReady": True,
            "labelsCount": 10,
            "chatRowCount": 12,
            "export": {
                "path": str(kwargs["output_path"]),
                "exists": True,
                "threadCount": 1,
                "sizeBytes": 64,
                "updatedAt": "2026-04-19T00:01:00+00:00",
                "exportedAt": "2026-04-19T00:00:00+00:00",
            },
        },
    )

    exit_code = main(["status", "--profile-dir", str(tmp_path), "--output", str(export_path)], collector=StubCollector())

    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"mode": "dedicated_profile_status"' in out
    assert '"debugPort": 19220' in out
    assert '"threadCount": 1' in out


def test_cli_ensure_window_does_not_assume_display_name(monkeypatch, capsys, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_ensure_dedicated_whatsapp_window(**kwargs):
        captured.update(kwargs)
        return {
            "windowId": 4321,
            "display": {"name": "PRIMARY", "x": 0, "y": 0, "width": 1728, "height": 1117},
            "profileDir": str(tmp_path),
            "markerTitle": kwargs["marker_title"],
            "markerUrlSubstring": kwargs["marker_url_substring"],
            "targetUrl": "https://web.whatsapp.com/",
            "placementMode": kwargs["placement_mode"],
            "settleSeconds": kwargs["settle_seconds"],
            "launched": False,
        }

    monkeypatch.setattr("whatsapp_collector.cli.ensure_dedicated_whatsapp_window", fake_ensure_dedicated_whatsapp_window)

    exit_code = main(["ensure-window", "--profile-dir", str(tmp_path)], collector=StubCollector())

    assert exit_code == 0
    assert captured["display_name"] is None
    assert "TV" not in capsys.readouterr().out


def test_cli_max_messages_is_not_clamped_to_default(capsys, tmp_path: Path) -> None:
    exit_code = main(
        ["dashboard-export", "--output", str(tmp_path / "export.json"), "--max-messages", "50"],
        collector=StubCollector(),
    )

    assert exit_code == 0
    assert '"maxRecentMessages": 50' in (tmp_path / "export.json").read_text()


def test_cli_ui_starts_local_web_ui(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_ui_server(config, *, open_browser=False):
        captured["config"] = config
        captured["open_browser"] = open_browser

    monkeypatch.setattr("whatsapp_collector.cli.run_ui_server", fake_run_ui_server)

    exit_code = main(
        [
            "ui",
            "--host",
            "127.0.0.1",
            "--port",
            "9009",
            "--profile-dir",
            str(tmp_path / "profile"),
            "--output",
            str(tmp_path / "export.json"),
            "--max-messages",
            "88",
            "--open-browser",
        ],
        collector=StubCollector(),
    )

    assert exit_code == 0
    assert captured["open_browser"] is True
    config = captured["config"]
    assert config.port == 9009
    assert config.max_messages == 88
    assert str(config.profile_dir) == str(tmp_path / "profile")
    assert str(config.output_path) == str(tmp_path / "export.json")
    assert config.marker_title == "WhatsApp Collector"
    assert config.display_name is None
