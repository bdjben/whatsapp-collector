from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


# These getters were observed in the Web client. Missing capabilities stay unknown.
MESSAGE_EXPIRY_HELPERS_JS = r'''
    const waCollectorExpiry = msg => {
        const result = {};
        for (const field of ['ephemeralDuration', 'ephemeralSettingTimestamp', 'ephemeralStartTimestamp',
                             'expiredTimestamp', 'isEphemeral', 'isKept', 'keepInChat', 'ephemeralOutOfSync']) {
            const value = msg?.[field];
            if (typeof value === 'number' || typeof value === 'boolean') result[field] = value;
        }
        try {
            const getters = window.require('WAWebMsgGetters');
            for (const [field, getter] of [['isKept', 'getIsKept'], ['isEphemeral', 'getIsEphemeral'],
                                         ['expiredTimestamp', 'getEphemeralExpirationTimestamp']]) {
                if (typeof getters?.[getter] !== 'function') continue;
                const value = getters[getter](msg);
                if (typeof value === 'number' || typeof value === 'boolean') result[field] = value;
            }
            result.evidenceSource = 'live-message-model';
        } catch (_) {}
        return result;
    };
'''


def epoch(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def iso(value: Any) -> str | None:
    timestamp = epoch(value)
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp else None
    except (ValueError, OverflowError, OSError):
        return None


def expiry_metadata(raw: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    fields = ('ephemeralDuration', 'ephemeralSettingTimestamp', 'ephemeralStartTimestamp',
              'expiredTimestamp', 'isEphemeral', 'isKept', 'keepInChat', 'ephemeralOutOfSync')
    evidence = {key: raw[key] for key in fields if raw.get(key) is not None}
    if not evidence:
        return None
    duration = epoch(raw.get('ephemeralDuration'))
    expires = epoch(raw.get('expiredTimestamp'))
    kept = raw.get('isKept') if isinstance(raw.get('isKept'), bool) else raw.get('keepInChat')
    is_ephemeral = raw.get('isEphemeral')
    basis = 'explicit-expiry' if expires else 'unknown'
    # A message-local duration is evidence; the conversation's current timer is not.
    if not expires and duration and is_ephemeral is not False:
        start = epoch(raw.get('ephemeralStartTimestamp')) or epoch(raw.get('t'))
        if start:
            expires, basis = start + duration, 'message-duration'
    result: dict[str, Any] = {
        'source': source, 'observedAt': datetime.now(timezone.utc).isoformat(),
        'isEphemeral': is_ephemeral if isinstance(is_ephemeral, bool) else bool(duration or expires),
        'kept': kept if isinstance(kept, bool) else None,
        'durationSeconds': duration, 'expiresAt': iso(expires), 'basis': basis,
        'settingChangedAt': iso(raw.get('ephemeralSettingTimestamp')),
        'outOfSync': raw.get('ephemeralOutOfSync') is True,
    }
    return result


def expected_expired(message: dict[str, Any], at: Any, *, grace_seconds: int = 120) -> bool:
    expiry = message.get('expiry') or {}
    deadline, now = epoch(expiry.get('expiresAt')), epoch(at)
    return bool(deadline and now and deadline + grace_seconds <= now
                and expiry.get('isEphemeral') is True and expiry.get('kept') is False
                and not expiry.get('outOfSync') and expiry.get('basis') in ('explicit-expiry', 'message-duration'))


def confirmed_empty(diagnostics: dict[str, Any]) -> bool:
    state = diagnostics.get('captureState') or {}
    return bool(diagnostics.get('openedChatChecked') and state.get('identityVerified') is True
                and state.get('confirmedEmpty') is True and state.get('historyStartReached') is True
                and state.get('isLoading') is False and state.get('exportableMessageCount') == 0)


def complete_live_capture(diagnostics: dict[str, Any], limit: int) -> bool:
    if confirmed_empty(diagnostics):
        return True
    state = diagnostics.get('captureState') or {}
    count = diagnostics.get('openedChatMessageCount', 0)
    if not count or 'opened-chat' not in diagnostics.get('sourcesUsed', []) or state.get('isLoading'):
        return False
    if not state:  # Older diagnostic records had no detailed capture state.
        return True
    return bool(state.get('historyStartReached') or (limit > 0 and count >= limit))
