"""Recover only recorded native quota failures, without reading task text."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_behavior import state, observation
import state as core
import recovery_fast as fast


class FastRecovery(unittest.TestCase):
    def setUp(self):
        self.s = state()
        core.observe(self.s, observation(), 1000)
        self.window = next(iter(self.s['queue'].values()))['windowKey']
        self.payload = {'threadId':'task', 'windowKey':self.window, 'observedAt':1220,
            'usage':observation(1220,0,2000)['usage'],
            'snapshot':{'polls':[{'thread':{'id':'task','hostId':'local','status':{'type':'systemError'}},
                'latestTurn':{'id':'work-turn','status':'failed','startedAt':900,'completedAt':1105,
                              'error':{'message':'You’ve hit your usage limit.'}},
                'assistantPhase':'commentary'}]}}

    def reserve(self, payload=None, now=1220):
        obs = fast.native_observation(self.s, payload or self.payload, now)
        return {'accepted':False} if obs is None else core.reserve(self.s,obs,'task',self.window,now)

    def test_native_error_is_sufficient_and_exact_multiline_text_is_preserved(self):
        result = self.reserve()
        self.assertTrue(result['accepted'])
        self.assertEqual(result['message'],self.s['settings']['message'])
        self.assertEqual(result['threadId'],'task')
        self.assertFalse(self.reserve()['accepted'])

    def test_completed_paused_waiting_active_and_changed_turns_never_send(self):
        for change in ['completed','final','paused','approval','active','superseded','other_error','archived','subagent']:
            with self.subTest(change=change):
                self.setUp()
                poll = self.payload['snapshot']['polls'][0]
                if change == 'completed':poll['latestTurn']['status']='completed'
                if change == 'final':poll['assistantPhase']='final_answer'
                if change == 'paused':poll['latestTurn']['status']='interrupted'
                if change == 'approval':poll['thread']['status']={'type':'active','activeFlags':['waitingOnApproval']}
                if change == 'active':poll['thread']['status']={'type':'active'}
                if change == 'superseded':poll['latestTurn']['id']='new-user-turn'
                if change == 'other_error':poll['latestTurn']['error']['message']='Network connection failed'
                if change == 'archived':poll['thread']['archived']=True
                if change == 'subagent':poll['thread']['isSubagent']=True
                self.assertFalse(self.reserve()['accepted'])

    def test_quota_delay_identity_and_existing_reservations_still_gate_delivery(self):
        for change in ['early','no_reset','exhausted','account','old_error','disabled','reserved','uncertain','excluded']:
            with self.subTest(change=change):
                self.setUp()
                q=self.payload['usage']['rateLimitsByLimitId']['codex']['primary']
                if change=='early':self.s['settings']['delaySeconds']=300
                if change=='no_reset':q['resetsAt']=1100
                if change=='exhausted':q['usedPercent']=100
                if change=='account':self.payload['usage']['accountId']='different'
                if change=='old_error':self.payload['snapshot']['polls'][0]['latestTurn']['completedAt']=500
                if change=='disabled':self.s['enabled']=False
                if change in {'reserved','uncertain'}:next(iter(self.s['queue'].values()))['status']=change
                if change=='excluded':self.s['excluded']=['task']
                self.assertFalse(self.reserve()['accepted'])

    def test_unrecorded_native_target_and_failed_envelopes_are_rejected(self):
        p=deepcopy(self.payload)
        p['threadId']='unrecorded'
        self.assertIsNone(fast.native_observation(self.s,p,1220))
        self.payload['snapshot']={'isError':True,'content':[{'type':'text','text':'not available'}]}
        with self.assertRaises(ValueError):self.reserve()

    def test_only_queue_ids_are_in_plan(self):
        self.s['selection']={'mode':'automatic','threads':{'never-recorded':{'title':'Do not inspect'}}}
        self.assertEqual([i['threadId'] for i in fast.plan(self.s,1220)],['task'])
        self.s['excluded']=['task']
        self.assertEqual(fast.plan(self.s,1220),[])

    def test_finish_only_rearms_early_or_truly_exhausted_windows(self):
        with patch.object(core.recovery_schedule,'request',return_value={'dueAt':1220}) as request:
            self.assertEqual(fast.finish(self.s,'state.json',1100)['action'],'rearm')
            self.assertEqual(request.call_count,1)
        record={'kind':'heartbeat','target_thread_id':'controller','status':'PAUSED','next_run_at':None}
        self.s['automationId']='same'
        with patch.object(core.recovery_schedule,'native_record',return_value=record):
            self.assertEqual(fast.finish(self.s,'state.json',1220)['action'],'none')
            self.s['lastQuota'].update(allowed=False,blockingReset=9000,observedAt=1220)
            with patch.object(core.recovery_schedule,'request',return_value={'dueAt':9120}):
                self.assertEqual(fast.finish(self.s,'state.json',1220)['action'],'rearm')
            self.s['excluded']=['task']
            self.assertEqual(fast.finish(self.s,'state.json',1220)['action'],'none')

    def test_account_change_is_reported_and_disables_dispatch(self):
        self.payload['usage']['accountId']='different'
        self.assertEqual(self.reserve(),{'accepted':False,'reason':'account_changed'})
        self.assertFalse(self.s['enabled'])

    def test_superseded_capture_in_same_window_cannot_block_the_current_turn(self):
        older=deepcopy(next(iter(self.s['queue'].values())))
        older['turnId']='superseded-work'
        self.s['queue']['old-capture']=older
        self.assertTrue(self.reserve()['accepted'])
        self.assertEqual(older['status'],'excluded')

    def test_recovery_cannot_capture_a_new_active_turn_while_checking_an_old_candidate(self):
        poll=self.payload['snapshot']['polls'][0]
        poll['thread']['status']={'type':'active'}
        poll['latestTurn'].update(id='new-unrecorded-turn',status='inProgress')
        self.payload['usage']['rateLimitsByLimitId']['codex']['primary']['usedPercent']=99
        self.assertFalse(self.reserve()['accepted'])
        self.assertEqual(len(self.s['queue']),1)
        self.assertEqual(next(iter(self.s['queue'].values()))['status'],'excluded')


if __name__=='__main__':unittest.main()
