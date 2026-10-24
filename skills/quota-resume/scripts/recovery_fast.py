"""Deterministic recovery from native status envelopes. No server, UI or model calls."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
import time

import state as core


def unwrap(value):
    if not isinstance(value, dict) or value.get('isError'):
        raise ValueError('Native status unavailable; no send authorized')
    if isinstance(value.get('structuredContent'), dict):
        return value['structuredContent']
    if 'content' in value:
        for item in value['content']:
            if item.get('type') == 'text':
                try:
                    result = json.loads(item['text'])
                    if isinstance(result, dict):
                        return result
                except (ValueError, TypeError):
                    pass
        raise ValueError('Native status did not contain JSON')
    return value


def read_quota():
    """Fresh account quota only; no desktop state, reservation or sending."""
    import hook
    usage = hook.read_usage()
    return {'usage': usage, 'observedAt': time.time()}


def native_observation(s, payload, now):
    """Only saved targets and native state/error flags; never inspect conversation text."""
    usage = unwrap(payload['usage'])
    raw = unwrap(payload['snapshot'])
    tid = payload['threadId']
    candidates = [i for i in s['queue'].values() if i.get('threadId') == tid
                  and i.get('windowKey') == payload['windowKey'] and i.get('status') == 'pending'
                  and i.get('capturePolicy') == 2 and i.get('turnId')]
    if not candidates:
        return None
    matches = [p for p in raw.get('polls', []) if p.get('thread', {}).get('id') == tid]
    if len(matches) != 1:
        raise ValueError('Exact recorded native target unavailable')
    poll = matches[0]
    thread, turn = poll['thread'], poll.get('latestTurn') or {}
    if (thread.get('hostId') != 'local' or not turn.get('id')
            or thread.get('kind', 'codex') != 'codex' or thread.get('parentThreadId')):
        raise ValueError('Local native identity unavailable')
    status = thread.get('status') or {}
    assessment, reason = 'unknown', None
    if status.get('type') == 'paused' or turn.get('status') in {'interrupted', 'cancelled'}:
        assessment = 'user_paused'
    elif turn.get('status') == 'completed' or poll.get('assistantPhase') == 'final_answer':
        assessment = 'completed'
    elif any(flag in {'waitingOnApproval', 'waitingOnUserInput', 'waitingForInput'}
             for flag in status.get('activeFlags', [])):
        assessment = 'awaiting_input'
    elif (turn.get('status') == 'failed' and re.search(
            r"hit your usage limit|usage limit (?:reached|exceeded)|quota (?:exhausted|exceeded)|"
            r"(?:额度|用量).*(?:耗尽|已用尽|上限)",
            str((turn.get('error') or {}).get('message', '')), re.I)):
        assessment, reason = 'quota_interrupted', 'quota_exhausted'
    # Root/source binding was verified when this specific local turn was captured.
    # A native inaccessible/archived target or changed turn never gets resumed.
    normalized = {'id': tid, 'hostId': 'local', 'kind': 'codex',
        'archived': bool(thread.get('archived', False)), 'isSubagent': bool(thread.get('isSubagent', False)),
        'status': status.get('type', 'unknown'), 'turnId': turn['id'],
        'turnStartedAt': turn.get('startedAt'), 'turnCompletedAt': turn.get('completedAt'),
        'turnStatus': turn.get('status'), 'purpose': 'task', 'assessment': assessment,
        'stopReason': reason, 'evidence': 'The exact recorded native turn failed with an explicit usage-limit error.'}
    for item in candidates:
        item['dueAt'] = item['resetAt']+s['settings']['delaySeconds']
    return {'observedAt': payload['observedAt'], 'usage': usage, 'threads': [normalized]}


def plan(s, now):
    scope = s.get('selection', {})
    selected = scope.get('threads', {})
    return [{'threadId': i['threadId'], 'windowKey': i['windowKey'],
             'title': (i.get('title') if i.get('title') != i['threadId'] else None)
                      or selected.get(i['threadId'], {}).get('title'),
             'dueAt': i['resetAt']+s['settings']['delaySeconds']}
            for i in core.recovery_schedule.pending(s, now)
            if i['threadId'] != s['controllerId'] and i['threadId'] not in s['excluded']
            and (scope.get('mode') != 'selected' or i['threadId'] in selected)] if s['enabled'] else []


def finish(s, path, now):
    pending = core.recovery_schedule.pending(s, now)
    q = s.get('lastQuota', {})
    early = any(i['resetAt']+s['settings']['delaySeconds'] > now for i in pending)
    blocked = (pending and q.get('accountId') == s['accountId']
               and 0 <= now-q.get('observedAt', 0) <= 90
               and not q.get('allowed') and q.get('blockingReset', 0) > now)
    if s['enabled'] and (early or blocked):
        return {'action': 'rearm', 'request': core.recovery_schedule.request(s, path, now)}
    identifier = s.get('automationId')
    record = core.recovery_schedule.native_record(identifier) if identifier else None
    if not record:
        return {'action': 'none'}
    if record['kind'] != 'heartbeat' or record['target_thread_id'] != s['controllerId']:
        raise ValueError('Saved timer ownership mismatch')
    if record['status'] == 'PAUSED' and record['next_run_at'] is None:
        return {'action': 'none'}
    if s['enabled'] and s.get('scheduledFor') and s['scheduledFor'] > now:
        try:
            core.recovery_schedule.verify(s, identifier, s['scheduledFor'], now)
            return {'action': 'none', 'newerCheckPreserved': True}
        except ValueError:
            pass
    if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
        raise ValueError('Invalid native timer identity')
    root = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    native_file = root/'automations'/identifier/'automation.toml'
    match = re.search(r'^rrule\s*=\s*("[^\r\n]*")\s*$', native_file.read_text(encoding='utf-8'), re.M)
    if not match:
        raise ValueError('Saved native timing unavailable')
    return {'action': 'pause', 'arguments': {'mode': 'update', 'id': identifier, 'kind': 'heartbeat',
        'name': record['name'], 'prompt': record['prompt'], 'status': 'PAUSED',
        'targetThreadId': s['controllerId'], 'notificationPolicy': record['notification_policy'],
        'rrule': json.loads(match[1])}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path)
    parser.add_argument('action', choices=['bootstrap', 'read-quota', 'plan', 'reserve', 'finish', 'ack-pause', 'commit', 'ack-arm'])
    data = parser.add_mutually_exclusive_group()
    data.add_argument('--json', default=None, help='Legacy/manual JSON input')
    data.add_argument('--payload-base64', help='ASCII transport for UTF-8 JSON; never shell-interpreted')
    args = parser.parse_args()
    path = (args.state or core.data_dir()/'state.json').resolve()
    raw = (base64.b64decode(args.payload_base64, validate=True).decode('utf-8')
           if args.payload_base64 is not None else args.json or '{}')
    payload = json.loads(raw)
    now = time.time()
    if args.action == 'bootstrap':
        print(json.dumps({'code': Path(__file__).with_name('native_dispatch.js').read_text(encoding='utf-8'),
            'config': {'python': sys.executable, 'script': str(Path(__file__).resolve()), 'state': str(path)}}))
        return
    if args.action == 'read-quota':
        # The existing read-only account endpoint is a quota fallback only.
        # It cannot read desktop task state or send, and does not open state.json.
        print(json.dumps(read_quota(), ensure_ascii=False))
        return
    with core.locked(path):
        s = core.load(path)
        result = {}
        if args.action == 'plan':
            result = {'targets': plan(s, now)}
        elif args.action == 'reserve':
            obs = native_observation(s, payload, now)
            result = {'accepted': False} if obs is None else core.reserve(s, obs, payload['threadId'], payload['windowKey'], now)
        elif args.action == 'commit':
            item = next((i for i in s['queue'].values() if i.get('token') == payload['token']), None)
            if not item or item.get('status') != 'reserved' or payload['outcome'] not in {'sent', 'uncertain'}:
                raise ValueError('Reservation unavailable or already committed')
            item.update(status=payload['outcome'], committedAt=now)
            core.event(s, now, payload['outcome'], threadId=item['threadId'])
            result = {'status': payload['outcome']}
        elif args.action == 'finish':
            result = finish(s, path, now)
        elif args.action == 'ack-pause':
            record = core.recovery_schedule.native_record(s['automationId'])
            if not record or record['status'] != 'PAUSED' or record['next_run_at'] is not None:
                raise ValueError('Native pause not verified')
            if not s.get('armRequest') and not (s.get('scheduledFor') and s['scheduledFor'] > now):
                s.update(scheduledFor=None, scheduleWakeAt=None, prearmResetAt=None, armLeaseUntil=0)
            result = {'paused': True}
        elif args.action == 'ack-arm':
            timing = core.recovery_schedule.verify(s, payload['id'], payload['dueAt'], now)
            s.update(automationId=payload['id'], scheduledFor=payload['dueAt'], scheduleWakeAt=timing['wakeAt'],
                     scheduleVerifiedAt=now, scheduleError=None, armRequest=None, armLeaseUntil=0)
            result = {'armed': True}
        if args.action not in {'plan', 'finish'}:
            core.atomic_write(path, s)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
