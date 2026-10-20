"""Read-only event detector. No inference, thread mutation, or message sending."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from queue import Empty

# Load the sibling state helper without modifying global import paths.
import importlib.util
spec = importlib.util.spec_from_file_location('skillhub_state', Path(__file__).with_name('state.py'))
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
import announcements


def read_usage(codex=None):
    executable = codex or shutil.which('codex')
    if not executable:
        raise ValueError('Codex CLI unavailable; local checks disabled')
    # Independent read-only server: never use its thread status for desktop state.
    proc = subprocess.Popen([executable, 'app-server', '--stdio'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding='utf-8', creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
    replies = queue.Queue()
    def reader():
        try:
            for line in proc.stdout:
                try:
                    replies.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        finally:
            replies.put({'error': {'message': 'Read-only server closed'}})
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    def send(obj):
        proc.stdin.write(json.dumps(obj) + '\n')
        proc.stdin.flush()
    def call(identifier, method, params):
        send({'id': identifier, 'method': method, 'params': params})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Read-only quota query timed out')
            try:
                msg = replies.get(timeout=remaining)
            except Empty:
                raise ValueError('Read-only quota query timed out') from None
            if msg.get('id') == identifier or ('error' in msg and 'id' not in msg):
                if 'error' in msg:
                    raise ValueError('Read-only quota query failed')
                return msg['result']
    try:
        call(1, 'initialize', {'clientInfo': {'name': 'skillhub_quota_reader',
             'title': 'Task Resume read-only quota detector', 'version': '1.0.0'}})
        send({'method': 'initialized', 'params': {}})
        return call(2, 'account/rateLimits/read', {})
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        for stream in [proc.stdin, proc.stdout]:
            stream.close()
        thread.join(timeout=1)


def handle(path, payload, usage_provider=read_usage, now=None):
    now = time.time() if now is None else now
    if not path.exists():
        return None
    event_name = payload.get('hook_event_name')
    sid = payload.get('session_id')
    if (not isinstance(sid, str) or not sid or payload.get('source') in ('fork', 'subagent')
            or payload.get('parent_thread_id') or payload.get('is_subagent') is True):
        return None
    probe_token = None
    with core.locked(path):
        s = core.load(path)
        if not s['enabled'] and not announcements.enabled(s):
            return None
        announcements.on_event(s, sid, event_name, now)
        turn_id = payload.get('turn_id')
        previous = s['runtime'].get(sid, {})
        if event_name == 'UserPromptSubmit':
            if not isinstance(turn_id, str) or not turn_id:
                return None
            prompt = payload.get('prompt', '')
            setup = isinstance(prompt, str) and 'quota-resume' in prompt.lower()
            s['runtime'][sid] = {'active': True, 'seenAt': now, 'startedAt': now,
                                 'turnId': turn_id, 'purpose': 'plugin_setup' if setup else 'task'}
            for item in s['queue'].values():
                if item['threadId'] == sid and item['status'] == 'pending' and item.get('turnId') != turn_id:
                    item.update(status='excluded', exclusionReason='superseded_turn')
        elif event_name == 'PostToolUse':
            # Async callbacks cannot resurrect a stopped or superseded turn.
            if not turn_id or previous.get('turnId') != turn_id or not previous.get('active'):
                return None
            previous['seenAt'] = now
        elif event_name in {'Stop', 'Interrupt', 'SessionEnd'}:
            if event_name != 'SessionEnd' and (not turn_id or previous.get('turnId') != turn_id):
                return None
            s['runtime'][sid] = {**previous, 'active': False, 'seenAt': now}
            if event_name == 'Interrupt':
                for item in s['queue'].values():
                    if item['threadId'] == sid and item['status'] == 'pending':
                        item['status'] = 'user_paused'
        elif event_name != 'SessionStart':
            return None
        # Persist activity before an async query; other events keep updating it.
        if (s['enabled'] and now - s.get('lastProbeAt', 0) >= s['settings']['checkSeconds']
                and now >= s.get('probeLeaseUntil', 0)):
            probe_token = uuid.uuid4().hex
            s.update(probeToken=probe_token, probeLeaseUntil=now+25, lastProbeAt=now)
        core.atomic_write(path, s)
    if probe_token:
        try:
            usage = usage_provider()
        except Exception:
            with core.locked(path):
                s = core.load(path)
                if s.get('probeToken') == probe_token:
                    s['probeLeaseUntil'] = 0
                    s['lastProbeError'] = 'Read-only quota unavailable; no schedule or send authorized.'
                    core.atomic_write(path, s)
            return None
        with core.locked(path):
            s = core.load(path)
            if not s['enabled'] or s.get('probeToken') != probe_token:
                return None
            s['probeLeaseUntil'] = 0
            try:
                q = core.canonical_quota(s, core.quota(usage, s['settings']['limitId']))
            except ValueError:
                s['lastProbeError'] = 'Quota schema unavailable; fail closed.'
                core.atomic_write(path, s)
                return None
            if q['accountId'] != s['accountId']:
                s['enabled'] = False
                s['settings']['announcementEnabled'] = False
                core.event(s, now, 'account_changed')
                core.atomic_write(path, s)
                return None
            s['lastProbeError'] = None
            s['lastQuota'] = {**q, 'observedAt': now}
            s['lastLiveUsage'] = usage
            s['lastProbeSuccessAt'] = now
            s['lastProbeSource'] = 'task_event'
            if q['remaining'] < s['settings']['threshold']:
                # Capture event-observed activity, NOT invented cross-process status.
                threads = core.runtime_threads(s, now)
                core.observe(s, {'observedAt': now, 'usage': usage, 'threads': threads}, now)
                s['lastProbeError'] = None
                s['lastProbeSuccessAt'] = now
                s['lastProbeSource'] = 'task_event'
            core.atomic_write(path, s)
    with core.locked(path):
        s = core.load(path)
        if not s['enabled']:
            return None
        core.expire(s, now)
        pending = [i for i in s['queue'].values() if i['status'] == 'pending']
        q = s.get('lastQuota', {})
        # Fresh low-quota cache can capture a task starting within the throttle interval.
        if (event_name in {'UserPromptSubmit', 'PostToolUse'}
                and 0 <= now-q.get('observedAt', 0) <= min(90, s['settings']['checkSeconds'])
                and q.get('remaining', 100) < s['settings']['threshold']):
            # Reuse the last *live* quota only with the same strict activity gate.
            fresh = s.get('lastLiveUsage')
            if fresh:
                core.observe(s, {'observedAt': now, 'usage': fresh,
                                 'threads': core.runtime_threads(s, now)}, now)
                pending = [i for i in s['queue'].values() if i['status'] == 'pending']
        plan = core.schedule_plan(s, now)
        if event_name in {'SessionEnd', 'Interrupt'} or not plan:
            core.atomic_write(path, s)
            return None
        due = plan['dueAt']
        # Successfully scheduled work requires no repeated model instruction.
        if (s.get('automationId') and s.get('scheduledFor')
                and abs(s['scheduledFor']-due) <= 60):
            import scheduling
            try:
                scheduling.verify(s, s['automationId'], due, now)
                s['armRequest'] = None
                core.atomic_write(path, s)
                return None
            except (ValueError, OSError):
                pass
        # A lifecycle hook is a detector, never an instruction to the task model.
        # Persist the request for the controller; do not inject additionalContext,
        # emit system messages, or block Stop to make another chat schedule work.
        previous_request = s.get('armRequest') or {}
        s['armRequest'] = {'dueAt': due, 'nominalAt': plan['nominalAt'],
                           'requestedAt': previous_request.get('requestedAt', now)
                               if previous_request.get('dueAt') == due else now,
                           'controllerId': s['controllerId'], 'status': 'pending'}
        core.atomic_write(path, s)
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path)
    p.add_argument('--probe', action='store_true', help='Read-only compatibility check; no activation')
    args = p.parse_args()
    if args.probe:
        q = core.quota(read_usage(), 'codex')
        print(json.dumps({'remainingPercent': q['remaining'], 'resetsAt': q['reset'],
                          'readOnly': True, 'inferenceRequested': False}))
        return
    payload = json.load(sys.stdin)
    path = args.state or core.data_dir() / 'state.json'
    result = handle(path, payload)
    # Stop requires JSON on stdout, even if there is nothing to do.
    print(json.dumps(result or {}, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as error:
        if '--probe' in sys.argv:
            print('Read-only compatibility probe failed: '+str(error), file=sys.stderr)
            sys.exit(2)
        # Hooks are advisory. Never break a user's work on detector failure.
        print('{}')
        sys.exit(0)
