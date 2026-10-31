"""Explicit account-button migration with durable backup and no dispatch."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/quota-resume/scripts')]
import account_binding
import local_catalog
import settings_mcp
import state as core
from test_behavior import state, observation


class AccountBinding(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)/'state.json'
        self.saved = state()
        core.observe(self.saved, observation(), 1000)
        self.saved['queue']['uncertain'] = {'status': 'uncertain', 'token': 'never-replay'}
        self.saved['queue']['sent'] = {'status': 'sent', 'token': 'confirmed-send'}
        self.saved['settings']['message'] = '  自定义\n继续任务  '
        self.saved['selection'] = {'mode': 'selected', 'threads': {'task': {'title': 'Local project'}}}
        self.saved['runtime']['task'] = {'active': True, 'turnId': 'work-turn', 'seenAt': 1000}
        self.saved.update(scheduledFor=1220, scheduleWakeAt=1220, automationId='owned-timer')
        core.atomic_write(self.path, self.saved)
        self.usage = observation()['usage']
        self.usage['accountId'] = 'new-account'

    def rebind(self):
        with patch.object(local_catalog, 'monitor_state', return_value={'enabled': False}):
            return account_binding.rebind(self.path, True, lambda: copy.deepcopy(self.usage), 1001)

    def test_button_preserves_settings_scope_ledger_and_backs_up_old_candidates(self):
        result = self.rebind()
        saved = core.load(self.path)
        self.assertEqual(saved['accountId'], 'new-account')
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['runtime'], {})
        self.assertIsNone(saved['scheduledFor'])
        self.assertIsNone(saved['armRequest'])
        for key in ['controllerId', 'settings', 'selection', 'excluded', 'automationId']:
            self.assertEqual(saved.get(key), self.saved.get(key))
        for key in ['uncertain', 'sent']:
            self.assertEqual(saved['queue'][key], self.saved['queue'][key])
        pending = next(v for k, v in saved['queue'].items() if k not in ['uncertain', 'sent'])
        self.assertEqual(pending['status'], 'excluded')
        self.assertEqual(pending['exclusionReason'], 'account_rebound')
        backups = list(self.path.parent.glob('state.before-account-rebind-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8')), self.saved)
        self.assertTrue(result['changed'])
        self.assertFalse(result['sent'])
        self.assertFalse(result['values']['monitoringEnabled'])
        self.assertEqual(saved['lastQuota']['accountId'], 'new-account')

    def test_same_account_click_preserves_monitor_and_does_not_create_a_backup(self):
        self.usage['accountId'] = self.saved['accountId']
        original = self.path.read_bytes()
        result = self.rebind()
        self.assertFalse(result['changed'])
        self.assertTrue(result['values']['monitoringEnabled'])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('state.before-account-rebind-*.json')), [])

    def test_failed_or_switching_account_read_cannot_change_binding(self):
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            account_binding.rebind(self.path, True, lambda: {}, 1001)
        sources = iter([self.usage, observation()['usage']])
        with self.assertRaisesRegex(ValueError, '正在切换'):
            account_binding.rebind(self.path, True, lambda: next(sources), 1001)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('state.before-account-rebind-*.json')), [])

    def test_confirmation_and_existing_controller_are_required(self):
        for confirmed in [False, None, 'true', 1]:
            with self.assertRaises(ValueError):
                account_binding.rebind(self.path, confirmed, lambda: self.fail('No query'), 1001)
        with self.assertRaisesRegex(ValueError, '初始化'):
            account_binding.rebind(self.path.with_name('new.json'), True, lambda: self.fail('No query'), 1001)

    def test_backup_or_atomic_write_failure_never_replaces_original_state(self):
        original = self.path.read_bytes()
        with patch.object(account_binding.os, 'fsync', side_effect=OSError('backup unavailable')):
            with self.assertRaises(OSError):
                self.rebind()
        self.assertEqual(self.path.read_bytes(), original)
        with patch.object(core, 'atomic_write', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                self.rebind()
        self.assertEqual(self.path.read_bytes(), original)

    def test_mcp_tool_is_button_only_and_rejects_arbitrary_account_input(self):
        tools = {t['name']: t for t in settings_mcp.tools()}
        self.assertEqual(tools['account.rebind']['_meta']['ui']['visibility'], ['app'])
        with patch.object(account_binding, 'rebind', return_value={'changed': False, 'sent': False}) as migration:
            result = settings_mcp.dispatch_raw('tools/call', {
                'name': 'account.rebind', 'arguments': {'confirmed': True}}, self.path)
            self.assertFalse(result.get('isError'))
            migration.assert_called_once_with(self.path, True)
            result = settings_mcp.dispatch_raw('tools/call', {
                'name': 'account.rebind', 'arguments': {'confirmed': True, 'accountId': 'untrusted'}}, self.path)
            self.assertTrue(result['isError'])


if __name__ == '__main__':
    unittest.main()
