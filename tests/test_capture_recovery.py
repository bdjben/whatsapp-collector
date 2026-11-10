import json
import hashlib
from copy import deepcopy

from whatsapp_collector.capture_recovery import load_captures, recovery_key, retain_candidate
from test_targeted_retry import RetryCollector
from whatsapp_collector.collector import WhatsAppCollector


def test_private_checkpoint_requires_same_browser_configuration_and_baseline(tmp_path):
    path = tmp_path / 'failed.json'
    key = recovery_key({'limit': 15}, {'threads': []})
    retain_candidate(path, key=key, browser_epoch=123, candidate={'threads': []}, captures=[{'complete': True}])
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_captures(path, key=key, browser_epoch=123) == [{'complete': True}]
    assert load_captures(path, key=key, browser_epoch=124) == []
    assert load_captures(path, key=recovery_key({'limit': 16}, {'threads': []}), browser_epoch=123) == []
    assert load_captures(path, key=recovery_key({'limit': 15}, {'threads': [1]}), browser_epoch=123) == []
    record = json.loads(path.read_text()); record['createdAt'] -= 601
    path.write_text(json.dumps(record))
    assert load_captures(path, key=key, browser_epoch=123) == []


def test_retry_reuses_only_unchanged_complete_captures(monkeypatch):
    first = RetryCollector('always')
    monkeypatch.setattr(first, '_live_capture_fingerprint', lambda jid: 'unchanged')
    first.collect_dashboard_export(allow_labels=['Clients'])
    assert len(first.capture_checkpoint) == 1
    retry = RetryCollector('none')
    monkeypatch.setattr(retry, '_live_capture_fingerprint', lambda jid: 'unchanged')
    payload = retry.collect_dashboard_export(allow_labels=['Clients'], resume_captures=first.capture_checkpoint)
    assert retry.calls == {'retry@lid': 1}
    assert payload['exportQuality']['ok']
    assert any(c.get('resumed') for c in payload['exportDiagnostics']['chatCaptures'])
    changed = RetryCollector('none')
    monkeypatch.setattr(changed, '_live_capture_fingerprint', lambda jid: 'changed')
    changed.collect_dashboard_export(allow_labels=['Clients'], resume_captures=first.capture_checkpoint)
    assert changed.calls == {'good@lid': 1, 'retry@lid': 1}


def test_expired_message_forces_new_capture_even_when_fingerprint_matches(monkeypatch):
    first = RetryCollector('none')
    monkeypatch.setattr(first, '_live_capture_fingerprint', lambda jid: 'same')
    first.collect_dashboard_export()
    checkpoint = deepcopy(first.capture_checkpoint)
    checkpoint[0]['messages'][0]['expiry'] = {
        'expiresAt': '2020-01-01T00:00:00Z', 'isEphemeral': True, 'kept': False, 'basis': 'explicit-expiry'}
    retry = RetryCollector('none')
    monkeypatch.setattr(retry, '_live_capture_fingerprint', lambda jid: 'same')
    retry.collect_dashboard_export(resume_captures=checkpoint)
    assert retry.calls['good@lid'] == 1


def test_resumed_media_requires_present_unchanged_bytes(tmp_path):
    path = tmp_path / 'attachment.bin'; path.write_bytes(b'original')
    messages = [{'attachments': [{'status': 'downloaded', 'localPath': str(path),
                 'sizeBytes': 8, 'sha256': hashlib.sha256(b'original').hexdigest()}]}]
    assert WhatsAppCollector._resumed_media_verified(messages)
    path.write_bytes(b'modified')
    assert not WhatsAppCollector._resumed_media_verified(messages)
    path.unlink()
    assert not WhatsAppCollector._resumed_media_verified(messages)
