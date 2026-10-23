"""Prepare native timer arguments and verify timing. Never mutates the native store."""
from contextlib import closing
import datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import sys

FAST_MARKER = 'Codex quota recovery fast path v1.'


def fast_prompt(state_path):
    """Reviewed local code orchestrates native tools inside the current model tool session."""
    def quote(value):
        return "'"+str(value).replace("'", "''")+"'"
    helper = Path(__file__).with_name('recovery_fast.py').resolve()
    command = '& '+quote(sys.executable)+' -X utf8 '+quote(helper)+' --state '+quote(state_path)+' bootstrap'
    wrapper = ('const r=await tools.exec_command({cmd:'+json.dumps(command)+',max_output_tokens:12000});'
               'if(r.exit_code!==0)throw Error(r.output);const b=JSON.parse(r.output);'
               'text(await(new Function("tools","config","return (async()=>{"+b.code+"})()"))(tools,b.config));')
    return (FAST_MARKER+' Perform ONE user-authorized recovery pass using CURRENT saved settings. '
        'Execute the following JavaScript with functions.exec immediately. It loads this installed plugin\'s '
        'reviewed native-tool runner, not an independent sender. Do not read the skill/reference, chat history, '
        'source code or all-chat inventory before execution. The runner checks ONLY recorded target IDs, '
        'same-turn native quota failure, completed/paused/active/blocked flags, live quota, original reset '
        'plus current delay, and durable reserve/send-once/commit. It preserves the exact saved prompt and '
        'never overrides models or permissions. It pauses this SAME timer after the pass unless an early '
        'wake or exhausted window needs a later verified wake. No additional monitor or model polling.\n'
        +wrapper+'\nStay quiet when unchanged or non-actionable. Notify only confirmed continuation '
        '(distinguish delivery from a new running turn), meaningful failure or required user action. '
        'If execution fails, report it once; never substitute history inspection or another dispatch backend.')


def saved_prompt(record, state_path):
    old = (record or {}).get('prompt')
    generated = (FAST_MARKER, 'Use the quota-resume skill at ', 'Use $quota-resume at ',
                 'Use the installed $quota-resume skill at ')
    return fast_prompt(state_path) if not old or old.startswith(generated) else old


def native_record(identifier, database=None):
    if database is None:
        root = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'sqlite'
        candidates = [root/name for name in ('codex-dev.db', 'codex.db') if (root/name).is_file()]
        if len(candidates) != 1:
            raise ValueError('Native schedule store is missing or ambiguous; client compatibility is not verified')
        database = candidates[0]
    database = Path(database)
    if not database.is_file():
        raise ValueError('Native schedule metadata unavailable; recovery timing is not verified')
    try:
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=.5)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT id,name,prompt,kind,status,target_thread_id,notification_policy,'
                               'next_run_at,next_run_nominal_at,last_run_at FROM automations WHERE id=?', (identifier,)).fetchone()
            return dict(row) if row else None
    except sqlite3.Error:
        raise ValueError('Native schedule metadata unavailable; recovery timing is not verified') from None


def pending(s, now):
    scope = s.get('selection', {})
    return [i for i in s.get('queue', {}).values() if i.get('status') == 'pending'
            and i.get('capturePolicy') == 2 and i.get('turnId') and i.get('expiresAt', 0) >= now
            and i.get('hostId') == 'local' and i['threadId'] != s.get('controllerId')
            and i['threadId'] not in s.get('excluded', [])
            and (scope.get('mode') != 'selected' or i['threadId'] in scope.get('threads', {}))]


def active_work(s, now):
    """Work can justify an early check, never a continuation candidate."""
    scope = s.get('selection', {})
    return [tid for tid, rt in s.get('runtime', {}).items()
            if tid != s.get('controllerId') and tid not in s.get('excluded', [])
            and (scope.get('mode') != 'selected' or tid in scope.get('threads', {}))
            and rt.get('active') and rt.get('purpose') == 'task' and rt.get('turnId')
            and isinstance(rt.get('startedAt'), (int, float))
            and not isinstance(rt.get('startedAt'), bool)
            and 0 < rt['startedAt'] <= now+5
            and 0 <= now-rt.get('seenAt', 0) <= 90]


def verify(s, identifier, due, now, database=None):
    if not s.get('enabled'):
        raise ValueError('Monitor is disabled')
    items = pending(s, now)
    if items and due < min(i['resetAt']+s['settings']['delaySeconds'] for i in items):
        raise ValueError('Saved delay changed; recalculate the original recovery time')
    prepared = s.get('armRequest') or {}
    prearm_reset = (prepared.get('resetAt') if prepared.get('reason') == 'active_work_prearm'
                    and prepared.get('dueAt') == due else s.get('prearmResetAt'))
    if (not items and isinstance(prearm_reset, (int, float))
            and due < prearm_reset+s['settings']['delaySeconds']):
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
    if not s.get('enabled'):
        return None
    q = s.get('lastQuota', {})
    work = active_work(s, now)
    fresh_quota = (q.get('accountId') == s['accountId']
                   and 0 <= now-q.get('observedAt', 0) <= 90)
    # Arm while the task still has quota, not after its final model request
    # has already failed. This does NOT capture healthy work or send to it.
    if items:
        due = min(i['resetAt']+s['settings']['delaySeconds'] for i in items)
        reason = 'captured_work'
    elif work and fresh_quota and q.get('allowed') and q.get('reset', 0) > now:
        due = q['reset']+s['settings']['delaySeconds']
        reason = 'active_work_prearm'
    else:
        return None
    if (q.get('accountId') == s['accountId'] and 0 <= now-q.get('observedAt', 0) <= 90
            and not q.get('allowed') and q.get('blockingReset', 0) > now):
        due = max(due, q['blockingReset']+s['settings']['delaySeconds'])
    identifier = s.get('automationId')
    record = native_record(identifier, database) if identifier else None
    if record and (record['kind'] != 'heartbeat' or record['target_thread_id'] != s['controllerId']):
        raise ValueError('Saved timer belongs to another workflow; no update authorized')
    last_run = (record or {}).get('last_run_at')
    saved_due = s.get('scheduledFor')
    if (reason == 'active_work_prearm' and record and record['status'] == 'ACTIVE'
            and isinstance(saved_due, (int, float)) and not isinstance(saved_due, bool)
            and isinstance(s.get('prearmResetAt'), (int, float))
            and (not isinstance(last_run, (int, float)) or last_run/1000 < saved_due)):
        # Quota can refresh before reset+delay. An outstanding original wake
        # must still happen; moving it into the next quota window loses it.
        due = min(due, s['prearmResetAt']+s['settings']['delaySeconds'])
    # Do not silently add five minutes to the user's reset delay. Compensate
    # only for an observed early native offset, then verify the actual wake.
    early_offset = 0
    if record:
        actual, native_nominal = record.get('next_run_at'), record.get('next_run_nominal_at')
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (actual, native_nominal)):
            offset = (native_nominal-actual)/1000
            if 0 < offset <= 600:
                early_offset = offset+1
    nominal = math.ceil(max(due, now+60)+early_offset)
    local = datetime.datetime.fromtimestamp(nominal, datetime.timezone.utc).astimezone()
    prompt = saved_prompt(record, state_path)
    args = {'mode': 'update' if record else 'create', 'kind': 'heartbeat',
            'name': (record or {}).get('name') or 'Codex 任务续接 · 额度恢复检查',
            'prompt': prompt, 'status': 'ACTIVE', 'targetThreadId': s['controllerId'],
            'rrule': f'FREQ=DAILY;BYHOUR={local.hour};BYMINUTE={local.minute};BYSECOND={local.second}'}
    if record:
        args['id'] = identifier
        args['notificationPolicy'] = record.get('notification_policy')
    cycle_due = min(due, saved_due) if isinstance(saved_due, (int, float)) else due
    recent_wake = (bool(record) and record['status'] == 'ACTIVE'
                   and isinstance(last_run, (int, float)) and not isinstance(last_run, bool)
                   and cycle_due <= last_run/1000 <= now and now-last_run/1000 < 600)
    return {'dueAt': due, 'reason': reason, 'resetAt': due-s['settings']['delaySeconds'],
            'nominalAt': nominal, 'nominalLocal': local.isoformat(),
            'recentRecoveryWake': recent_wake, 'arguments': args}
