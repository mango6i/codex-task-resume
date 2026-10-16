import concurrent.futures
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/quota-resume/scripts'
sys.path.insert(0, str(SCRIPTS))
import state as core
import hook


def state():
    return {'version': 1, 'enabled': True, 'accountId': 'a', 'controllerId': 'controller',
            'queue': {}, 'excluded': [], 'events': [], 'runtime': {}, 'automationId': None,
            'settings': {'threshold': 2, 'delaySeconds': 120, 'checkSeconds': 60,
                         'expiryHours': 72, 'message': '继续任务\n保留上一轮要求。', 'limitId': 'codex'}}


def observation(now=1000, used=99, reset=1100, status='active', assessment='unknown', **thread_fields):
    return {'observedAt': now, 'usage': {'accountId': 'a', 'ordinaryUsageAllowed': True,
            'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': used,
             'windowDurationMins': 300, 'resetsAt': reset}, 'secondary': {
             'usedPercent': 20, 'windowDurationMins': 10080, 'resetsAt': 9000}}}},
            'threads': [{'id': 'task', 'hostId': 'local', 'kind': 'codex',
             'archived': False, 'isSubagent': False, 'status': status,
             'assessment': assessment, 'evidence': 'The requested verification remains unfinished.',
             'turnId': 'work-turn', 'turnStartedAt': 900, 'turnCompletedAt': 1105,
             'turnStatus': 'failed', 'stopReason': 'quota_exhausted', 'purpose': 'task',
             **thread_fields}]}


class Behavior(unittest.TestCase):
    def captured(self):
        s = state()
        r = core.observe(s, observation(), 1000)
        self.assertEqual(r['nextWakeAt'], 1220)
        return s

    def test_strict_threshold_and_configurable_threshold(self):
        s = state()
        self.assertFalse(core.observe(s, observation(used=98), 1000)['pendingCount'])
        s['settings']['threshold'] = 10
        self.assertEqual(core.observe(s, observation(used=91), 1000)['pendingCount'], 1)

    def test_no_early_send_and_real_recovery(self):
        s = self.captured()
        o = observation(now=1219, used=0, reset=2000, status='idle', assessment='quota_interrupted')
        self.assertFalse(core.observe(s, o, 1219)['due'])
        o['observedAt'] = 1220
        self.assertEqual(len(core.observe(s, o, 1220)['due']), 1)
        o['usage']['rateLimitsByLimitId']['codex']['primary']['resetsAt'] = 1101
        self.assertFalse(core.observe(s, o, 1220)['due'])

    def test_rounding_does_not_duplicate_windows(self):
        s = self.captured()
        core.observe(s, observation(now=1001, reset=1101), 1001)
        self.assertEqual(len(s['queue']), 1)

    def test_busy_completed_paused_unknown_and_scope(self):
        for status, assessment, fields in [
            ('active', 'incomplete', {}), ('needsAttention', 'incomplete', {}),
            ('idle', 'unknown', {}), ('idle', 'completed', {}),
            ('idle', 'user_paused', {}), ('idle', 'awaiting_input', {}),
            ('idle', 'incomplete', {'archived': True}),
            ('idle', 'incomplete', {'hostId': 'remote'}),
            ('idle', 'incomplete', {'isSubagent': True}),
            ('idle', 'incomplete', {'kind': 'chatgpt'})]:
            with self.subTest(status=status, assessment=assessment, fields=fields):
                s = self.captured()
                r = core.observe(s, observation(1220, 0, 2000, status, assessment, **fields), 1220)
                self.assertFalse(r['due'])

    def test_weekly_exhaustion_and_unknown_usage_fail_closed(self):
        s = self.captured()
        o = observation(1220, 0, 2000, 'idle', 'quota_interrupted')
        o['usage']['rateLimitsByLimitId']['codex']['secondary']['usedPercent'] = 100
        r = core.observe(s, o, 1220)
        self.assertFalse(r['due'])
        self.assertEqual(r['nextWakeAt'], 9120)
        del o['usage']['rateLimitsByLimitId']['codex']['secondary']['usedPercent']
        with self.assertRaises(ValueError):
            core.observe(s, o, 1220)

    def test_missing_bucket_stale_snapshot_and_account_change(self):
        s = self.captured()
        with self.assertRaises(ValueError):
            core.observe(s, observation(), 1220)
        o = observation(1220)
        o['usage']['rateLimitsByLimitId'] = {'other': {}}
        with self.assertRaises(ValueError):
            core.observe(s, o, 1220)
        o = observation(1220)
        o['usage']['accountId'] = 'different'
        self.assertFalse(core.observe(s, o, 1220)['enabled'])

    def test_controller_and_exclusions_never_captured(self):
        for fields in [{'id': 'controller'}, {'id': 'excluded'}]:
            s = state()
            s['excluded'] = ['excluded']
            self.assertEqual(core.observe(s, observation(**fields), 1000)['pendingCount'], 0)

    def test_expiration(self):
        s = self.captured()
        now = 1221+72*3600
        r = core.observe(s, observation(now, 0, now+1000, 'idle', 'incomplete'), now)
        self.assertFalse(r['due'])
        self.assertEqual(next(iter(s['queue'].values()))['status'], 'expired')

    def test_event_throttle_disabled_hook_and_manual_interrupt(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            s = state()
            core.atomic_write(path, s)
            calls = []
            def provider():
                calls.append(1)
                return observation()['usage']
            payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'task',
                       'turn_id': 'work-turn', 'prompt': 'Finish the requested feature'}
            result = hook.handle(path, payload, provider, 1000)
            self.assertIn('hookSpecificOutput', result)
            self.assertIsNone(hook.handle(path, {**payload, 'hook_event_name': 'PostToolUse'}, provider, 1001))
            self.assertEqual(len(calls), 1)
            hook.handle(path, {'hook_event_name': 'Interrupt', 'session_id': 'task', 'turn_id': 'work-turn'}, provider, 1002)
            self.assertEqual(next(iter(core.load(path)['queue'].values()))['status'], 'user_paused')
            s = core.load(path)
            s['enabled'] = False
            core.atomic_write(path, s)
            self.assertIsNone(hook.handle(path, payload, provider, 2000))
            self.assertEqual(len(calls), 1)

    def test_atomic_reserve_restart_and_exact_custom_message(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            s = self.captured()
            core.atomic_write(path, s)
            obs = Path(td)/'obs.json'
            core.atomic_write(obs, observation(1220, 0, 2000, 'idle', 'quota_interrupted'))
            key = next(iter(s['queue'].values()))['windowKey']
            cmd = [sys.executable, str(SCRIPTS/'state.py'), '--state', str(path), '--now', '1220',
                   'reserve', '--thread', 'task', '--window', key, '--input', str(obs)]
            def reserve():
                r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(r.returncode, 0, r.stderr)
                return json.loads(r.stdout)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: reserve(), range(2)))
            accepted = [r for r in results if r['accepted']]
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0]['message'], s['settings']['message'])
            self.assertFalse(core.observe(core.load(path), core.read_json(obs), 1220)['due'])
            commit = subprocess.run([sys.executable, str(SCRIPTS/'state.py'), '--state', str(path),
                '--now', '1221', 'commit', '--token', accepted[0]['token'], '--outcome', 'sent'],
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(commit.returncode, 0, commit.stderr)
            self.assertEqual(next(iter(core.load(path)['queue'].values()))['status'], 'sent')

    def test_configuration_does_not_enable_disabled_monitor(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            s = self.captured()
            s['enabled'] = False
            core.atomic_write(path, s)
            message = Path(td)/'message.txt'
            message.write_text('继续任务\n请保持上一轮范围。', encoding='utf-8')
            r = subprocess.run([sys.executable, str(SCRIPTS/'state.py'), '--state', str(path),
                '--now', '1100', 'init', '--account', 'a', '--controller', 'controller',
                '--authorized', '--preserve-enabled-state', '--threshold', '5',
                '--delay-seconds', '60', '--message-file', str(message)],
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(r.returncode, 0, r.stderr)
            saved = core.load(path)
            self.assertFalse(saved['enabled'])
            self.assertEqual(saved['settings']['threshold'], 5)
            self.assertEqual(saved['settings']['message'], message.read_text(encoding='utf-8'))
            self.assertEqual(next(iter(saved['queue'].values()))['dueAt'], 1160)


if __name__ == '__main__':
    unittest.main()
