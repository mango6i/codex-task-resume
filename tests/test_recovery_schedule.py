"""Regressions from a viewed-but-paused timer and rejected update arguments."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/quota-resume/scripts')]
import hook
import local_catalog
import recovery_schedule as timers
import state as core
from test_behavior import state, observation


class RecoverySchedule(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'state.json'
        self.db = Path(self.temp.name)/'sqlite/codex-dev.db'
        self.db.parent.mkdir()
        self.s = state()
        core.observe(self.s, observation(), 1000)
        self.s['automationId'] = 'existing'
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE automations (id TEXT,name TEXT,prompt TEXT,kind TEXT,status TEXT,'
                      'target_thread_id TEXT,notification_policy TEXT,next_run_at INTEGER,next_run_nominal_at INTEGER,last_run_at INTEGER)')
            c.execute('INSERT INTO automations VALUES (?,?,?,?,?,?,?,?,?,?)',
                      ('existing','Existing monitor','Preserve saved prompt','heartbeat','PAUSED',
                       'controller','failed_runs_only',None,None,None))

    def change(self, **values):
        with closing(sqlite3.connect(self.db)) as c, c:
            for key, value in values.items():
                c.execute('UPDATE automations SET '+key+'=?', (value,))

    def test_viewed_paused_timer_cannot_be_verified_or_shown_scheduled(self):
        self.s['scheduledFor'] = 1220
        with self.assertRaisesRegex(ValueError, 'paused'):
            timers.verify(self.s, 'existing', 1220, 1000, self.db)
        record = timers.native_record('existing', self.db)
        with patch.object(timers, 'native_record', return_value=record), patch.object(local_catalog.time, 'time', return_value=1000):
            shown = local_catalog.monitor_state(self.s, {'remaining': 1})
        self.assertFalse(shown['scheduled'])
        self.assertEqual(shown['phase'], 'pending_unscheduled')
        self.assertIsNone(shown['scheduledFor'])

    def test_prepared_update_uses_supported_arguments_and_preserves_preferences(self):
        plan = timers.request(self.s, self.path, 1000, self.db)
        args = plan['arguments']
        self.assertEqual(args['mode'], 'update')
        self.assertEqual(args['id'], 'existing')
        self.assertEqual(args['targetThreadId'], 'controller')
        self.assertEqual(args['status'], 'ACTIVE')
        self.assertEqual(args['prompt'], 'Preserve saved prompt')
        self.assertEqual(args['notificationPolicy'], 'failed_runs_only')
        self.assertFalse({'executionEnvironment','destination','model','thinking'} & args.keys())
        self.assertEqual(plan['dueAt'], 1220)
        self.assertEqual(plan['nominalAt'], plan['dueAt'])

    def test_native_timing_rejects_early_or_tomorrow_and_accepts_same_cycle(self):
        for wake, nominal in [(1219,1520),(87620,87920)]:
            self.change(status='ACTIVE',next_run_at=wake*1000,next_run_nominal_at=nominal*1000)
            with self.assertRaises(ValueError):
                timers.verify(self.s,'existing',1220,1000,self.db)
        self.change(status='ACTIVE',next_run_at=1250000,next_run_nominal_at=1520000)
        self.assertEqual(timers.verify(self.s,'existing',1220,1000,self.db)['wakeAt'],1250)
        self.s['settings']['delaySeconds'] = 300
        with self.assertRaisesRegex(ValueError,'delay changed'):
            timers.verify(self.s,'existing',1220,1000,self.db)

    def test_generated_prompts_migrate_to_native_fast_runner_but_custom_prompt_is_preserved(self):
        old = ('Use the quota-resume skill at old/SKILL.md and its orchestration reference '
               'for ONE recovery check.')
        for prompt in [None, old, 'Use $quota-resume at old/SKILL.md', timers.FAST_MARKER+' old code']:
            with self.subTest(prompt=prompt):
                self.change(prompt=prompt)
                value = timers.request(self.s,self.path,1000,self.db)['arguments']['prompt']
                self.assertTrue(value.startswith(timers.FAST_MARKER))
                self.assertIn('recovery_fast.py',value)
                self.assertIn('new Function',value)
                self.assertNotIn('read_thread',value)
        self.change(prompt='My custom timer instructions')
        self.assertEqual(timers.request(self.s,self.path,1000,self.db)['arguments']['prompt'],
                         'My custom timer instructions')

    def test_out_of_scope_candidates_cannot_keep_an_empty_check_alive(self):
        self.s['excluded'] = ['task']
        self.assertIsNone(timers.request(self.s,self.path,1000,self.db))
        self.s['excluded'] = []
        self.s['selection'] = {'mode':'selected','threads':{}}
        self.assertIsNone(timers.request(self.s,self.path,1000,self.db))

    def test_recovery_plan_keeps_original_reset_after_live_quota_refresh(self):
        core.observe(self.s, observation(1220,0,2000,'notLoaded','unknown'),1220)
        plan = timers.request(self.s,self.path,1220,self.db)
        self.assertEqual(plan['dueAt'],1220)
        self.assertEqual(plan['nominalAt'],1280)
        self.s['lastQuota'].update(allowed=False,blockingReset=9000)
        self.assertEqual(timers.request(self.s,self.path,1220,self.db)['dueAt'],9120)

    def test_no_candidate_or_disabled_monitor_never_prearms_healthy_quota(self):
        self.assertIsNone(timers.request(state(),self.path,1000,self.db))
        self.s['enabled'] = False
        self.assertIsNone(timers.request(self.s,self.path,1000,self.db))

    def test_only_an_observed_early_offset_adjusts_the_nominal_time(self):
        self.change(status='ACTIVE', next_run_at=1190000, next_run_nominal_at=1220000)
        plan = timers.request(self.s, self.path, 1000, self.db)
        self.assertEqual(plan['dueAt'], 1220)
        self.assertEqual(plan['nominalAt'], 1251)
        self.change(next_run_at=1250000, next_run_nominal_at=1220000)
        self.assertEqual(timers.request(self.s, self.path, 1000, self.db)['nominalAt'], 1220)

    def test_recovery_status_is_compact_read_only_and_excludes_send_history(self):
        self.s['queue']['sent-record'] = {'status': 'sent', 'token': 'private-send-token'}
        self.s['runtime']['unrelated'] = {'private': 'not needed for dispatch'}
        core.atomic_write(self.path, self.s)
        before = self.path.read_bytes()
        result = subprocess.run([sys.executable, str(ROOT/'skills/quota-resume/scripts/state.py'),
            '--state', str(self.path), 'recovery-status'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = json.loads(result.stdout)
        self.assertEqual([x['turnId'] for x in snapshot['pending']], ['work-turn'])
        self.assertNotIn('private-send-token', result.stdout)
        self.assertNotIn('unrelated', result.stdout)
        self.assertEqual(before, self.path.read_bytes())

    def test_hook_supplies_direct_update_and_verification_without_full_setup(self):
        core.atomic_write(self.path,self.s)
        record = timers.native_record('existing',self.db)
        with patch.object(timers,'native_record',return_value=record):
            r = hook.handle(self.path,{'session_id':'task','turn_id':'work-turn',
                'hook_event_name':'PostToolUse'}, lambda: observation()['usage'],1000)
        # Add the actual hook-observed turn first; unknown callbacks fail closed.
        self.assertIsNone(r)
        with patch.object(timers,'native_record',return_value=record):
            r = hook.handle(self.path,{'session_id':'task','turn_id':'work-turn',
                'hook_event_name':'UserPromptSubmit','prompt':'Finish the feature'},
                lambda: observation()['usage'],1000)
        text = r['hookSpecificOutput']['additionalContext']
        self.assertIn('automation_update ONCE',text)
        self.assertIn('Never use mode=view',text)
        self.assertIn('set-automation',text)
        self.assertNotIn('Read state from',text)
        saved = core.load(self.path)
        self.assertEqual(saved['armRequest']['arguments']['id'],'existing')
        self.assertIsNone(saved.get('scheduledFor'))

    def test_set_automation_does_not_acknowledge_a_failed_native_update(self):
        core.atomic_write(self.path,self.s)
        result = subprocess.run([sys.executable,str(ROOT/'skills/quota-resume/scripts/state.py'),
            '--state',str(self.path),'--now','1000','set-automation','--id','existing','--due-at','1220'],
            env={**os.environ,'CODEX_HOME':self.temp.name},capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(result.returncode,0)
        saved = core.load(self.path)
        self.assertIsNone(saved.get('scheduledFor'))
        self.assertIn('paused',saved['scheduleError'])
        self.assertEqual(saved['armLeaseUntil'],0)

    def test_subagent_events_never_arm_root_timer(self):
        core.atomic_write(self.path,self.s)
        before = self.path.read_bytes()
        for fields in [{'source':'fork'},{'source':'subagent'},{'parent_thread_id':'parent'},{'is_subagent':True}]:
            r=hook.handle(self.path,{'session_id':'child','turn_id':'child-turn','hook_event_name':'UserPromptSubmit',**fields},
                          lambda:self.fail('Subagents must not query or schedule'),1000)
            self.assertIsNone(r)
        self.assertEqual(before,self.path.read_bytes())

    def test_controller_recovery_events_never_emit_an_arm_instruction(self):
        core.atomic_write(self.path,self.s)
        before = self.path.read_bytes()
        for event in ['UserPromptSubmit','PostToolUse','Stop','SessionStart','SessionEnd','Interrupt']:
            r=hook.handle(self.path,{'session_id':'controller','turn_id':'recovery-check',
                'hook_event_name':event,'prompt':'Run the scheduled recovery check'},
                lambda:self.fail('Controller hooks must not query or preempt recovery'),1220)
            self.assertIsNone(r)
            self.assertEqual(before,self.path.read_bytes())

    def test_other_task_events_do_not_rearm_a_just_fired_recovery_check(self):
        self.change(status='ACTIVE',last_run_at=1220000,next_run_at=87620000,next_run_nominal_at=87920000)
        s=self.s
        core.observe(s,observation(1221,0,2000,'notLoaded','unknown'),1221)
        core.atomic_write(self.path,s)
        record=timers.native_record('existing',self.db)
        self.assertTrue(timers.request(s,self.path,1221,self.db)['recentRecoveryWake'])
        with patch.object(timers,'native_record',return_value=record):
            r=hook.handle(self.path,{'session_id':'other','turn_id':'other-work','hook_event_name':'UserPromptSubmit',
                                   'prompt':'Finish this project'},lambda:observation(1221,0,2000)['usage'],1221)
        self.assertIsNone(r)
        self.assertFalse(core.load(self.path).get('armRequest'))
        self.assertFalse(timers.request(s,self.path,1821,self.db)['recentRecoveryWake'])

    def test_early_native_wake_does_not_suppress_required_rearming(self):
        self.change(status='ACTIVE',last_run_at=1219000,next_run_at=87620000,next_run_nominal_at=87920000)
        self.assertFalse(timers.request(self.s,self.path,1221,self.db)['recentRecoveryWake'])


if __name__ == '__main__':
    unittest.main()
