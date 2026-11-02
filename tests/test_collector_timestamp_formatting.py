from datetime import datetime, timezone

import pytest

from whatsapp_collector.collector import WhatsAppCollector


@pytest.mark.parametrize("value", [1699199821, 1699199821.0, 1699199821000, "1699199821", "1699199821000"])
def test_timestamp_seconds_and_milliseconds_preserve_utc_format(value):
    expected = datetime.fromtimestamp(1699199821, timezone.utc).isoformat()
    assert WhatsAppCollector._format_timestamp(value) == expected


@pytest.mark.parametrize("value", [1699199821.987, 1699199821987])
def test_timestamp_fractional_seconds_remain_truncated(value):
    expected = datetime.fromtimestamp(1699199821, timezone.utc).isoformat()
    assert WhatsAppCollector._format_timestamp(value) == expected


@pytest.mark.parametrize("value", [
    None, False, True, 0, -1, "", "invalid", {}, [],
    float("nan"), float("inf"), float("-inf"), "NaN", "Infinity",
    1e30, 10**400, 253402300800000,
])
def test_invalid_or_out_of_range_timestamp_is_unknown_not_a_crash(value):
    assert WhatsAppCollector._format_timestamp(value) is None


def test_record_chat_expiry_exact_live_millisecond_setting_with_disabled_timer():
    collector = WhatsAppCollector(session=object())
    jid = "123456789012345@lid"
    collector._record_chat_expiry(jid, {"id": jid, "ephemeralSettingTimestamp": 1699199821000, "ephemeralDuration": 0})
    setting = collector._chat_expiry_settings[jid]
    assert setting["settingChangedAt"] == datetime.fromtimestamp(1699199821, timezone.utc).isoformat()
    assert setting["durationSeconds"] == 0
    assert setting["source"] == "indexeddb-chat"
    assert setting["appliesRetroactively"] is False
    assert datetime.fromisoformat(setting["observedAt"]).tzinfo is not None


@pytest.mark.parametrize("value", [True, "invalid", float("inf"), 1e30])
def test_record_chat_expiry_preserves_setting_when_timestamp_is_invalid(value):
    collector = WhatsAppCollector(session=object())
    collector._record_chat_expiry("chat@lid", {"ephemeralSettingTimestamp": value, "ephemeralDuration": 7776000})
    setting = collector._chat_expiry_settings["chat@lid"]
    assert setting["settingChangedAt"] is None
    assert setting["durationSeconds"] == 7776000
