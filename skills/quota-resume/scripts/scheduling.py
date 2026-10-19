"""Plan native wakes and inspect schedule metadata. Never edits the native store."""
import datetime
from contextlib import closing
import math
import os
from pathlib import Path
import sqlite3

# Native wakes can be jittered earlier than their nominal recurrence. This is
# only an initial margin: the actual next_run_at must still pass verification.
MARGIN_SECONDS = 300


def plan(s, now, active=False):
    if not s.get('enabled'):
        return None
    pending = [i for i in s.get('queue', {}).values() if i.get('status') == 'pending'
               and i.get('capturePolicy') == 2 and i.get('turnId')
               and i.get('expiresAt', 0) >= now]
    q = s.get('lastQuota', {})
    fresh = q.get('accountId') == s.get('accountId') and 0 <= now-q.get('observedAt', 0) <= 90
    delay = s['settings']['delaySeconds']
    if pending:
        due = min(i['resetAt']+delay for i in pending)
        if fresh and not q.get('allowed') and q.get('blockingReset', 0) > now:
            due = max(due, q['blockingReset']+delay)
    elif active and fresh and q.get('reset', 0) > now:
        # Arm while ordinary work still has quota; capture remains low-quota only.
        due = q['reset']+delay
    else:
        return None
    not_before = max(due, now+60)
    nominal = not_before+MARGIN_SECONDS
    return {'dueAt': due, 'notBeforeAt': not_before, 'nominalAt': nominal,
            'nominalLocal': datetime.datetime.fromtimestamp(nominal, datetime.timezone.utc).astimezone().isoformat(),
            'pendingCount': len(pending), 'prearmed': not pending}


def native_record(identifier, database=None):
    if database is None:
        base = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
        database = base/'sqlite/codex-dev.db'
    database = Path(database)
    if not database.is_file():
        raise ValueError('Native schedule metadata unavailable; timing not verified')
    # Schedule timing only. Never read desktop thread state through this DB.
    try:
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=.5)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT id,kind,status,target_thread_id,next_run_at,'
                               'next_run_nominal_at,last_run_at FROM automations WHERE id=?',
                               (identifier,)).fetchone()
            if row is None:
                raise ValueError('Native schedule not found')
            return dict(row)
    except sqlite3.Error:
        raise ValueError('Native schedule metadata unavailable; timing not verified') from None


def verify(s, identifier, due, now, database=None):
    pending = [i for i in s.get('queue', {}).values() if i.get('status') == 'pending'
               and i.get('capturePolicy') == 2 and i.get('turnId')]
    if pending and due < min(i['resetAt']+s['settings']['delaySeconds'] for i in pending):
        raise ValueError('Saved delay changed; recalculate the original recovery due time')
    record = native_record(identifier, database)
    if (record['kind'] != 'heartbeat' or record['status'] != 'ACTIVE'
            or record['target_thread_id'] != s['controllerId']):
        raise ValueError('Native schedule is inactive or targets another controller')
    actual, nominal = record['next_run_at'], record['next_run_nominal_at']
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (actual, nominal)):
        raise ValueError('Native next wake unavailable; timing not verified')
    actual, nominal = actual/1000, nominal/1000
    if actual < due or actual <= now:
        raise ValueError('Native wake is before recovery eligibility; move the same schedule later')
    if actual > max(due, now)+1800 or nominal > max(due, now)+1800:
        raise ValueError('Native wake is on a later cycle; re-arm the same schedule')
    return {'wakeAt': actual, 'nominalAt': nominal, 'verifiedAt': now}


def health(s, now, database=None):
    if not s or not s.get('automationId') or not s.get('scheduledFor'):
        return {'status': 'none', 'wakeAt': None}
    due = s['scheduledFor']
    try:
        record = native_record(s['automationId'], database)
        if record['kind'] != 'heartbeat' or record['target_thread_id'] != s['controllerId']:
            return {'status': 'unverified', 'wakeAt': None}
        if record['status'] != 'ACTIVE':
            return {'status': 'paused', 'wakeAt': None}
        actual = (record['next_run_at'] or 0)/1000
        last = (record['last_run_at'] or 0)/1000
        if due-1800 <= last < due and now > due+120:
            return {'status': 'missed', 'wakeAt': None}
        if actual < due or actual <= now or actual > max(due, now)+1800:
            return {'status': 'missed' if now > due+120 else 'unverified', 'wakeAt': None}
        nominal = (record['next_run_nominal_at'] or 0)/1000
        if nominal < due or nominal > max(due, now)+1800:
            return {'status': 'unverified', 'wakeAt': None}
        return {'status': 'verified', 'wakeAt': actual}
    except (ValueError, OSError, TypeError):
        return {'status': 'missed' if now > due+120 else 'unverified', 'wakeAt': None}
