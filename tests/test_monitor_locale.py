import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/quota-resume/scripts')]
import i18n
import local_catalog
import monitor
import preferences
import settings_mcp
import state as core
from test_catalog import FakeReader, usage


class ConnectedMonitor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'state.json'
        import subprocess
        result = subprocess.run([sys.executable, str(ROOT/'skills/quota-resume/scripts/state.py'),
            '--state', str(self.path), 'init', '--account', 'a', '--controller', 'controller', '--authorized'],
            capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = core.load(self.path)
        state['selection'] = {'mode': 'selected', 'threads': {'task-a': {'title': '继续任务', 'project': str(ROOT)}}}
        core.atomic_write(self.path, state)

    def tearDown(self):
        self.temp.cleanup()

    def test_idle_service_captures_selected_targets_without_model_or_task_event(self):
        original = preferences.values(self.path)
        monitor.tick(self.path, lambda: usage(used=100), now=1000)
        state = core.load(self.path)
        self.assertEqual(state['runtime'], {})
        self.assertEqual(len(state['queue']), 1)
        item = next(iter(state['queue'].values()))
        self.assertEqual(item['dueAt'], 2000000120)
        self.assertEqual(item['status'], 'pending')
        self.assertIsNone(state.get('automationId'))
        self.assertFalse(state.get('sends'))
        self.assertEqual(state['lastProbeSuccessAt'], 1000)
        self.assertEqual(preferences.values(self.path), original)
        calls = []
        monitor.tick(self.path, lambda: calls.append(True), now=1005)
        self.assertEqual(calls, [])

    def test_disable_before_query_finishes_prevents_capture(self):
        def query():
            preferences.update(self.path, {'monitoringEnabled': False})
            return usage()
        monitor.tick(self.path, query, now=1000)
        self.assertFalse(core.load(self.path)['enabled'])
        self.assertEqual(core.load(self.path)['queue'], {})
        calls = []
        monitor.tick(self.path, lambda: calls.append(True), now=1100)
        self.assertEqual(calls, [])

    def test_unknown_quota_does_not_capture_and_account_change_disables(self):
        def failed():
            raise OSError('offline')
        monitor.tick(self.path, failed, now=1000)
        state = core.load(self.path)
        self.assertEqual(state['queue'], {})
        self.assertTrue(state['lastProbeError'])
        monitor.tick(self.path, lambda: usage(account='other'), now=1100)
        self.assertFalse(core.load(self.path)['enabled'])
        self.assertEqual(core.load(self.path)['queue'], {})

    def test_quota_read_is_read_only_and_uses_actual_reset(self):
        original = self.path.read_bytes()
        FakeReader.account = 'a'
        FakeReader.calls = []
        result = local_catalog.read_quota(self.path, FakeReader)
        self.assertEqual(result['quota']['resetAt'], 2000000000)
        self.assertEqual(result['monitor']['selectedCount'], 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual([name for name, _ in FakeReader.calls], ['account/rateLimits/read'])

    def test_quota_retry_and_offline_history_never_refresh_capture_state(self):
        class Flaky(FakeReader):
            failures = 1
            def call(self, method, params):
                if self.failures:
                    type(self).failures -= 1
                    raise OSError('network')
                return super().call(method, params)
        with patch.object(local_catalog.time, 'sleep'):
            result = local_catalog.read_quota(self.path, Flaky)
        self.assertFalse(result['stale'])
        monitor.tick(self.path, lambda: usage(used=60), now=1000)
        original = self.path.read_bytes()
        class Offline(FakeReader):
            def call(self, method, params):
                raise OSError('network')
        with patch.object(local_catalog.time, 'time', return_value=1005), patch.object(local_catalog.time, 'sleep'):
            result = local_catalog.read_quota(self.path, Offline)
        self.assertTrue(result['stale'])
        self.assertEqual(result['quota']['readAt'], 1000)
        self.assertEqual(result['quota']['remaining'], 40)
        self.assertEqual(self.path.read_bytes(), original)
        with patch.object(local_catalog.time, 'time', return_value=1400), patch.object(local_catalog.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, '暂时无法连接额度服务'):
                local_catalog.read_quota(self.path, Offline)


class Locale(unittest.TestCase):
    def test_chinese_variants_and_english_fallback(self):
        for value in ['zh-CN', 'zh_TW', 'zh-Hant', 'ZH-hk']:
            self.assertEqual(i18n.language(value), 'zh-CN')
        for value in ['en-US', 'de-DE', 'ja-JP', '', None]:
            if value is not None:
                self.assertEqual(i18n.language(value), 'en')

    def test_native_metadata_translates_but_saved_message_and_ids_do_not(self):
        with tempfile.TemporaryDirectory() as td, patch.object(i18n, 'language', return_value='en'):
            path = Path(td)/'state.json'
            message = '  继续任务\nKeep the exact text.  '
            preferences.update(path, {'message': message})
            result = settings_mcp.dispatch('tools/call', {'name': 'settings.read', 'arguments': {}}, path)
            self.assertEqual(result['structuredContent']['values']['message'], message)
            self.assertEqual(result['structuredContent']['schema']['properties']['message']['title'], 'Continuation prompt')
            self.assertEqual(result['_meta']['task-resume/locale'], 'en')
            tools = settings_mcp.dispatch('tools/list', {}, path)['tools']
            editor = next(x for x in tools if x['name'] == 'settings.editor')
            self.assertEqual(editor['title'], 'Manage Codex Task Resume')

    def test_resources_embed_local_translations_without_network_or_user_data(self):
        for lang in ['en', 'zh-CN']:
            text = i18n.render('settings.html', lang)
            self.assertNotIn('/*__TASK_RESUME_I18N__*/', text)
            self.assertIn('"language": "'+lang+'"', text)
            self.assertNotIn('<script src=', text)
            self.assertNotIn('19*3600', text)
            self.assertIn('live-reset', text)

    def test_installed_details_localize_without_changing_identity_or_author(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'plugin.json'
            original = (ROOT/'.codex-plugin/plugin.json').read_bytes()
            path.write_bytes(original)
            i18n.localize_manifest(path, 'en')
            result = json.loads(path.read_text(encoding='utf-8'))
            source = json.loads(original)
            self.assertEqual(result['name'], source['name'])
            self.assertEqual(result['version'], '1.0.0')
            self.assertEqual(result['author'], source['author'])
            self.assertEqual(result['homepage'], 'https://github.com/mango6i/codex-task-resume')
            self.assertEqual(result['interface']['displayName'], 'Codex Task Resume')
            self.assertIn('remains unverified', result['interface']['longDescription'])


if __name__ == '__main__':
    unittest.main()
