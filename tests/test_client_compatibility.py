"""Desktop hook lifecycle, cached UI resources and durable Windows writes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/quota-resume/scripts')]
import hook
import local_catalog
import recovery_schedule
import settings_mcp
import state as core
from test_behavior import state, observation


class ClientCompatibility(unittest.TestCase):
    def test_windows_system_proxy_is_passed_to_child_without_changing_parent(self):
        with patch.dict(os.environ, {'PATH': 'fixture'}, clear=True), patch.object(hook.os, 'name', 'nt'), \
                patch.object(hook.urllib.request, 'getproxies', return_value={'https': 'http://127.0.0.1:7897', 'no': 'localhost'}):
            child = hook.quota_environment()
            self.assertEqual(child['HTTPS_PROXY'], 'http://127.0.0.1:7897')
            self.assertEqual(child['NO_PROXY'], 'localhost')
            self.assertNotIn('HTTPS_PROXY', os.environ)

    def test_explicit_proxy_overrides_including_empty_values_are_preserved(self):
        with patch.dict(os.environ, {'https_proxy': '', 'NO_PROXY': 'private.local'}, clear=True), \
                patch.object(hook.os, 'name', 'nt'), \
                patch.object(hook.urllib.request, 'getproxies', return_value={'https': 'http://system:7897', 'no': 'localhost'}):
            self.assertEqual(hook.quota_environment(), dict(os.environ))

    def test_non_windows_proxy_environment_is_unchanged(self):
        with patch.object(hook.os, 'name', 'posix'), patch.object(hook.urllib.request, 'getproxies') as provider:
            self.assertEqual(hook.quota_environment(), dict(os.environ))
            provider.assert_not_called()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'state.json'
        self.s = state()

    def save(self):
        core.atomic_write(self.path, self.s)

    def event(self, name, **fields):
        return {'session_id': 'task', 'turn_id': 'work-turn',
                'hook_event_name': name, 'prompt': 'Finish the feature', **fields}

    def test_stop_never_creates_a_continuation_or_invalidates_captured_turn(self):
        core.observe(self.s, observation(), 1000)
        self.s['runtime']['task'] = {'active': True, 'seenAt': 1000, 'startedAt': 900,
                                   'turnId': 'work-turn', 'purpose': 'task'}
        self.save()
        result = hook.handle(self.path, self.event('Stop'), lambda: observation()['usage'], 1001)
        self.assertIsNone(result)
        saved = core.load(self.path)
        self.assertFalse(saved['runtime']['task']['active'])
        self.assertEqual(next(iter(saved['queue'].values()))['status'], 'pending')
        self.assertFalse(saved.get('armRequest'))
        # The same native failed turn still qualifies after genuine recovery.
        due = core.observe(saved, observation(1220, 0, 2000, 'idle', 'quota_interrupted'), 1220)['due']
        self.assertEqual([x['turnId'] for x in due], ['work-turn'])

    def test_first_low_quota_seen_at_stop_records_work_but_does_not_spawn_a_turn(self):
        self.save()
        hook.handle(self.path, self.event('UserPromptSubmit'), lambda: observation(used=20)['usage'], 900)
        result = hook.handle(self.path, self.event('Stop'), lambda: observation()['usage'], 1000)
        self.assertIsNone(result)
        saved = core.load(self.path)
        self.assertEqual(next(iter(saved['queue'].values()))['turnId'], 'work-turn')
        self.assertFalse(saved['runtime']['task']['active'])
        self.assertIsNone(saved.get('scheduledFor'))

    def test_unrelated_healthy_project_never_receives_another_turns_arm_prompt(self):
        core.observe(self.s, observation(), 1000)
        self.save()
        result = hook.handle(self.path, self.event('UserPromptSubmit', session_id='other', turn_id='other-turn'),
                             lambda: observation(used=20)['usage'], 1001)
        self.assertIsNone(result)
        self.assertFalse(core.load(self.path).get('armRequest'))

    def test_interrupt_persists_pause_without_waiting_for_quota(self):
        core.observe(self.s, observation(), 1000)
        self.s['runtime']['task'] = {'active': True, 'seenAt': 1000, 'startedAt': 900,
                                   'turnId': 'work-turn', 'purpose': 'task'}
        self.save()
        hook.handle(self.path, self.event('Interrupt'), lambda: self.fail('No network during Interrupt'), 1001)
        self.assertEqual(next(iter(core.load(self.path)['queue'].values()))['status'], 'user_paused')

    def test_structured_subagent_source_is_ignored_without_mutating_parent(self):
        self.save()
        original = self.path.read_bytes()
        self.assertIsNone(hook.handle(self.path, self.event('UserPromptSubmit', source={'subAgent': {'thread_spawn': {}}}),
                                      lambda: self.fail('Subagent cannot probe parent'), 1000))
        self.assertEqual(self.path.read_bytes(), original)

    def test_all_known_cached_templates_remain_readable(self):
        for uri, filename in settings_mcp.UI_FILES.items():
            result = settings_mcp.dispatch_raw('resources/read', {'uri': uri}, self.path)['contents'][0]
            self.assertIn('<!doctype html>', result['text'].lower())
            self.assertEqual(result['_meta']['ui']['prefersBorder'], filename != 'settings.html')
        with self.assertRaisesRegex(ValueError, 'Resource not found'):
            settings_mcp.dispatch_raw('resources/read', {'uri': 'ui://task-resume/../../state.json'}, self.path)

    def test_temporary_windows_sharing_violation_preserves_atomic_ledger(self):
        self.save()
        replace = os.replace
        error = PermissionError('sharing violation')
        error.winerror = 32
        attempts = []
        def retry(source, target):
            attempts.append(1)
            if len(attempts) < 3:
                raise error
            replace(source, target)
        self.s['queue']['sent'] = {'status': 'sent', 'token': 'do-not-replay'}
        with patch.object(core.os, 'replace', side_effect=retry), patch.object(core.time, 'sleep'):
            self.save()
        self.assertEqual(core.load(self.path)['queue']['sent']['token'], 'do-not-replay')
        original = self.path.read_bytes()
        with patch.object(core.os, 'replace', side_effect=error), patch.object(core.time, 'sleep'):
            with self.assertRaises(PermissionError):
                core.atomic_write(self.path, {'invalid': 'replacement'})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_expired_catalog_budget_never_sends_another_request(self):
        reader = local_catalog.Reader()
        reader.deadline = 0
        with self.assertRaisesRegex(ValueError, '超时'):
            reader.call('thread/read', {'threadId': 'task'})

    def test_ambiguous_native_store_is_not_guessed(self):
        root = self.path.parent/'sqlite'
        root.mkdir()
        for name in ('codex.db', 'codex-dev.db'):
            (root/name).touch()
        with patch.dict(os.environ, {'CODEX_HOME': str(self.path.parent)}):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                recovery_schedule.native_record('existing')

    def test_corrupt_saved_switch_or_scope_cannot_expand_authorization(self):
        for field, value in [('enabled', 'false'), ('selection', {'mode': 'all', 'threads': {}}),
                             ('excluded', 'task'), ('settings', {**self.s['settings'], 'delaySeconds': 0})]:
            with self.subTest(field=field):
                core.atomic_write(self.path, {**self.s, field: value})
                original = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    core.load(self.path)
                self.assertEqual(self.path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
