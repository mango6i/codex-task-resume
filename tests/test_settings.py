import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/quota-resume/scripts'))
sys.path.insert(0, str(ROOT/'server'))
import state as core
import preferences
import settings_mcp


class Settings(unittest.TestCase):
    def test_modal_open_is_read_only_and_only_registered_resources_are_readable(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            for name in ['settings.editor', 'settings.open', 'settings.manage']:
                r = settings_mcp.dispatch('tools/call', {'name': name, 'arguments': {}}, path)
                self.assertFalse(r.get('isError'))
            self.assertEqual(list(Path(td).iterdir()), [])
            resources = settings_mcp.dispatch('resources/list', {}, path)['resources']
            for resource in resources:
                result = settings_mcp.dispatch('resources/read', {'uri': resource['uri']}, path)
                self.assertEqual(result['contents'][0]['mimeType'], 'text/html;profile=mcp-app')
                self.assertIn('<!doctype html>', result['contents'][0]['text'])
            with self.assertRaises(ValueError):
                settings_mcp.dispatch('resources/read', {'uri': '../../state.json'}, path)
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_native_read_has_schema_values_and_no_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            r = settings_mcp.read_settings(path)
            self.assertEqual(set(r['schema']['properties']), set(r['values']))
            self.assertEqual(r['values']['message'], '继续任务')
            self.assertEqual(r['values']['announcementKeyword'], 'reset')
            self.assertFalse(r['values']['monitoringEnabled'])
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_cached_old_entrypoints_keep_rendering_the_current_form(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'state.json'
            for suffix in ('20261016-bilingual','20261016-compact-header','20261018-announcements',
                           '20261018-balanced-switches','20261018-keyword','20261018-catalog',
                           '20261019-recovery'):
                for stem,filename in [('settings-studio-','settings.html'),
                                      ('settings-launcher-studio-','launcher.html')]:
                    uri='ui://task-resume/'+stem+suffix+'.html'
                    r=settings_mcp.dispatch('resources/read',{'uri':uri},path)['contents'][0]
                    self.assertEqual(r['uri'],uri)
                    self.assertIn('<!doctype html>',r['text'])
                    self.assertEqual(r['_meta']['ui']['prefersBorder'],filename!='settings.html')
            self.assertEqual(settings_mcp.EDITOR_URI,'ui://task-resume/settings.html')
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_slow_quota_read_does_not_block_local_chat_inventory_or_settings(self):
        with tempfile.TemporaryDirectory() as td:
            bootstrap = '''
import sys,time
sys.path[:0]=sys.argv[1:3]
sys.argv=[sys.argv[0],'--state',sys.argv[3]]
import local_catalog,settings_mcp
def slow_quota(path):
    time.sleep(.5)
    return {'slowQuotaFinished':True}
local_catalog.read_quota=slow_quota
local_catalog.list_threads=lambda path,cursor=None:{'threads':[],'inventoryFinished':True}
settings_mcp.main(start_monitor=False)
'''
            requests = [{'id': 1, 'method': 'tools/call', 'params': {'name': 'quota.read', 'arguments': {}}},
                        {'id': 2, 'method': 'tools/call', 'params': {'name': 'tasks.list', 'arguments': {}}},
                        {'id': 3, 'method': 'tools/call', 'params': {'name': 'settings.read', 'arguments': {}}}]
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', bootstrap, str(ROOT/'server'),
                str(ROOT/'skills/quota-resume/scripts'), str(Path(td)/'state.json')],
                input='\n'.join(json.dumps(x) for x in requests)+'\n', capture_output=True,
                text=True, encoding='utf-8', timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            ids = [item['id'] for item in replies]
            self.assertEqual(set(ids), {1, 2, 3})
            self.assertLess(ids.index(2), ids.index(1))
            self.assertLess(ids.index(3), ids.index(1))

    def test_save_before_setup_and_import_without_activation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            message = '  继续任务\n请保持原始范围。  '
            announcement = '  项目检查\n保留我的原文。  '
            preferences.update(path, {'threshold': 7, 'delayMinutes': 1.5, 'message': message,
                                      'announcementMessage': announcement, 'announcementCheckMinutes': 3,
                                      'announcementKeyword': ' Global reset '})
            self.assertFalse(path.exists())
            cmd = [sys.executable, str(ROOT/'skills/quota-resume/scripts/state.py'), '--state', str(path),
                'init', '--account', 'a', '--controller', 'controller', '--authorized', '--preserve-enabled-state']
            r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(r.returncode, 0, r.stderr)
            s = core.load(path)
            self.assertFalse(s['enabled'])
            self.assertEqual(s['settings']['threshold'], 7)
            self.assertEqual(s['settings']['delaySeconds'], 90)
            self.assertEqual(s['settings']['message'], message)
            self.assertEqual(s['settings']['announcementMessage'], announcement)
            self.assertEqual(s['settings']['announcementCheckMinutes'], 3)
            self.assertEqual(s['settings']['announcementKeyword'], 'Global reset')
            self.assertFalse(s['settings'].get('announcementEnabled', False))
            self.assertNotIn('authorization', s)
            preferences.update(path, {'message': '另一个内容'})
            self.assertFalse(core.load(path)['enabled'])
            preferences.update(path, {'monitoringEnabled': True})
            self.assertTrue(core.load(path)['enabled'])
            self.assertEqual(core.load(path)['authorization']['source'], 'explicit settings switch')
            preferences.update(path, {'monitoringEnabled': False})
            self.assertFalse(core.load(path)['enabled'])

    def test_uninitialized_enable_rejected_and_invalid_batch_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            with self.assertRaises(ValueError):
                preferences.update(path, {'monitoringEnabled': True})
            preferences.update(path, {'threshold': 5})
            old = preferences.preference_path(path).read_bytes()
            for patch in [{'threshold': 100}, {'message': '   '}, {'checkSeconds': 30.5},
                          {'message': '新值', 'threshold': -1}, {'monitoringEnabled': 'yes'},
                          {'announcementKeyword': '   '}, {'announcementKeyword': 123},
                          {'announcementKeyword': 'x'*101}, {'announcementKeyword': 'reset\nlimit'}]:
                with self.subTest(patch=patch), self.assertRaises(ValueError):
                    preferences.update(path, patch)
                self.assertEqual(old, preferences.preference_path(path).read_bytes())

    def test_concurrent_partial_updates_do_not_lose_other_fields(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(preferences.update, path, p)
                           for p in [{'threshold': 9}, {'message': '自定义\n继续任务'}]]
                for f in futures:
                    f.result()
            r = preferences.values(path)
            self.assertEqual(r['threshold'], 9)
            self.assertEqual(r['message'], '自定义\n继续任务')
            self.assertEqual(r['delayMinutes'], 2)
            self.assertFalse(r['monitoringEnabled'])

    def test_mcp_wire_capability_and_partial_update(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            requests = [
                {'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                {'method': 'notifications/initialized'},
                {'id': 2, 'method': 'tools/list'},
                {'id': 3, 'method': 'tools/call', 'params': {'name': 'settings.update', 'arguments': {'set': {'message': '继续\n完成验证'}}}},
                {'id': 4, 'method': 'tools/call', 'params': {'name': 'settings.read', 'arguments': {}}}
            ]
            r = subprocess.run([sys.executable, str(ROOT/'server/settings_mcp.py'), '--state', str(path)],
                input='\n'.join(json.dumps(x) for x in requests)+'\n',
                capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            replies = [json.loads(x) for x in r.stdout.splitlines()]
            self.assertEqual(len(replies), 4)
            cap = replies[0]['result']['capabilities']['experimental']['openai/settings']
            self.assertEqual(cap['readTool'], 'settings.read')
            self.assertIn('outputSchema', replies[1]['result']['tools'][0])
            values = replies[-1]['result']['structuredContent']['values']
            self.assertEqual(values['message'], '继续\n完成验证')
            self.assertFalse(values['monitoringEnabled'])


if __name__ == '__main__':
    unittest.main()
