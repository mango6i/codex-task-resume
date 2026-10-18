import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/quota-resume/scripts')]
import local_catalog as catalog
import state as core
import preferences
import settings_mcp
import hook


def usage(account='a', used=100):
    return {'accountId': account, 'ordinaryUsageAllowed': True, 'rateLimitsByLimitId': {
        'codex': {'primary': {'usedPercent': used, 'windowDurationMins': 300, 'resetsAt': 2000000000}}}}


class FakeReader:
    calls = []
    account = 'a'
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'account/rateLimits/read':
            return usage(self.account)
        if method == 'thread/list':
            return {'data': [self.thread('task-a'), self.thread('controller')], 'nextCursor': None}
        if method == 'thread/read':
            return {'thread': self.thread(params['threadId'])}
        raise AssertionError('Unexpected mutation: '+method)
    def thread(self, identifier):
        return {'id': identifier, 'name': '<script>Local task</script>', 'source': 'vscode',
                'cwd': str(ROOT), 'updatedAt': 123, 'status': {'type': 'notLoaded'}}


class Catalog(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'state.json'
        result = subprocess.run([sys.executable, str(ROOT/'skills/quota-resume/scripts/state.py'),
            '--state', str(self.path), '--now', '900', 'init', '--account', 'a', '--controller', 'controller', '--authorized'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        FakeReader.calls = []
        FakeReader.account = 'a'
    def tearDown(self):
        self.temp.cleanup()

    def test_listing_is_read_only_and_independent_status_is_not_desktop_status(self):
        original = self.path.read_bytes()
        result = catalog.list_threads(self.path, reader_factory=FakeReader)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(result['threads'][0]['selectable'])
        self.assertEqual(result['threads'][0]['activity'], 'unverified')
        self.assertFalse(result['threads'][1]['selectable'])
        self.assertFalse(result['monitor']['scheduled'])
        self.assertTrue(result['monitor']['modelFreeRead'])
        self.assertEqual([m for m, _ in FakeReader.calls], ['account/rateLimits/read', 'thread/list'])

    def test_manual_selection_is_only_scope_and_never_captures_idle_targets(self):
        result = catalog.update_selection(self.path, 'selected', ['task-a'], FakeReader, now=1000)
        state = core.load(self.path)
        self.assertTrue(state['enabled'])
        self.assertEqual(result['pendingCount'], 0)
        self.assertFalse(result['sent'])
        self.assertFalse(result['scheduled'])
        self.assertEqual(state['queue'], {})
        self.assertTrue(all(m in {'account/rateLimits/read', 'thread/read'} for m, _ in FakeReader.calls))
        catalog.update_selection(self.path, 'selected', [], FakeReader, now=1001)
        self.assertEqual(core.load(self.path)['queue'], {})
        catalog.update_selection(self.path, 'selected', ['task-a'], FakeReader, now=1002)
        self.assertEqual(core.load(self.path)['queue'], {})

    def test_selected_scope_blocks_automatic_capture_and_preserves_completion_checks(self):
        catalog.update_selection(self.path, 'selected', ['task-a'], FakeReader, now=1000)
        state = core.load(self.path)
        obs = {'observedAt': 1001, 'usage': usage(), 'threads': [{'id': 'unselected', 'hostId': 'local',
            'kind': 'codex', 'archived': False, 'isSubagent': False, 'status': 'active',
            'turnId': 'work-turn', 'turnStartedAt': 950, 'purpose': 'task'}]}
        core.observe(state, obs, 1001)
        self.assertEqual(len(state['queue']), 0)
        obs['threads'][0]['id'] = 'task-a'
        core.observe(state, obs, 1001)
        self.assertEqual(len(state['queue']), 1)
        obs['threads'] = [{'id': 'task-a', 'hostId': 'local', 'kind': 'codex', 'archived': False,
            'isSubagent': False, 'status': 'notLoaded', 'assessment': 'completed'}]
        core.observe(state, obs, 1001)
        self.assertEqual(next(iter(state['queue'].values()))['status'], 'completed')
        state['lastProbeAt'] = 1001
        state['lastQuota'] = {**core.quota(usage(), 'codex'), 'observedAt': 1001}
        core.atomic_write(self.path, state)
        hook.handle(self.path, {'hook_event_name': 'UserPromptSubmit', 'session_id': 'unselected'},
                    usage_provider=lambda: usage(), now=1002)
        self.assertFalse(any(i['threadId'] == 'unselected' for i in core.load(self.path)['queue'].values()))

    def test_account_or_scope_failure_keeps_existing_selection_atomic(self):
        original = self.path.read_bytes()
        FakeReader.account = 'b'
        with self.assertRaises(ValueError):
            catalog.update_selection(self.path, 'selected', ['task-a'], FakeReader)
        self.assertEqual(original, self.path.read_bytes())
        FakeReader.account = 'a'
        for targets in [['controller'], ['task-a', 'task-a']]:
            with self.assertRaises(ValueError):
                catalog.update_selection(self.path, 'selected', targets, FakeReader)
            self.assertEqual(original, self.path.read_bytes())
        raw = FakeReader().thread('remote')
        raw['cwd'] = '//server/share'
        self.assertFalse(catalog.normalize(raw, core.load(self.path))['selectable'])
        raw['cwd'] = str(ROOT)
        raw['source'] = 'subAgent'
        self.assertFalse(catalog.normalize(raw, core.load(self.path))['selectable'])

    def test_disabled_monitor_remains_disabled_when_targets_are_saved(self):
        preferences.update(self.path, {'monitoringEnabled': False})
        result = catalog.update_selection(self.path, 'automatic', ['task-a'], FakeReader)
        self.assertFalse(core.load(self.path)['enabled'])
        self.assertEqual(result['pendingCount'], 0)
        self.assertEqual(core.load(self.path)['selection']['threads']['task-a']['updatedAt'], 123)

    def test_monitor_distinguishes_waiting_threshold_pending_and_unverified_schedule(self):
        state = core.load(self.path)
        state['settings']['threshold'] = 1
        original = self.path.read_bytes()
        self.assertEqual(catalog.monitor_state(None, {'remaining': 92})['phase'], 'setup_required')
        for remaining, expected in [(92, 'waiting_threshold'), (1, 'waiting_threshold'), (0, 'low_quota')]:
            result = catalog.monitor_state(state, {'remaining': remaining})
            self.assertEqual(result['phase'], expected)
            self.assertEqual(result['threshold'], 1)
            self.assertEqual(result['pendingCount'], 0)
            self.assertFalse(result['scheduled'])
        state['queue']['example'] = {'status': 'pending'}
        self.assertEqual(catalog.monitor_state(state, {'remaining': 0})['phase'], 'pending_unscheduled')
        state['automationId'], state['scheduledFor'] = 'saved-record', 2000000120
        self.assertEqual(catalog.monitor_state(state, {'remaining': 0})['phase'], 'schedule_record')
        state['enabled'] = False
        self.assertEqual(catalog.monitor_state(state, {'remaining': 0})['phase'], 'disabled')
        self.assertEqual(self.path.read_bytes(), original)

    def test_fixed_entrypoint_and_native_management_action_are_registered(self):
        tools = {tool['name']: tool for tool in settings_mcp.tools()}
        entry = tools['settings.manage']
        self.assertIn({'type': 'global'}, entry['_meta']['openai/ui']['entrypoints'])
        self.assertEqual(entry['_meta']['ui']['resourceUri'], settings_mcp.EDITOR_URI)
        action = settings_mcp.read_settings(self.path)['layout'][0]['items'][0]
        self.assertEqual(action['tool'], 'settings.editor')
        self.assertEqual(tools['settings.editor']['title'], '管理 Codex 任务续接')
        self.assertEqual(list(core.load(self.path)['queue']), [])
        resource = settings_mcp.dispatch('resources/read', {'uri': settings_mcp.EDITOR_URI}, self.path)['contents'][0]
        self.assertEqual(resource['_meta']['openai/widgetCSP']['redirect_domains'], ['https://github.com', 'https://x.com'])
        self.assertEqual(resource['_meta']['ui']['csp']['connectDomains'], [])


if __name__ == '__main__':
    unittest.main()
