"""Model-free checks while a native MCP connection is alive. Never sends messages."""
import os
import threading
import time
import uuid

import state as core
import hook


def tick(path, usage_provider=hook.read_usage, now=None):
    now = time.time() if now is None else now
    if not path.exists():
        return
    token = uuid.uuid4().hex
    with core.locked(path):
        state = core.load(path)
        if not state['enabled']:
            return
        state['serviceHeartbeatAt'] = now
        state['servicePid'] = os.getpid()
        if now-state.get('lastProbeAt', 0) < state['settings']['checkSeconds'] or now < state.get('probeLeaseUntil', 0):
            core.atomic_write(path, state)
            return
        state.update(probeToken=token, probeLeaseUntil=now+25, lastProbeAt=now)
        core.atomic_write(path, state)
    try:
        usage = usage_provider()
        with core.locked(path):
            state = core.load(path)
            if state.get('probeToken') != token:
                return
            state['probeLeaseUntil'] = 0
            if state['enabled']:
                threads = core.runtime_threads(state, now)
                core.observe(state, {'observedAt': now, 'usage': usage, 'threads': threads}, now)
                state['lastLiveUsage'] = usage
                state['lastProbeError'] = None
                state['lastProbeSuccessAt'] = now
                state['lastProbeSource'] = 'connected_service'
            core.atomic_write(path, state)
    except Exception:
        with core.locked(path):
            state = core.load(path)
            if state.get('probeToken') == token:
                state['probeLeaseUntil'] = 0
                state['lastProbeError'] = 'Read-only quota unavailable; no send authorized.'
                core.atomic_write(path, state)


class Worker:
    def __init__(self, path):
        self.path = path
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                tick(self.path)
            except Exception:
                # A corrupt or inaccessible state must not break MCP transport.
                pass
            self.stop_event.wait(5)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=20)
