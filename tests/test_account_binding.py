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
        self.assertTrue(saved['enabled'])
        self.assertTrue(saved['monitoringRequested'])
        self.assertEqual(saved['runtime']['task']['turnId'], 'work-turn')
        self.assertEqual(saved['runtime']['task']['seenAt'], 1000)
        self.assertEqual(saved['runtime']['task']['accountId'], 'new-account')
        self.assertIsNone(saved['scheduledFor'])
        self.assertIsNone(saved['armRequest'])
        for key in ['controllerId', 'settings', 'selection', 'excluded']:
            self.assertEqual(saved.get(key), self.saved.get(key))
        self.assertIsNone(saved['automationId'])
        self.assertEqual(saved['retiredAutomations'][0]['id'], 'owned-timer')
        for key in ['uncertain', 'sent']:
            self.assertEqual(saved['queue'][key], self.saved['queue'][key])
        old = next(v for v in saved['queue'].values() if v.get('exclusionReason') == 'account_rebound')
        self.assertEqual(old['status'], 'excluded')
        pending = [v for v in saved['queue'].values() if v['status'] == 'pending']
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['accountId'], 'new-account')
        self.assertEqual(pending[0]['resetAt'], 1100)
        self.assertEqual(pending[0]['dueAt'], 1220)
        backups = list(self.path.parent.glob('state.before-account-rebind-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8')), self.saved)
        self.assertTrue(result['changed'])
        self.assertFalse(result['sent'])
        self.assertTrue(result['values']['monitoringEnabled'])
        self.assertEqual(saved['lastQuota']['accountId'], 'new-account')

    def test_user_disabled_switch_stays_off_but_account_suspension_restores_intent(self):
        self.saved.update(enabled=False, monitoringRequested=False)
        core.atomic_write(self.path, self.saved)
        self.assertFalse(self.rebind()['values']['monitoringEnabled'])
        self.saved.pop('monitoringRequested')
        self.saved['authorization'] = {'source': 'explicit settings switch'}
        self.saved['events'].append({'type': 'account_changed', 'at': 1000})
        core.atomic_write(self.path, self.saved)
        self.assertTrue(self.rebind()['values']['monitoringEnabled'])

    def test_binding_again_and_back_uses_each_accounts_fresh_reset(self):
        self.usage['rateLimitsByLimitId']['codex']['primary']['resetsAt'] = 2000
        self.rebind()
        saved = core.load(self.path)
        pending = next(v for v in saved['queue'].values() if v['status'] == 'pending')
        self.assertEqual(pending['resetAt'], 2000)
        self.assertEqual(pending['dueAt'], 2120)
        self.usage['accountId'] = self.saved['accountId']
        self.usage['rateLimitsByLimitId']['codex']['primary']['resetsAt'] = 3000
        self.rebind()
        saved = core.load(self.path)
        pending = next(v for v in saved['queue'].values() if v['status'] == 'pending')
        self.assertEqual(pending['resetAt'], 3000)
        self.assertEqual(pending['dueAt'], 3120)
        self.assertEqual(pending['accountId'], self.saved['accountId'])
        self.assertEqual(saved['queue']['sent'], self.saved['queue']['sent'])
        self.assertEqual(saved['queue']['uncertain'], self.saved['queue']['uncertain'])

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
