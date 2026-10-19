"""Persistent quota queue. Standard library only; never sends or infers."""
import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
import scheduling

VERSION = 1
TERMINAL = {'sent', 'reserved', 'uncertain', 'completed', 'user_paused',
            'awaiting_input', 'excluded', 'expired'}
ASSESSMENTS = {'unknown', 'incomplete', 'quota_interrupted', 'completed',
               'user_paused', 'awaiting_input'}


def data_dir():
    if os.name == 'nt':
        root = os.environ.get('LOCALAPPDATA')
        if not root:
            raise ValueError('LOCALAPPDATA is unavailable; specify --state')
        return Path(root) / 'SkillHubQuotaResume'
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'skillhub-quota-resume'


def number(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f'{name} is out of range')
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextlib.contextmanager
def locked(path):
    """OS-owned lock releases automatically on crash; no stale PID heuristics."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lockfile = path.with_name(path.name + '.lock')
    with lockfile.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + 3
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('State is busy; defer until the next event')
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def load(path):
    s = read_json(path)
    if s.get('version') != VERSION:
        raise ValueError('Unsupported state version')
    return s


def event(s, now, kind, **fields):
    s['events'] = (s.get('events', []) + [{'at': now, 'type': kind, **fields}])[-100:]


def quota(usage, limit_id):
    account = usage.get('accountId')
    if not isinstance(account, str) or not account.strip():
        raise ValueError('Live account identity unavailable')
    buckets = usage.get('rateLimitsByLimitId')
    if buckets:
        bucket = buckets.get(limit_id)
    else:
        legacy = usage.get('rateLimits') or {}
        bucket = legacy if legacy.get('limitId') == limit_id else None
    if not isinstance(bucket, dict):
        raise ValueError('Configured quota bucket unavailable')
    windows = []
    for field in ['primary', 'secondary', 'individualLimit']:
        w = bucket.get(field)
        if w is not None:
            if not isinstance(w, dict):
                raise ValueError('Malformed quota window')
            used = number(w.get('usedPercent'), 'usedPercent', 0, 100)
            duration = number(w.get('windowDurationMins'), 'windowDurationMins', 1)
            reset = number(w.get('resetsAt'), 'resetsAt', 1)
            windows.append({'used': used, 'duration': duration, 'reset': reset})
    short = [w for w in windows if w['duration'] == 300]
    if len(short) != 1:
        raise ValueError('An unambiguous five-hour quota window is required')
    w = short[0]
    return {'accountId': account, 'remaining': max(0, 100-w['used']),
            'reset': w['reset'], 'key': f'{limit_id}:{w["reset"]:.6f}'.rstrip('0').rstrip('.'),
            'allowed': usage.get('ordinaryUsageAllowed') is True
                       and not bucket.get('spendControlReached', False)
                       and all(x['used'] < 100 for x in windows),
            'blockingReset': max([x['reset'] for x in windows if x['used'] >= 100] or [w['reset']])}


def validate_observation(obs, s, now):
    at = number(obs.get('observedAt'), 'observedAt', 1)
    if not -5 <= now-at <= 90:
        raise ValueError('Observation is stale or in the future')
    usage = obs.get('usage')
    if not isinstance(usage, dict):
        raise ValueError('Live quota required')
    q = quota(usage, s['settings']['limitId'])
    threads = obs.get('threads', [])
    if not isinstance(threads, list):
        raise ValueError('threads must be an array')
    indexed = {}
    for t in threads:
        if not isinstance(t, dict) or not isinstance(t.get('id'), str) or not t['id']:
            raise ValueError('Invalid thread identity')
        if t.get('assessment', 'unknown') not in ASSESSMENTS:
            raise ValueError('Unknown task assessment')
        if t['id'] in indexed and indexed[t['id']] != t:
            raise ValueError('Conflicting duplicate thread observations')
        indexed[t['id']] = t
    return q, indexed


def eligible(s, t):
    selection = s.get('selection', {})
    return (t.get('hostId') == 'local' and t.get('kind') == 'codex'
            and t.get('archived') is False and t.get('isSubagent') is False
            and t['id'] != s['controllerId'] and t['id'] not in s['excluded']
            and (selection.get('mode') != 'selected' or t['id'] in selection.get('threads', {})))


def canonical_quota(s, q):
    # The server can round the same reset timestamp by a second across reads.
    previous = s.get('lastQuota', {})
    if previous.get('key') and 'e+' not in previous['key'] and abs(previous.get('reset', 0)-q['reset']) <= 60:
        q['key'] = previous['key']
    return q


def runtime_threads(s, now):
    """Only fresh, identified work turns; selection alone proves no activity."""
    return [{'id': tid, 'hostId': 'local', 'kind': 'codex', 'archived': False,
             'isSubagent': False, 'status': 'active', 'assessment': 'unknown',
             'turnId': rt['turnId'], 'turnStartedAt': rt['startedAt'], 'purpose': 'task'}
            for tid, rt in s.get('runtime', {}).items()
            if rt.get('active') and rt.get('purpose') == 'task' and rt.get('turnId')
            and isinstance(rt.get('startedAt'), (int, float))
            and 0 <= now-rt.get('seenAt', 0) <= 90]


def start_monitoring(s, now):
    """A new activation cannot replay work captured before it was enabled."""
    s['monitoringStartedAt'] = now
    s['runtime'] = {}
    s['scheduledFor'] = None
    s['armLeaseUntil'] = s['nextArmAfter'] = 0
    for item in s['queue'].values():
        if item['status'] == 'pending':
            item.update(status='excluded', exclusionReason='previous_activation')


def schedule_plan(s, now):
    return scheduling.plan(s, now, active=any(eligible(s, t) for t in runtime_threads(s, now)))


def expire(s, now):
    changes = []
    for item in s['queue'].values():
        if item['status'] == 'pending' and now > item['expiresAt']:
            item['status'] = 'expired'
            changes.append({'threadId': item['threadId'], 'status': 'expired'})
            event(s, now, 'expired', threadId=item['threadId'])
    return changes


def observe(s, obs, now):
    if not s['enabled']:
        return {'enabled': False, 'due': [], 'scheduleMode': 'disabled', 'nextWakeAt': None}
    q, threads = validate_observation(obs, s, now)
    q = canonical_quota(s, q)
    if q['accountId'] != s['accountId']:
        s['enabled'] = False
        s['settings']['announcementEnabled'] = False
        event(s, now, 'account_changed')
        return {'enabled': False, 'due': [], 'scheduleMode': 'disabled',
                'nextWakeAt': None, 'reason': 'account_changed'}
    settings = s['settings']
    s['lastQuota'] = {**q, 'observedAt': now}
    changes = expire(s, now)
    if q['remaining'] < settings['threshold'] and now < q['reset']:
        for t in threads.values():
            if not eligible(s, t) or t.get('status') != 'active':
                continue
            started = t.get('turnStartedAt')
            if (t.get('purpose') != 'task' or not isinstance(t.get('turnId'), str) or not t['turnId']
                    or isinstance(started, bool) or not isinstance(started, (int, float))
                    or not 0 < started <= now+5):
                continue
            if t.get('assessment') in {'completed', 'user_paused', 'awaiting_input'}:
                continue
            key = q['key'] + ':' + t['id'] + ':' + t['turnId']
            if key not in s['queue']:
                due = q['reset'] + settings['delaySeconds']
                s['queue'][key] = {'threadId': t['id'], 'hostId': 'local',
                    'title': t.get('title', t['id']), 'windowKey': q['key'],
                    'resetAt': q['reset'], 'dueAt': due, 'capturedAt': now,
                    'expiresAt': due + settings['expiryHours']*3600,
                    'status': 'pending', 'capturePolicy': 2, 'captureSource': 'observed_running',
                    'turnId': t['turnId'], 'turnStartedAt': started}
                changes.append({'threadId': t['id'], 'status': 'captured'})
    due = []
    for key, item in s['queue'].items():
        if item['status'] != 'pending':
            continue
        if item.get('capturePolicy') != 2 or not item.get('turnId'):
            item.update(status='excluded', exclusionReason='unbound_legacy_candidate')
            changes.append({'threadId': item['threadId'], 'status': 'excluded'})
            continue
        t = threads.get(item['threadId'])
        if item['threadId'] in s['excluded']:
            item['status'] = 'excluded'
        elif t:
            assessment = t.get('assessment', 'unknown')
            if t.get('turnId') and t['turnId'] != item['turnId']:
                item.update(status='excluded', exclusionReason='superseded_turn')
                changes.append({'threadId': item['threadId'], 'status': 'excluded'})
                continue
            if assessment in {'completed', 'user_paused', 'awaiting_input'}:
                item['status'] = assessment
                changes.append({'threadId': item['threadId'], 'status': assessment})
            elif (eligible(s, t) and t.get('status') in {'idle', 'notLoaded', 'systemError'}
                  and assessment == 'quota_interrupted' and t.get('purpose') == 'task'
                  and t.get('turnId') == item['turnId'] and t.get('turnStatus') == 'failed'
                  and t.get('stopReason') == 'quota_exhausted'
                  and isinstance(t.get('turnCompletedAt'), (int, float))
                  and item['capturedAt']-5 <= t['turnCompletedAt'] <= min(now+5, item['resetAt']+120)
                  and isinstance(t.get('evidence'), str) and t['evidence'].strip()
                  and now >= item['dueAt'] and q['reset'] > item['resetAt'] + 60
                  and q['allowed'] and q['remaining'] >= settings['threshold']):
                due.append({'key': key, **item, 'message': settings['message']})
    pending = [i for i in s['queue'].values() if i['status'] == 'pending']
    future = [i['dueAt'] for i in pending if i['dueAt'] > now]
    if future:
        mode, wake = 'reset', min(future)
    elif pending and not q['allowed'] and q['blockingReset'] > now:
        mode, wake = 'reset', q['blockingReset'] + settings['delaySeconds']
    else:
        # No model-powered polling. Further activity will trigger local checks.
        mode, wake = 'event', None
    return {'enabled': True, 'quota': q, 'due': due, 'changes': changes,
            'scheduleMode': mode, 'nextWakeAt': wake,
            'pendingCount': len(pending)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path)
    p.add_argument('--now', type=float, help='Test clock only')
    subs = p.add_subparsers(dest='command', required=True)
    init = subs.add_parser('init')
    init.add_argument('--account', required=True)
    init.add_argument('--controller', required=True)
    init.add_argument('--authorized', action='store_true')
    init.add_argument('--preserve-enabled-state', action='store_true', help='Edit settings without activating a disabled monitor')
    init.add_argument('--limit-id', default='codex')
    init.add_argument('--threshold', type=float)
    init.add_argument('--delay-seconds', type=int)
    init.add_argument('--check-seconds', type=int)
    init.add_argument('--expiry-hours', type=int)
    init.add_argument('--message-file', type=Path)
    subs.add_parser('status')
    subs.add_parser('paths')
    subs.add_parser('schedule-plan')
    subs.add_parser('disable')
    observe_p = subs.add_parser('observe')
    observe_p.add_argument('--input', type=Path, required=True)
    reserve = subs.add_parser('reserve')
    reserve.add_argument('--thread', required=True)
    reserve.add_argument('--window', required=True)
    reserve.add_argument('--input', type=Path, required=True)
    commit = subs.add_parser('commit')
    commit.add_argument('--token', required=True)
    commit.add_argument('--outcome', choices=['sent', 'uncertain'], required=True)
    automation = subs.add_parser('set-automation')
    automation.add_argument('--id', required=True)
    automation.add_argument('--due-at', type=float, required=True)
    exclude = subs.add_parser('exclude')
    exclude.add_argument('--thread', required=True)
    args = p.parse_args()
    path = args.state or data_dir() / 'state.json'
    path = path.resolve()
    now = args.now if args.now is not None else time.time()
    number(now, 'now', 1)
    if args.command == 'paths':
        print(json.dumps({'state': str(path), 'script': str(Path(__file__).resolve())}))
        return
    with locked(path):
        if args.command == 'init':
            if not args.authorized or not args.account.strip() or not args.controller.strip():
                raise ValueError('Explicit enable authorization and identities required')
            if path.exists():
                s = load(path)
                if s['accountId'] != args.account or s['controllerId'] != args.controller:
                    raise ValueError('State belongs to another account/controller; stop its monitor and explicitly migrate state first')
                previous_enabled = s['enabled']
            else:
                previous_enabled = False
                s = {'version': VERSION, 'accountId': args.account,
                    'controllerId': args.controller, 'queue': {}, 'excluded': [],
                    'events': [], 'runtime': {}, 'settings': {}, 'automationId': None}
            cfg = s['settings']
            pref_path = path.with_name('preferences.json')
            if not path.exists() and pref_path.exists():
                saved = read_json(pref_path)
                if not isinstance(saved, dict):
                    raise ValueError('Invalid saved preferences')
                cfg.update({k: v for k, v in saved.items() if k in {
                    'threshold', 'delaySeconds', 'checkSeconds', 'expiryHours', 'message', 'limitId',
                    'announcementMessage', 'announcementCheckMinutes', 'announcementKeyword'}})
            if 'announcementKeyword' in cfg:
                import announcements
                cfg['announcementKeyword'] = announcements.normalize_keyword(cfg['announcementKeyword'])
            defaults = {'threshold': 2, 'delaySeconds': 120, 'checkSeconds': 60,
                        'expiryHours': 72, 'message': '继续任务', 'limitId': args.limit_id}
            for key, value in defaults.items():
                cfg.setdefault(key, value)
            for arg, key, lo, hi in [
                ('threshold', 'threshold', 0.01, 99),
                ('delay_seconds', 'delaySeconds', 60, 3600),
                ('check_seconds', 'checkSeconds', 30, 3600),
                ('expiry_hours', 'expiryHours', 1, 720)]:
                value = getattr(args, arg)
                if value is not None:
                    cfg[key] = number(value, arg, lo, hi)
            if args.message_file:
                msg = args.message_file.read_text(encoding='utf-8-sig')
                if not msg.strip() or len(msg) > 12000:
                    raise ValueError('Continuation text must contain 1 to 12000 characters')
                cfg['message'] = msg
            for key, lo, hi in [('threshold', 0.01, 99), ('delaySeconds', 60, 3600),
                                ('checkSeconds', 30, 3600), ('expiryHours', 1, 720)]:
                number(cfg[key], key, lo, hi)
            if not isinstance(cfg['message'], str) or not cfg['message'].strip() or len(cfg['message']) > 12000:
                raise ValueError('Invalid continuation message')
            if args.delay_seconds is not None or args.expiry_hours is not None:
                for item in s['queue'].values():
                    if item['status'] == 'pending':
                        item['dueAt'] = item['resetAt'] + cfg['delaySeconds']
                        item['expiresAt'] = item['dueAt'] + cfg['expiryHours'] * 3600
            s['enabled'] = previous_enabled if args.preserve_enabled_state else True
            if s['enabled'] and not previous_enabled:
                start_monitoring(s, now)
            if s['enabled'] and not args.preserve_enabled_state:
                s['authorization'] = {'at': now, 'scope': 'local active root codex chats at threshold',
                                      'messageConfiguredByUser': True}
            event(s, now, 'configured' if args.preserve_enabled_state else 'enabled')
            result = {'enabled': s['enabled'], 'settings': cfg, 'state': str(path)}
        else:
            if not path.exists():
                if args.command == 'status':
                    print(json.dumps({'enabled': False, 'state': str(path), 'configured': False}))
                    return
                raise ValueError('Plugin has not been enabled')
            s = load(path)
            if args.command == 'status':
                result = s
            elif args.command == 'schedule-plan':
                result = {'plan': schedule_plan(s, now), 'schedule': scheduling.health(s, now)}
            elif args.command == 'disable':
                s['enabled'] = False
                if s['settings'].get('announcementEnabled'):
                    import announcements
                    s['settings']['announcementEnabled'] = False
                    announcements.activate(s, False, now)
                event(s, now, 'disabled')
                result = {'enabled': False, 'automationId': s.get('automationId')}
            elif args.command == 'set-automation':
                if not s['enabled']:
                    raise ValueError('Monitor is disabled')
                number(args.due_at, 'due-at', 1)
                timing = scheduling.verify(s, args.id, args.due_at, now)
                s['automationId'] = args.id
                s['scheduledFor'] = args.due_at
                s['scheduleWakeAt'] = timing['wakeAt']
                s['scheduleVerifiedAt'] = timing['verifiedAt']
                s['armLeaseUntil'] = 0
                event(s, now, 'schedule_verified', dueAt=args.due_at, wakeAt=timing['wakeAt'])
                result = {'automationId': args.id, 'scheduledFor': args.due_at, **timing}
            elif args.command in {'observe', 'reserve'}:
                obs = read_json(args.input)
                result = observe(s, obs, now)
                if args.command == 'reserve':
                    match = next((i for i in result['due'] if i['threadId'] == args.thread
                                  and i['windowKey'] == args.window), None)
                    result = {'accepted': False} if match is None else {'accepted': True}
                    if match:
                        item = s['queue'][match['key']]
                        token = uuid.uuid4().hex
                        item.update(status='reserved', token=token, reservedAt=now,
                                    reservedMessage=s['settings']['message'])
                        result.update(token=token, threadId=args.thread, hostId='local',
                                      message=item['reservedMessage'])
            elif args.command == 'commit':
                item = next((i for i in s['queue'].values() if i.get('token') == args.token), None)
                if not item or item['status'] != 'reserved':
                    raise ValueError('Reservation is unavailable or already committed')
                item['status'] = args.outcome
                item['committedAt'] = now
                event(s, now, args.outcome, threadId=item['threadId'])
                result = {'status': args.outcome, 'threadId': item['threadId']}
            elif args.command == 'exclude':
                if args.thread not in s['excluded']:
                    s['excluded'].append(args.thread)
                for item in s['queue'].values():
                    if item['threadId'] == args.thread and item['status'] == 'pending':
                        item['status'] = 'excluded'
                result = {'excluded': s['excluded']}
        if args.command not in {'status', 'schedule-plan'}:
            atomic_write(path, s)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
