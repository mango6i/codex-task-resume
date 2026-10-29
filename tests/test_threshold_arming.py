"""Only work recorded strictly below the saved threshold may arm recovery."""
import unittest
from unittest.mock import patch

import test_recovery_schedule as fixtures
from test_behavior import observation
import hook
import recovery_fast
import recovery_schedule as timers
import state as core


class ThresholdArming(unittest.TestCase):
    setUp = fixtures.RecoverySchedule.setUp
    change = fixtures.RecoverySchedule.change

    def prepare(self, remaining):
        s = self.s
        s['settings']['threshold'] = 10
        s['queue'] = {}
        s['runtime']['task'] = {'active': True, 'purpose': 'task', 'turnId': 'work-turn',
                                'startedAt': 990, 'seenAt': 1000}
        usage = observation(used=100-remaining)['usage']
        s['lastQuota'] = {**core.quota(usage, 'codex'), 'observedAt': 1000}
        s['lastLiveUsage'] = usage
        s['lastProbeAt'] = 1000
        core.atomic_write(self.path, s)
        return s

    def test_healthy_or_exact_threshold_work_does_not_arm(self):
        record = timers.native_record('existing', self.db)
        for remaining in [96, 10]:
            with self.subTest(remaining=remaining):
                s = self.prepare(remaining)
                self.assertIsNone(timers.request(s, self.path, 1000, self.db))
                with patch.object(timers, 'native_record', return_value=record):
                    result = hook.handle(self.path, {'session_id': 'task', 'turn_id': 'work-turn',
                        'hook_event_name': 'PostToolUse'}, lambda: self.fail('Use fresh cache'), 1000)
                self.assertIsNone(result)
                saved = core.load(self.path)
                self.assertFalse(saved.get('armRequest'))
                self.assertEqual(saved['queue'], {})

    def test_below_threshold_records_same_turn_before_requesting_one_timer(self):
        self.prepare(9)
        record = timers.native_record('existing', self.db)
        with patch.object(timers, 'native_record', return_value=record):
            result = hook.handle(self.path, {'session_id': 'task', 'turn_id': 'work-turn',
                'hook_event_name': 'PostToolUse'}, lambda: self.fail('Use fresh cache'), 1000)
        self.assertIsNotNone(result)
        saved = core.load(self.path)
        self.assertEqual(saved['armRequest']['reason'], 'captured_work')
        self.assertEqual(saved['armRequest']['dueAt'], 1220)
        self.assertEqual(next(iter(saved['queue'].values()))['turnId'], 'work-turn')
        self.assertIsNone(hook.handle(self.path, {'session_id': 'task', 'turn_id': 'work-turn',
            'hook_event_name': 'PostToolUse'}, lambda: self.fail('Use fresh cache'), 1001))

    def test_low_quota_without_a_captured_target_never_arms(self):
        s = self.prepare(9)
        self.assertIsNone(timers.request(s, self.path, 1000, self.db))

    def test_old_healthy_prearm_cannot_be_verified_or_preserved(self):
        s = self.prepare(96)
        s.update(scheduledFor=1220, prearmResetAt=1100)
        s['armRequest'] = {'reason': 'active_work_prearm', 'resetAt': 1100, 'dueAt': 1220}
        self.change(status='ACTIVE', next_run_at=1220000, next_run_nominal_at=1220000)
        with self.assertRaisesRegex(ValueError, 'No recorded low-quota'):
            timers.verify(s, 'existing', 1220, 1000, self.db)
        self.assertIsNone(timers.request(s, self.path, 1000, self.db))
        record = timers.native_record('existing', self.db)
        root = self.path.parent
        native = root/'automations/existing/automation.toml'
        native.parent.mkdir(parents=True)
        native.write_text('rrule = "FREQ=DAILY;BYHOUR=1;BYMINUTE=0"', encoding='utf-8')
        with patch.object(timers, 'native_record', return_value=record), \
                patch.dict('os.environ', {'CODEX_HOME': str(root)}):
            self.assertEqual(recovery_fast.finish(s, self.path, 1000)['action'], 'pause')

    def test_capture_then_refresh_keeps_original_reset_and_current_delay(self):
        s = self.prepare(9)
        core.observe(s, observation(1000, 91), 1000)
        self.assertEqual(timers.request(s, self.path, 1000, self.db)['dueAt'], 1220)
        s['lastQuota'].update(reset=2000, remaining=100, observedAt=1200)
        self.assertEqual(timers.request(s, self.path, 1200, self.db)['dueAt'], 1220)
        s['settings']['delaySeconds'] = 300
        self.assertEqual(timers.request(s, self.path, 1200, self.db)['dueAt'], 1400)

    def test_below_threshold_timer_still_checks_completion_and_exact_quota_failure(self):
        s = self.prepare(9)
        core.observe(s, observation(1000, 91), 1000)
        self.assertFalse(core.observe(s, observation(1220, 0, 2000, 'idle', 'completed'), 1220)['due'])
        self.assertEqual(next(iter(s['queue'].values()))['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
