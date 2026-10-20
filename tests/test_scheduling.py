"""Early native wakes, missed cycles and arming before quota exhaustion."""
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/quota-resume/scripts')]
import state as core
import hook
import local_catalog
import scheduling
from test_behavior import state, observation


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'state.json'
        self.db = Path(self.temp.name)/'native.db'
        self.s = state()
        self.s.update(automationId='recovery', scheduledFor=1220)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE automations (id TEXT,kind TEXT,status TEXT,target_thread_id TEXT,'
                      'next_run_at INTEGER,next_run_nominal_at INTEGER,last_run_at INTEGER)')
            c.execute('INSERT INTO automations VALUES (?,?,?,?,?,?,?)',
                      ('recovery','heartbeat','ACTIVE','controller',1300000,1520000,None))

    def change(self, **fields):
        with closing(sqlite3.connect(self.db)) as c, c:
            for k,v in fields.items():
                c.execute('UPDATE automations SET '+k+'=?', (v,))

    def test_actual_wake_must_be_after_due_even_if_nominal_looks_correct(self):
        self.change(next_run_at=1199000, next_run_nominal_at=1220000)
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, 'before recovery'):
            scheduling.verify(self.s, 'recovery', 1220, 1000, self.db)
        self.assertEqual(self.db.read_bytes(), before)
        self.change(next_run_at=1300000, next_run_nominal_at=1520000)
        self.assertEqual(scheduling.verify(self.s,'recovery',1220,1000,self.db)['wakeAt'],1300)

    def test_tomorrows_recurrence_and_wrong_target_are_rejected(self):
        self.change(next_run_at=87620000, next_run_nominal_at=87620000)
        with self.assertRaisesRegex(ValueError, 'later cycle'):
            scheduling.verify(self.s,'recovery',1220,1000,self.db)
        self.change(next_run_at=1300000, next_run_nominal_at=1520000,target_thread_id='another')
        with self.assertRaisesRegex(ValueError, 'another controller'):
            scheduling.verify(self.s,'recovery',1220,1000,self.db)

    def test_missed_early_wake_is_visible_instead_of_a_stale_schedule(self):
        self.change(last_run_at=1000000,next_run_at=87620000,next_run_nominal_at=87620000)
        status = scheduling.health(self.s,1400,self.db)
        self.assertEqual(status['status'],'missed')
        with patch.object(scheduling,'native_record',return_value=scheduling.native_record('recovery',self.db)), \
                patch.object(local_catalog.time,'time',return_value=1400):
            shown=local_catalog.monitor_state(self.s,{'remaining':100})
        self.assertEqual(shown['phase'],'recovery_missed')
        self.assertIsNone(shown['scheduleWakeAt'])
        self.assertEqual(shown['pendingCount'],0)

    def test_disconnected_paused_and_verified_status_do_not_claim_dispatch(self):
        self.assertEqual(scheduling.health(self.s,1000,self.db)['status'],'verified')
        with patch.object(scheduling,'native_record',return_value=scheduling.native_record('recovery',self.db)), \
                patch.object(local_catalog.time,'time',return_value=1000):
            self.assertEqual(local_catalog.monitor_state(self.s,{'remaining':94})['phase'],'recovery_prearmed')
        self.change(status='PAUSED')
        self.assertEqual(scheduling.health(self.s,1000,self.db)['status'],'paused')
        self.assertEqual(scheduling.health(self.s,1000,Path(self.temp.name)/'missing')['status'],'unverified')

    def test_advance_arm_requires_fresh_real_work_and_preserves_empty_queue(self):
        core.observe(self.s,observation(used=20),1000)
        self.assertIsNone(core.schedule_plan(self.s,1000))
        self.s['runtime']['task']={'active':True,'seenAt':1000,'startedAt':990,
                                  'turnId':'work-turn','purpose':'task'}
        p=core.schedule_plan(self.s,1000)
        self.assertEqual(p['dueAt'],1220)
        self.assertGreater(p['nominalAt'],p['dueAt'])
        self.assertTrue(p['prearmed'])
        self.assertEqual(self.s['queue'],{})
        self.assertIsNone(core.schedule_plan(self.s,1091))
        self.s['enabled']=False
        self.assertIsNone(core.schedule_plan(self.s,1000))

    def test_controller_or_out_of_scope_activity_never_arms_empty_monitor(self):
        core.observe(self.s,observation(used=20),1000)
        self.s['runtime']={'controller':{'active':True,'seenAt':1000,'startedAt':990,
                                        'turnId':'monitor-turn','purpose':'task'}}
        self.assertIsNone(core.schedule_plan(self.s,1000))
        self.s['runtime']['task']=dict(self.s['runtime']['controller'])
        self.s['selection']={'mode':'selected','threads':{}}
        self.assertIsNone(core.schedule_plan(self.s,1000))

    def test_hook_prearms_at_healthy_quota_without_capturing_or_sending(self):
        core.atomic_write(self.path,self.s)
        result=hook.handle(self.path,{'session_id':'task','turn_id':'work-turn',
                'hook_event_name':'UserPromptSubmit','prompt':'Finish the feature'},
                lambda:observation(used=20)['usage'],1000)
        self.assertIsNone(result)
        self.assertEqual(core.load(self.path)['armRequest']['controllerId'],'controller')
        self.assertEqual(core.load(self.path)['queue'],{})

    def test_changed_delay_and_weekly_blocking_window_are_respected(self):
        core.observe(self.s,observation(),1000)
        self.s['settings']['delaySeconds']=300
        with self.assertRaisesRegex(ValueError, 'delay changed'):
            scheduling.verify(self.s,'recovery',1220,1000,self.db)
        self.assertEqual(core.schedule_plan(self.s,1000)['dueAt'],1400)
        o=observation(now=1220,used=0,reset=2000)
        o['usage']['rateLimitsByLimitId']['codex']['secondary']['usedPercent']=100
        core.observe(self.s,o,1220)
        self.assertEqual(core.schedule_plan(self.s,1220)['dueAt'],9300)

    def test_rounding_same_window_does_not_request_another_schedule_card(self):
        core.atomic_write(self.path,self.s)
        record=scheduling.native_record('recovery',self.db)
        with patch.object(scheduling,'native_record',return_value=record):
            result=hook.handle(self.path,{'session_id':'task','turn_id':'work-turn',
                    'hook_event_name':'UserPromptSubmit','prompt':'Continue the work'},
                    lambda:observation(used=20,reset=1101)['usage'],1000)
        self.assertIsNone(result)
        self.assertEqual(core.load(self.path)['queue'],{})

    def test_all_target_hooks_are_silent_even_when_a_schedule_is_missing(self):
        for used in (0, 90, 91, 100):
            with self.subTest(used=used):
                s=state();s['settings']['threshold']=10
                core.atomic_write(self.path,s)
                payload={'session_id':'task','turn_id':'work-turn','prompt':'Finish the project'}
                for n,event in enumerate(('UserPromptSubmit','PostToolUse','Stop','SessionEnd')):
                    self.assertIsNone(hook.handle(self.path,{**payload,'hook_event_name':event},
                            lambda:observation(used=used)['usage'],1000+n))
                saved=core.load(self.path)
                self.assertEqual(bool(saved['queue']),used>90)
                self.assertTrue(saved['enabled'])

    def test_explicit_subagent_hooks_never_record_or_instruct(self):
        core.atomic_write(self.path,state())
        original=self.path.read_bytes()
        for fields in ({'source':'fork'},{'source':'subagent'},{'parent_thread_id':'parent'},
                       {'is_subagent':True}):
            self.assertIsNone(hook.handle(self.path,{'session_id':'child','turn_id':'child-turn',
                    'hook_event_name':'UserPromptSubmit','prompt':'Work',**fields},
                    lambda: self.fail('Subagent must not query quota'),1000))
        self.assertEqual(self.path.read_bytes(),original)


if __name__=='__main__':
    unittest.main()
