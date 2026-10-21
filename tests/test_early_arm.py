"""Arm with usable quota; only a later real quota interruption can be resumed."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_recovery_schedule as fixtures
from test_behavior import observation
import hook
import recovery_schedule as timers
import state as core


class EarlyArm(unittest.TestCase):
    setUp = fixtures.RecoverySchedule.setUp
    change = fixtures.RecoverySchedule.change

    def healthy(self):
        s = self.s
        s['queue'] = {}
        s['runtime']['task'] = {'active': True, 'purpose': 'task', 'turnId': 'work-turn',
                                'startedAt': 990, 'seenAt': 1000}
        q = core.quota(observation(used=20)['usage'], 'codex')
        s['lastQuota'] = {**q, 'observedAt': 1000}
        s['lastLiveUsage'] = observation(used=20)['usage']
        s['lastProbeAt'] = 1000
        core.atomic_write(self.path, s)
        return s

    def test_healthy_work_arms_without_becoming_a_candidate(self):
        s = self.healthy()
        plan = timers.request(s, self.path, 1000, self.db)
        self.assertEqual(plan['reason'], 'active_work_prearm')
        self.assertEqual(plan['dueAt'], 1220)
        self.assertEqual(s['queue'], {})
        record = timers.native_record('existing', self.db)
        with patch.object(timers, 'native_record', return_value=record):
            result = hook.handle(self.path, {'session_id':'task','turn_id':'work-turn',
                'hook_event_name':'PostToolUse'}, lambda: self.fail('Use fresh live cache'), 1000)
        self.assertIsNotNone(result)
        self.assertEqual(core.load(self.path)['armRequest']['reason'], 'active_work_prearm')
        self.assertEqual(core.load(self.path)['queue'], {})

    def test_excluded_idle_stale_setup_and_unknown_quota_never_prearm(self):
        original = self.healthy()
        for change in ['idle','stale','setup','excluded','unselected','unbound','account','quota','disabled']:
            with self.subTest(change=change):
                s = deepcopy(original)
                if change == 'idle': s['runtime']['task']['active'] = False
                if change == 'stale': s['runtime']['task']['seenAt'] = 800
                if change == 'setup': s['runtime']['task']['purpose'] = 'plugin_setup'
                if change == 'excluded': s['excluded'] = ['task']
                if change == 'unselected': s['selection'] = {'mode':'selected','threads':{}}
                if change == 'unbound': s['runtime']['task']['turnId'] = None
                if change == 'account': s['lastQuota']['accountId'] = 'other'
                if change == 'quota': s['lastQuota']['allowed'] = False
                if change == 'disabled': s['enabled'] = False
                self.assertIsNone(timers.request(s,self.path,1000,self.db))

    def test_armed_healthy_work_can_stop_at_zero_without_another_model_arm(self):
        s = self.healthy()
        s.update(scheduledFor=1220, scheduleWakeAt=1220)
        core.atomic_write(self.path,s)
        self.change(status='ACTIVE', next_run_at=1220000, next_run_nominal_at=1220000)
        result = hook.handle(self.path, {'session_id':'task','turn_id':'work-turn',
            'hook_event_name':'Stop'}, lambda: observation(1061,100)['usage'],1061)
        self.assertIsNone(result)
        s = core.load(self.path)
        item = next(iter(s['queue'].values()))
        self.assertEqual(item['turnId'], 'work-turn')
        self.assertEqual(s['scheduledFor'],1220)
        self.assertFalse(s.get('armRequest'))
        self.assertFalse(core.observe(s,observation(1219,0,2000,'notLoaded','quota_interrupted',
            turnCompletedAt=1100),1219)['due'])
        due = core.observe(s,observation(1220,0,2000,'notLoaded','quota_interrupted',
            turnCompletedAt=1100),1220)['due']
        self.assertEqual([i['threadId'] for i in due],['task'])

    def test_early_check_never_continues_healthy_or_completed_work(self):
        s = self.healthy()
        self.assertFalse(core.observe(s,observation(1220,0,2000,'idle','completed'),1220)['due'])
        self.assertEqual(s['queue'],{})

    def test_prearm_delay_edit_is_respected_without_advancing_to_new_window(self):
        s = self.healthy()
        s['armRequest'] = timers.request(s,self.path,1000,self.db)
        self.change(status='ACTIVE',next_run_at=1220000,next_run_nominal_at=1220000)
        # A refreshed quota window cannot postpone an already armed original check.
        s['lastQuota']['reset'] = 2000
        self.assertEqual(timers.verify(s,'existing',1220,1100,self.db)['wakeAt'],1220)
        s['settings']['delaySeconds'] = 300
        with self.assertRaisesRegex(ValueError,'delay changed'):
            timers.verify(s,'existing',1220,1100,self.db)

    def test_refresh_cannot_postpone_an_outstanding_early_check(self):
        s = self.healthy()
        s.update(scheduledFor=1220,prearmResetAt=1100)
        self.change(status='ACTIVE',next_run_at=1220000,next_run_nominal_at=1220000)
        s['lastQuota'].update(reset=2000,observedAt=1200)
        s['runtime']['task']['seenAt'] = 1200
        self.assertEqual(timers.request(s,self.path,1200,self.db)['dueAt'],1220)
        self.change(last_run_at=1220000,next_run_at=87620000,next_run_nominal_at=87620000)
        s['lastQuota']['observedAt'] = 1221
        s['runtime']['task']['seenAt'] = 1221
        self.assertTrue(timers.request(s,self.path,1221,self.db)['recentRecoveryWake'])


if __name__ == '__main__':
    unittest.main()
