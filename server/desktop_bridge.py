"""Optional existing desktop daemon connection. Never starts a new app-server."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

import announcements as ann
import local_catalog
import state as core

UNAVAILABLE = '当前客户端未开放可验证的原生任务控制通道；不能自动群发或停止任务。'


class Desktop:
    def __enter__(self):
        executable = shutil.which('codex')
        if not executable:
            raise ValueError(UNAVAILABLE)
        self.proc = subprocess.Popen([executable, 'app-server', 'proxy'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.messages, self.identifier = queue.Queue(), 0
        def read():
            try:
                for line in self.proc.stdout:
                    try:
                        value = json.loads(line)
                        if 'id' in value:
                            self.messages.put(value)
                    except ValueError:
                        pass
            finally:
                self.messages.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'codex_task_resume_native', 'version': '1.0.0'}})
            self.proc.stdin.write(json.dumps({'method': 'initialized', 'params': {}})+'\n')
            self.proc.stdin.flush()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise ValueError(UNAVAILABLE) from None

    def call(self, method, params):
        if method not in {'initialize', 'account/rateLimits/read', 'thread/loaded/list', 'thread/list',
                          'thread/read', 'thread/turns/list', 'model/list', 'turn/interrupt',
                          'thread/resume', 'turn/start'}:
            raise ValueError('Unsupported desktop operation')
        self.identifier += 1
        identifier = self.identifier
        self.proc.stdin.write(json.dumps({'id': identifier, 'method': method, 'params': params})+'\n')
        self.proc.stdin.flush()
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            try:
                result = self.messages.get(timeout=max(.01, deadline-time.monotonic()))
            except queue.Empty:
                raise ValueError(UNAVAILABLE) from None
            if result is None:
                raise ValueError(UNAVAILABLE)
            if result.get('id') == identifier:
                if 'error' in result:
                    raise ValueError('原生任务接口拒绝请求，未确认操作成功。')
                return result['result']
        raise ValueError(UNAVAILABLE)

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

    def latest(self, identifier):
        turns = self.call('thread/turns/list', {'threadId': identifier, 'limit': 1,
                                               'sortDirection': 'desc', 'itemsView': 'full'})['data']
        return turns[0] if turns else {}

    def verify(self, s):
        usage = self.call('account/rateLimits/read', {})
        if core.quota(usage, s['settings']['limitId'])['accountId'] != s['accountId']:
            raise ValueError('账号不匹配；公告群发和停止任务已阻止。')
        loaded = self.call('thread/loaded/list', {}).get('data', [])
        # A stored thread in another process is insufficient. Match a loaded
        # native session against a real lifecycle-hook turn on this computer.
        proof = [(sid, rt) for sid, rt in s.get('runtime', {}).items()
                 if sid in loaded and rt.get('turnId')]
        if not any(self.latest(sid).get('id') == rt['turnId'] for sid, rt in proof):
            raise ValueError(UNAVAILABLE)
        return usage

    def models(self):
        cursor, seen = None, set()
        while True:
            args = {'limit': 100, 'includeHidden': False}
            if cursor:
                args['cursor'] = cursor
            data = self.call('model/list', args)
            for m in data['data']:
                if m.get('model', m.get('id')) == ann.MODEL and any(
                        e.get('reasoningEffort') == ann.EFFORT for e in m.get('supportedReasoningEfforts', [])):
                    return True
            cursor = data.get('nextCursor')
            if not cursor:
                return False
            if cursor in seen:
                raise ValueError('模型分页重复，未确认模型可用。')
            seen.add(cursor)

    def inventory(self, s):
        cursor, seen, output = None, set(), []
        while True:
            args = {'limit': 100, 'archived': False, 'sourceKinds': ['cli', 'vscode', 'exec', 'appServer']}
            if cursor:
                args['cursor'] = cursor
            data = self.call('thread/list', args)
            output.extend(t for t in data['data'] if local_catalog.normalize(t, s)['selectable'])
            cursor = data.get('nextCursor')
            if not cursor:
                return output
            if cursor in seen:
                raise ValueError('对话分页重复，未读取完整列表。')
            seen.add(cursor)


def capability(path, factory=Desktop):
    if not path.exists():
        return {'available': False, 'reason': '请先初始化本机控制对话。'}
    try:
        s = core.load(path)
        with factory() as desktop:
            desktop.verify(s)
            if not desktop.models():
                raise ValueError('当前账号不支持 GPT-6 Astra 极高，未自动替换其他模型。')
        return {'available': True, 'reason': '原生控制通道与模型已核实。'}
    except Exception as error:
        return {'available': False, 'reason': str(error) if isinstance(error, ValueError) else UNAVAILABLE}


def normalized(raw, turn, s):
    allowed = local_catalog.normalize(raw, s)['selectable']
    status = raw.get('status', {}).get('type', 'unknown')
    assessment = 'unknown'
    messages = [i for i in turn.get('items', []) if i.get('type') in {'agentMessage', 'userMessage'}]
    last = messages[-1] if messages else {}
    text = last.get('text', '')
    # Completed projects can receive a NEW review. A completed turn asking for
    # input is blocked; no semantic guess is made from a title or summary.
    if (allowed and turn.get('status') == 'completed' and last.get('type') == 'agentMessage'
            and last.get('phase') in {'final_answer', 'final'} and text.strip()
            and not any(x in text for x in ['?', '？', '请确认', '请提供', '请告诉我', '需要你', 'please confirm', 'please provide'])):
        assessment = 'ready_for_review'
    return {'id': raw['id'], 'title': raw.get('name') or raw.get('threadName') or raw['id'],
        'kind': 'codex', 'hostId': 'local', 'archived': raw.get('archived', False),
        'isSubagent': not allowed, 'status': status, 'turnId': turn.get('id'),
        'assessment': assessment, 'evidence': 'Verified shared native desktop, completed work with final response; no unanswered input or approval.' if assessment == 'ready_for_review' else ''}
