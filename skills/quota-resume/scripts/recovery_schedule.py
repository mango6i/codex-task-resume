"""Prepare native timer arguments and verify timing. Never mutates the native store."""
from contextlib import closing
import datetime
import math
import os
from pathlib import Path
import sqlite3


def native_record(identifier, database=None):
    database = Path(database or Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'sqlite/codex-dev.db')
    if not database.is_file():
        raise ValueError('Native schedule metadata unavailable; recovery timing is not verified')
    try:
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=.5)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT id,name,prompt,kind,status,target_thread_id,notification_policy,'
                               'next_run_at,next_run_nominal_at FROM automations WHERE id=?', (identifier,)).fetchone()
            return dict(row) if row else None
    except sqlite3.Error:
        raise ValueError('Native schedule metadata unavailable; recovery timing is not verified') from None


def pending(s, now):
    return [i for i in s.get('queue', {}).values() if i.get('status') == 'pending'
            and i.get('capturePolicy') == 2 and i.get('turnId') and i.get('expiresAt', 0) >= now]


def verify(s, identifier, due, now, database=None):
    if not s.get('enabled'):
        raise ValueError('Monitor is disabled')
    items = pending(s, now)
    if items and due < min(i['resetAt']+s['settings']['delaySeconds'] for i in items):
        raise ValueError('Saved delay changed; recalculate the original recovery time')
    record = native_record(identifier, database)
    if (not record or record['kind'] != 'heartbeat' or record['status'] != 'ACTIVE'
            or record['target_thread_id'] != s['controllerId']):
        raise ValueError('Native recovery timer is missing, paused, or targets another conversation')
    values = [record['next_run_at'], record['next_run_nominal_at']]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('Native next wake is unavailable')
    actual, nominal = (v/1000 for v in values)
    if actual < due or actual <= now:
        raise ValueError('Native wake is too early; move the SAME timer later and verify again')
    if max(actual, nominal) > max(due, now)+1800:
        raise ValueError('Native wake is on another cycle; correct the SAME timer')
    return {'wakeAt': actual, 'nominalAt': nominal, 'verifiedAt': now}


def request(s, state_path, now, database=None):
    items = pending(s, now)
    if not s.get('enabled') or not items:
        return None
    due = min(i['resetAt']+s['settings']['delaySeconds'] for i in items)
    q = s.get('lastQuota', {})
    if (q.get('accountId') == s['accountId'] and 0 <= now-q.get('observedAt', 0) <= 90
            and not q.get('allowed') and q.get('blockingReset', 0) > now):
        due = max(due, q['blockingReset']+s['settings']['delaySeconds'])
    nominal = max(due, now+60)+300
    local = datetime.datetime.fromtimestamp(nominal, datetime.timezone.utc).astimezone()
    identifier = s.get('automationId')
    record = native_record(identifier, database) if identifier else None
    if record and (record['kind'] != 'heartbeat' or record['target_thread_id'] != s['controllerId']):
        raise ValueError('Saved timer belongs to another workflow; no update authorized')
    helper = Path(__file__).with_name('state.py')
    skill = helper.parent.parent/'SKILL.md'
    prompt = (record or {}).get('prompt') or (
        f'Use the quota-resume skill at {skill} and its orchestration reference for ONE recovery check. '
        f'Durable state: {state_path}. Controller: {s["controllerId"]}, local. '
        'Use CURRENT saved scope, threshold, delay and exact message. Require the captured turn to '
        'have stopped from quota exhaustion and verify live native quota and task state. '
        'Reserve before each native send, commit only confirmed success, never replay uncertain sends. '
        'Never override models or permissions. If early, re-arm this SAME timer after the ORIGINAL '
        'reset plus current delay and verify its actual wake. After one pass pause this timer unless '
        'a real exhausted window blocks recovery. No daily idle runs or periodic model polling. '
        'Stay quiet when no action is needed.')
    args = {'mode': 'update' if record else 'create', 'kind': 'heartbeat',
            'name': (record or {}).get('name') or 'Codex 任务续接 · 额度恢复检查',
            'prompt': prompt, 'status': 'ACTIVE', 'targetThreadId': s['controllerId'],
            'rrule': f'FREQ=DAILY;BYHOUR={local.hour};BYMINUTE={local.minute};BYSECOND={local.second}'}
    if record:
        args['id'] = identifier
        args['notificationPolicy'] = record.get('notification_policy')
    return {'dueAt': due, 'nominalAt': nominal, 'nominalLocal': local.isoformat(), 'arguments': args}
