"""Read-only local inventory and explicit target selection. Never starts a turn."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

import state as core


class Reader:
    def __enter__(self):
        self.deadline = time.monotonic()+getattr(self, 'total_timeout', float('inf'))
        executable = shutil.which('codex')
        if not executable:
            raise ValueError('未找到 Codex CLI，无法读取本地对话。')
        self.proc = subprocess.Popen([executable, 'app-server', '--stdio'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.messages = queue.Queue()
        self.identifier = 0
        def read():
            try:
                for line in self.proc.stdout:
                    try:
                        self.messages.put(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            finally:
                self.messages.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'task_resume_inventory', 'version': '1.0.0'},
                'capabilities': {'experimentalApi': True}})
            self.proc.stdin.write(json.dumps({'method': 'initialized', 'params': {}})+'\n')
            self.proc.stdin.flush()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def call(self, method, params):
        if method not in {'initialize', 'account/rateLimits/read', 'thread/list', 'thread/read'}:
            raise ValueError('Inventory only allows read-only requests.')
        self.identifier += 1
        identifier = self.identifier
        self.proc.stdin.write(json.dumps({'id': identifier, 'method': method, 'params': params})+'\n')
        self.proc.stdin.flush()
        deadline = min(time.monotonic()+15, self.deadline)
        while True:
            try:
                message = self.messages.get(timeout=max(0.01, deadline-time.monotonic()))
            except queue.Empty:
                raise ValueError('本地对话读取超时，请稍后刷新。') from None
            if message is None:
                raise ValueError('本地只读连接已关闭。')
            if message.get('id') == identifier:
                if 'error' in message:
                    raise ValueError('本地服务暂时无法读取数据，请稍后重试。')
                return message['result']
            if time.monotonic() >= deadline:
                raise ValueError('本地读取超时。')

    def __exit__(self, *args):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.reader.join(timeout=1)


def selection(state):
    return state.get('selection', {'mode': 'automatic', 'threads': {}}) if state else {'mode': 'automatic', 'threads': {}}


def normalize(thread, state):
    identifier = thread.get('id')
    source = thread.get('source')
    # Only explicit, recognized local root sources. No summary inference.
    root_source = source in {'cli', 'vscode', 'exec', 'appServer'} if isinstance(source, str) else False
    cwd = thread.get('cwd')
    local_path = isinstance(cwd, str) and Path(cwd).is_absolute() and not cwd.startswith(('\\\\', '//'))
    allowed = bool(isinstance(identifier, str) and root_source and local_path and not thread.get('ephemeral')
                   and not thread.get('archived', False) and not thread.get('parentThreadId')
                   and (not state or identifier != state.get('controllerId'))
                   and (not state or identifier not in state.get('excluded', [])))
    activity = (state or {}).get('runtime', {}).get(identifier, {})
    recent = time.time()-activity.get('seenAt', 0) < 86400
    return {'id': identifier, 'title': thread.get('name') or thread.get('threadName') or '未命名对话',
            'project': cwd or '', 'updatedAt': thread.get('updatedAt'), 'selectable': allowed,
            'activity': ('observed_active' if activity.get('active') else 'observed_stopped') if recent else 'unverified',
            'lastEventAt': activity.get('seenAt') if activity else None,
            'reason': '' if allowed else '不支持的来源、非本地任务、控制对话或已排除任务'}


def saved_state(path):
    return core.load(path) if path.exists() else None


def monitor_state(state, quota):
    pending = sum(item['status'] == 'pending' for item in (state or {}).get('queue', {}).values())
    enabled = bool(state and state['enabled'])
    scheduled = bool(state and state.get('automationId') and state.get('scheduledFor'))
    threshold = (state or {}).get('settings', {}).get('threshold', 2)
    if not state:
        phase = 'setup_required'
    elif not enabled:
        phase = 'disabled'
    elif scheduled:
        phase = 'schedule_record'
    elif pending:
        phase = 'pending_unscheduled'
    elif quota['remaining'] >= threshold:
        phase = 'waiting_threshold'
    else:
        phase = 'low_quota'
    return {'initialized': bool(state), 'enabled': enabled, 'threshold': threshold,
            'pendingCount': pending, 'scheduled': scheduled, 'phase': phase,
            'scheduledFor': (state or {}).get('scheduledFor'),
            'selectedCount': len(selection(state)['threads']),
            'lastCheckedAt': (state or {}).get('lastQuota', {}).get('observedAt'),
            'lastEventAt': max([v.get('seenAt', 0) for v in (state or {}).get('runtime', {}).values()] or [0]) or None,
            'serviceConnected': time.time()-(state or {}).get('serviceHeartbeatAt', 0) < 20,
            'lastProbeError': (state or {}).get('lastProbeError'),
            'hookTrust': 'not_checked', 'modelFreeRead': True}


def read_quota(path, reader_factory=Reader):
    state = saved_state(path)
    usage = None
    for attempt in range(2):
        try:
            connection = reader_factory()
            connection.total_timeout = 8
            with connection as reader:
                usage = reader.call('account/rateLimits/read', {})
            break
        except (ValueError, OSError):
            if attempt == 0:
                time.sleep(0.3)
    if usage is None:
        cached = (state or {}).get('lastQuota', {})
        age = time.time()-cached.get('observedAt', 0)
        if (state and cached.get('accountId') == state.get('accountId') and 0 <= age <= 300
                and isinstance(cached.get('remaining'), (float, int)) and isinstance(cached.get('reset'), (float, int))):
            return {'quota': {'remaining': cached['remaining'], 'resetAt': cached['reset'],
                             'readAt': cached['observedAt']}, 'stale': True,
                    'monitor': monitor_state(state, cached)}
        raise ValueError('暂时无法连接额度服务，请稍后重试。')
    quota = core.quota(usage, (state or {}).get('settings', {}).get('limitId', 'codex'))
    if state and quota['accountId'] != state['accountId']:
        raise ValueError('登录账号已改变，请先停止监控并重新关联。')
    return {'quota': {'remaining': quota['remaining'], 'resetAt': quota['reset'], 'readAt': time.time()}, 'stale': False,
            'monitor': monitor_state(state, quota)}


def list_threads(path, cursor=None, reader_factory=Reader):
    state = saved_state(path)
    with reader_factory() as reader:
        usage = reader.call('account/rateLimits/read', {})
        quota = core.quota(usage, (state or {}).get('settings', {}).get('limitId', 'codex'))
        if state and quota['accountId'] != state['accountId']:
            raise ValueError('登录账号已改变，请先停止监控并重新关联。')
        params = {'limit': 100, 'sortKey': 'updated_at', 'archived': False, 'useStateDbOnly': True,
                  'sourceKinds': ['cli', 'vscode', 'exec', 'appServer']}
        if cursor is not None:
            params['cursor'] = cursor
        result = reader.call('thread/list', params)
    chosen = selection(state)
    return {'threads': [normalize(thread, state) for thread in result['data']],
            'nextCursor': result.get('nextCursor'), 'selection': {'mode': chosen['mode'],
                'threadIds': list(chosen['threads'])},
            'quota': {'remaining': quota['remaining'], 'resetAt': quota['reset']},
            'monitor': monitor_state(state, quota)}


def update_selection(path, mode, identifiers, reader_factory=Reader, now=None):
    if mode not in {'automatic', 'selected'} or not isinstance(identifiers, list) or len(identifiers) > 200:
        raise ValueError('选择格式不正确，最多选择 200 个本地对话。')
    if any(not isinstance(i, str) or not i for i in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError('对话标识无效或重复。')
    before = saved_state(path)
    if not before or not before.get('accountId') or not before.get('controllerId'):
        raise ValueError('全局控制尚未初始化。只需初始化一个控制对话，不需要逐个对话设置。')
    with reader_factory() as reader:
        usage = reader.call('account/rateLimits/read', {})
        quota = core.quota(usage, before['settings']['limitId'])
        if quota['accountId'] != before['accountId']:
            raise ValueError('登录账号已改变，未保存选择。')
        targets = {}
        for identifier in identifiers:
            raw = reader.call('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
            item = normalize(raw, before)
            if item['id'] != identifier or not item['selectable']:
                raise ValueError('所选对话已不可用或不在支持范围内，未保存选择。')
            targets[identifier] = {k: item[k] for k in ['title', 'project', 'updatedAt']}
        usage = reader.call('account/rateLimits/read', {})
        if core.quota(usage, before['settings']['limitId'])['accountId'] != before['accountId']:
            raise ValueError('登录账号已改变，未保存选择。')
    now = time.time() if now is None else now
    with core.locked(path):
        state = core.load(path)
        if state['accountId'] != before['accountId'] or state['controllerId'] != before['controllerId']:
            raise ValueError('监控归属已改变，未保存选择。')
        old = selection(state)
        state['selection'] = {'mode': mode, 'threads': targets, 'savedAt': now}
        for item in state['queue'].values():
            if item['status'] == 'pending' and (mode == 'selected' and item['threadId'] not in targets
                    or item.get('captureSource') == 'manual' and item['threadId'] in old['threads'] and item['threadId'] not in targets):
                item['status'] = 'excluded'
                item['exclusionReason'] = 'selection_removed'
        # Manual selection can capture an already-stopped task at exhausted quota.
        # It never authorizes a send or bypasses the later native status check.
        core.observe(state, {'observedAt': now, 'usage': usage, 'threads': []}, now)
        core.event(state, now, 'selection_saved', count=len(targets), mode=mode)
        core.atomic_write(path, state)
    quota = core.quota(usage, state['settings']['limitId'])
    return {'mode': mode, 'threadIds': list(targets), 'pendingCount': sum(i['status'] == 'pending' for i in state['queue'].values()),
            'scheduled': bool(state.get('automationId') and state.get('scheduledFor')), 'sent': False,
            'quota': {'remaining': quota['remaining'], 'resetAt': quota['reset']},
            'monitor': monitor_state(state, quota)}
