"""Event-driven native automation when the installed client exposes its daemon."""
import time
import uuid

import announcements as ann
from desktop_bridge import Desktop, normalized, UNAVAILABLE
import state as core


def commit(s, token, outcome, now):
    for event in s.get('announcements', {}).get('events', {}).values():
        for target in event.get('targets', {}).values():
            if target.get('token') == token and target['status'] == 'reserved':
                target.update(status=outcome, committedAt=now)
                ann.finish(event)
                return
    raise ValueError('Reservation not found; never replay the send')


def stop_running(path, desktop, now, cancelled=lambda: False):
    with core.locked(path):
        s = core.load(path)
        request = s.get('announcements', {}).get('stopRequest', {})
        if not ann.enabled(s) or request.get('status') != 'pending':
            return False
    if not request.get('prepared'):
        targets = {}
        for raw in desktop.inventory(s):
            if raw.get('status', {}).get('type') == 'active':
                turn = desktop.latest(raw['id'])
                if turn.get('status') == 'inProgress' and turn.get('id'):
                    targets[raw['id']] = {'turnId': turn['id'], 'status': 'pending'}
        with core.locked(path):
            s = core.load(path)
            request = s['announcements']['stopRequest']
            if not ann.enabled(s):
                return False
            request.update(targets=targets, prepared=True)
            core.atomic_write(path, s)
    for identifier, item in request['targets'].items():
        if cancelled():
            return False
        if item['status'] != 'pending':
            continue
        with core.locked(path):
            s = core.load(path)
            if not ann.enabled(s):
                return False
        desktop.verify(s)
        raw = desktop.call('thread/read', {'threadId': identifier})['thread']
        turn = desktop.latest(identifier)
        current = normalized(raw, turn, s)
        if (not ann.identity(s, current) or turn.get('id') != item['turnId']
                or raw.get('status', {}).get('type') != 'active' or turn.get('status') != 'inProgress'):
            outcome = 'skipped'
        else:
            with core.locked(path):
                fresh = core.load(path)
                current = fresh.get('announcements', {}).get('stopRequest', {}).get('targets', {}).get(identifier)
                if (not ann.enabled(fresh) or fresh['accountId'] != s['accountId']
                        or not current or current.get('turnId') != item['turnId']):
                    return False
                if current['status'] != 'pending':
                    continue
                current['status'] = 'reserved'
                core.atomic_write(path, fresh)
            try:
                if cancelled():
                    raise ValueError('Client disconnected before interruption')
                desktop.call('turn/interrupt', {'threadId': identifier, 'turnId': item['turnId']})
                after = desktop.latest(identifier)
                outcome = 'stopped' if after.get('id') == item['turnId'] and after.get('status') == 'interrupted' else 'uncertain'
            except Exception:
                outcome = 'uncertain'
        with core.locked(path):
            s = core.load(path)
            s['announcements']['stopRequest']['targets'][identifier].update(status=outcome, checkedAt=time.time())
            core.atomic_write(path, s)
    with core.locked(path):
        s = core.load(path)
        request = s['announcements']['stopRequest']
        request['status'] = 'needs_attention' if any(t['status'] in {'uncertain', 'reserved'} for t in request['targets'].values()) else 'done'
        core.atomic_write(path, s)
    return True


def run_pending(path, desktop, now, cancelled=lambda: False):
    with core.locked(path):
        s = core.load(path)
        if not ann.enabled(s) or not ann.pending_events(s, now):
            return
        if s.get('announcements', {}).get('stopRequest', {}).get('status') in {'pending', 'needs_attention'}:
            return  # Never start another project while a reset stop is unresolved.
    if not desktop.models():
        raise ValueError('当前账号不支持 GPT-6 Astra 极高；未替换其他模型。')
    events = ann.pending_events(s, now)
    if any(not e.get('prepared') for e in events):
        usage = desktop.verify(s)
        rows = [normalized(raw, {}, s) for raw in desktop.inventory(s)]
        with core.locked(path):
            s = core.load(path)
            ann.prepare(s, {'observedAt': time.time(), 'usage': usage,
                'inventoryComplete': True, 'threads': rows}, time.time())
            core.atomic_write(path, s)
    for e in ann.pending_events(s, time.time()):
        for identifier, item in e['targets'].items():
            if cancelled():
                return
            if item['status'] != 'pending':
                continue
            with core.locked(path):
                s = core.load(path)
                if not ann.enabled(s):
                    return
            usage = desktop.verify(s)
            with core.locked(path):
                fresh = core.load(path)
                if ann.watch_quota(fresh, usage, time.time()):
                    core.atomic_write(path, fresh)
                    return  # Stop processing ALWAYS precedes another start.
                core.atomic_write(path, fresh)
            raw = desktop.call('thread/read', {'threadId': identifier})['thread']
            turn = desktop.latest(identifier)
            observation = {'observedAt': time.time(), 'usage': usage, 'threads': [normalized(raw, turn, s)]}
            if observation['threads'][0]['status'] == 'active':
                with core.locked(path):
                    fresh = core.load(path)
                    ann.settle(fresh, e['id'], identifier, 'deferred', time.time())
                    core.atomic_write(path, fresh)
                continue
            if observation['threads'][0]['assessment'] != 'ready_for_review':
                with core.locked(path):
                    fresh = core.load(path)
                    ann.settle(fresh, e['id'], identifier, 'skipped', time.time())
                    core.atomic_write(path, fresh)
                continue
            # Load only this already verified native chat, retaining permissions.
            desktop.call('thread/resume', {'threadId': identifier})
            usage = desktop.verify(s)
            raw = desktop.call('thread/read', {'threadId': identifier})['thread']
            turn = desktop.latest(identifier)
            observation = {'observedAt': time.time(), 'usage': usage, 'threads': [normalized(raw, turn, s)]}
            with core.locked(path):
                fresh = core.load(path)
                ann.watch_quota(fresh, usage, time.time())
                reservation = ann.reserve(fresh, e['id'], identifier, observation, time.time())
                core.atomic_write(path, fresh)
            if not reservation.get('accepted'):
                return
            outcome = 'uncertain'
            try:
                if cancelled():
                    raise ValueError('Client disconnected before send')
                response = desktop.call('turn/start', {'threadId': identifier,
                    'input': [{'type': 'text', 'text': reservation['message']}],
                    'model': reservation['model'], 'effort': reservation['thinking']})
                if response.get('turn', {}).get('id') and response['turn'].get('status') in {'inProgress', 'completed'}:
                    outcome = 'sent'
            finally:
                with core.locked(path):
                    fresh = core.load(path)
                    commit(fresh, reservation['token'], outcome, time.time())
                    core.atomic_write(path, fresh)
            return  # One start per tick; quota is checked again before the next.


def tick(path, factory=Desktop, now=None, cancelled=lambda: False):
    now = time.time() if now is None else now
    if not path.exists():
        return
    with core.locked(path):
        s = core.load(path)
        if not ann.enabled(s):
            return
    ann.tick(path, now=now)
    if cancelled():
        return
    token = uuid.uuid4().hex
    with core.locked(path):
        s = core.load(path)
        a = s['announcements']
        if not ann.enabled(s) or now < a.get('nativeLeaseUntil', 0) or now-a.get('lastControlAt', 0) < s['settings']['checkSeconds']:
            return
        a.update(lastControlAt=now, nativeLeaseUntil=now+180, nativeLeaseToken=token)
        core.atomic_write(path, s)
    try:
        with factory() as desktop:
            usage = desktop.verify(s)
            with core.locked(path):
                fresh = core.load(path)
                ann.watch_quota(fresh, usage, now)
                fresh['announcements']['control'] = {'available': True, 'reason': '原生控制通道已核实。', 'checkedAt': now}
                core.atomic_write(path, fresh)
            if not cancelled() and not stop_running(path, desktop, now, cancelled):
                run_pending(path, desktop, now, cancelled)
    except Exception as error:
        with core.locked(path):
            fresh = core.load(path)
            fresh.setdefault('announcements', {})['control'] = {'available': False,
                'reason': str(error) if isinstance(error, ValueError) else UNAVAILABLE, 'checkedAt': now}
            core.atomic_write(path, fresh)
    finally:
        with core.locked(path):
            fresh = core.load(path)
            if fresh.get('announcements', {}).get('nativeLeaseToken') == token:
                fresh['announcements']['nativeLeaseUntil'] = 0
                core.atomic_write(path, fresh)
