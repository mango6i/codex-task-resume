"""Regression cases for old/idle chats being resumed after unrelated quota resets."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/quota-resume/scripts')]
import state as core
import hook
import monitor
from test_behavior import state, observation


class CaptureIdentity(unittest.TestCase):
    def captured(self):
        s = state()
        core.observe(s, observation(), 1000)
        return s

    def recovered(self, **fields):
        return observation(1220, 0, 2000, 'notLoaded', 'quota_interrupted', **fields)

    def test_selected_idle_chats_and_old_failures_are_not_captured(self):
        s = state()
        s['selection'] = {'mode': 'automatic', 'threads': {'task': {'title': 'Old chat'}}}
        o = observation(status='notLoaded', assessment='quota_interrupted', turnCompletedAt=500)
        self.assertEqual(core.observe(s, o, 1000)['pendingCount'], 0)
        self.assertEqual(s['queue'], {})

    def test_setup_or_unidentified_turn_never_becomes_candidate(self):
        for fields in [{'purpose': 'plugin_setup'}, {'purpose': 'unknown'},
                       {'turnId': None}, {'turnStartedAt': None}]:
            with self.subTest(fields=fields):
                s = state()
                self.assertEqual(core.observe(s, observation(**fields), 1000)['pendingCount'], 0)

    def test_native_observation_can_capture_work_started_before_activation(self):
        s = state()
        core.start_monitoring(s, 990)
        self.assertEqual(core.observe(s, observation(turnStartedAt=900), 1000)['pendingCount'], 1)

    def test_old_or_different_turn_failure_cannot_authorize_send(self):
        for fields in [{'turnId': 'yesterdays-turn'}, {'turnCompletedAt': 500},
                       {'turnCompletedAt': 1226}, {'turnStatus': 'completed'},
                       {'stopReason': 'model_capacity'}, {'purpose': 'plugin_setup'}]:
            with self.subTest(fields=fields):
                self.assertFalse(core.observe(self.captured(), self.recovered(**fields), 1220)['due'])

    def test_generic_incomplete_work_is_not_quota_interruption(self):
        o = self.recovered()
        o['threads'][0]['assessment'] = 'incomplete'
        self.assertFalse(core.observe(self.captured(), o, 1220)['due'])

    def test_bound_current_quota_failure_is_due_once(self):
        s = self.captured()
        due = core.observe(s, self.recovered(), 1220)['due']
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]['turnId'], 'work-turn')
        self.assertEqual(due[0]['capturePolicy'], 2)

    def test_legacy_unbound_pending_is_retired_and_send_ledger_preserved(self):
        s = self.captured()
        old = next(iter(s['queue'].values()))
        del old['turnId']
        del old['capturePolicy']
        s['queue']['sent-record'] = {'status': 'sent', 'threadId': 'other', 'token': 'kept'}
        ledger = copy.deepcopy(s['queue']['sent-record'])
        self.assertFalse(core.observe(s, self.recovered(), 1220)['due'])
        self.assertEqual(old['exclusionReason'], 'unbound_legacy_candidate')
        self.assertEqual(s['queue']['sent-record'], ledger)

    def test_epoch_window_ids_do_not_collapse_into_scientific_notation(self):
        keys = []
        for reset in [1792126237, 1792144237]:
            q = core.quota(observation(reset=reset)['usage'], 'codex')
            keys.append(q['key'])
            self.assertIn(str(reset), q['key'])
        self.assertNotEqual(*keys)

    def test_new_activation_drops_old_runtime_and_pending_only(self):
        s = self.captured()
        s['runtime'] = {'task': {'active': True}}
        s['queue']['sent'] = {'status': 'sent', 'token': 'kept'}
        core.start_monitoring(s, 1200)
        self.assertEqual(s['runtime'], {})
        self.assertEqual(s['queue']['sent']['token'], 'kept')
        self.assertFalse(core.observe(s, self.recovered(), 1220)['due'])

    def with_state(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)/'state.json'
        core.atomic_write(path, state())
        return path

    def test_connected_service_captures_real_turn_without_controller_activity(self):
        path = self.with_state()
        s = core.load(path)
        s['runtime']['task'] = {'active': True, 'seenAt': 995, 'startedAt': 990,
                                'turnId': 'running-turn', 'purpose': 'task'}
        core.atomic_write(path, s)
        monitor.tick(path, lambda: observation()['usage'], 1000)
        item = next(iter(core.load(path)['queue'].values()))
        self.assertEqual(item['turnId'], 'running-turn')
        self.assertNotIn('controller', core.load(path)['runtime'])

    def test_stale_runtime_is_not_monitoring_activity(self):
        path = self.with_state()
        s = core.load(path)
        s['runtime']['task'] = {'active': True, 'seenAt': 900, 'startedAt': 850,
                                'turnId': 'old-turn', 'purpose': 'task'}
        core.atomic_write(path, s)
        monitor.tick(path, lambda: observation()['usage'], 1000)
        self.assertEqual(core.load(path)['queue'], {})

    def test_setup_prompt_and_stop_do_not_fabricate_running_task(self):
        path = self.with_state()
        provider = lambda: observation()['usage']
        p = {'session_id': 'task', 'turn_id': 'setup-turn',
             'hook_event_name': 'UserPromptSubmit', 'prompt': '使用 $quota-resume 初始化并启用'}
        hook.handle(path, p, provider, 1000)
        hook.handle(path, {**p, 'hook_event_name': 'Stop'}, provider, 1001)
        self.assertEqual(core.load(path)['queue'], {})

    def test_late_async_tool_callback_cannot_resurrect_stopped_turn(self):
        path = self.with_state()
        provider = lambda: observation(used=20)['usage']
        p = {'session_id': 'task', 'turn_id': 'work-turn',
             'hook_event_name': 'UserPromptSubmit', 'prompt': 'Finish the feature'}
        hook.handle(path, p, provider, 1000)
        hook.handle(path, {**p, 'hook_event_name': 'Stop'}, provider, 1001)
        hook.handle(path, {**p, 'hook_event_name': 'PostToolUse'}, provider, 1100)
        self.assertFalse(core.load(path)['runtime']['task']['active'])

    def test_new_user_turn_supersedes_old_pending_work(self):
        path = self.with_state()
        s = self.captured()
        core.atomic_write(path, s)
        hook.handle(path, {'session_id': 'task', 'turn_id': 'new-turn',
            'hook_event_name': 'UserPromptSubmit', 'prompt': 'Different request'},
            lambda: observation(used=20)['usage'], 1050)
        old = next(iter(core.load(path)['queue'].values()))
        self.assertEqual(old['exclusionReason'], 'superseded_turn')

    def test_new_running_turn_in_same_window_gets_its_own_identity(self):
        s = self.captured()
        core.observe(s, observation(now=1005, turnId='new-turn', turnStartedAt=1004), 1005)
        self.assertEqual(len(s['queue']), 2)
        pending = [v for v in s['queue'].values() if v['status'] == 'pending']
        self.assertEqual([v['turnId'] for v in pending], ['new-turn'])


if __name__ == '__main__':
    unittest.main()
