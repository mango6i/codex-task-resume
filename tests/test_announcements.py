import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'skills/quota-resume/scripts')]
import announcements as ann
import announcement_service as service
import desktop_bridge as bridge
import preferences
import settings_mcp
import state as core


NOW = time.time()


def post(at, text='New RESET announcement', author=ann.HANDLE):
    identifier = str((int(at*1000)-1288834974657) << 22)
    return {'id': identifier, 'author': author, 'text': text, 'publishedAt': at,
            'url': ann.PROFILE+'/status/'+identifier}


def usage(five=20, week=30, reset=NOW+1000, account='a'):
    return {'accountId': account, 'ordinaryUsageAllowed': True, 'rateLimitsByLimitId': {
        'codex': {'primary': {'usedPercent': five, 'windowDurationMins': 300, 'resetsAt': reset},
                  'secondary': {'usedPercent': week, 'windowDurationMins': 10080, 'resetsAt': reset+100000}}}}


def state():
    return {'version': 1, 'enabled': False, 'accountId': 'a', 'controllerId': 'controller',
        'settings': dict(preferences.DEFAULTS, announcementEnabled=True), 'excluded': [], 'queue': {},
        'events': [], 'runtime': {'controller': {'turnId': 'control-turn', 'active': True}}}


def target(identifier='task', **kwargs):
    return dict({'id': identifier, 'title': 'Project', 'kind': 'codex', 'hostId': 'local',
        'archived': False, 'isSubagent': False, 'status': 'idle', 'turnId': 'work-turn',
        'assessment': 'ready_for_review', 'evidence': 'Native final delivery; no pending input'}, **kwargs)


class AnnouncementState(unittest.TestCase):
    def setUp(self):
        self.s = state()
        ann.activate(self.s, True, NOW-100)

    def observe(self, **kwargs):
        return dict({'observedAt': NOW, 'usage': usage(), 'inventoryComplete': True,
                     'threads': [target()]}, **kwargs)

    def event(self):
        item = post(NOW-50)
        ann.record(self.s, [item], NOW)
        ann.prepare(self.s, self.observe(), NOW)
        return item['id']

    def test_historical_posts_and_duplicate_or_edited_posts_never_replay(self):
        self.assertEqual(ann.record(self.s, [post(NOW-1000)], NOW), [])
        p = post(NOW-5)
        self.assertEqual(ann.record(self.s, [p], NOW), [p['id']])
        p['text'] = 'RESET again, edited'
        self.assertEqual(ann.record(self.s, [p], NOW+1), [])
        self.assertEqual(len(self.s['announcements']['events']), 1)

    def test_default_custom_phrase_chinese_and_literal_keyword_matching(self):
        self.s['settings'].pop('announcementKeyword')
        self.assertEqual(ann.keyword(self.s), 'reset')
        self.assertTrue(ann.matches('Day 8/ Reset'))
        self.assertTrue(ann.matches('Global RESET landing today', 'global reset'))
        self.assertTrue(ann.matches('今日限额重置完成', '限额重置'))
        self.assertTrue(ann.matches('Announcement: R.SET', 'r.set'))
        self.assertFalse(ann.matches('reset', 'r.set'))
        self.assertFalse(ann.matches('reset', '[reset]'))
        self.s['settings']['announcementKeyword'] = '限额重置'
        p = post(NOW-5, text='今日限额重置')
        self.assertEqual(ann.record(self.s, [p], NOW), [p['id']])
        self.assertEqual(self.s['announcements']['events'][p['id']]['keyword'], '限额重置')

    def test_keyword_change_cancels_unsent_keeps_ledger_and_rejects_pre_change_posts(self):
        event_id = self.event()
        event = self.s['announcements']['events'][event_id]
        event['targets']['sent'] = {'status': 'sent', 'token': 'sent-token'}
        event['targets']['uncertain'] = {'status': 'uncertain', 'token': 'unknown-token'}
        self.s['announcements']['probeToken'] = 'old-probe'
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            core.atomic_write(path, self.s)
            preferences.update(path, {'announcementKeyword': ' New limit '}, now=NOW)
            saved = core.load(path)
        old = saved['announcements']['events'][event_id]
        self.assertEqual(old['status'], 'keyword_changed')
        self.assertEqual(old['targets']['task']['status'], 'keyword_changed')
        self.assertEqual(old['targets']['sent'], {'status': 'sent', 'token': 'sent-token'})
        self.assertEqual(old['targets']['uncertain'], {'status': 'uncertain', 'token': 'unknown-token'})
        self.assertIsNone(saved['announcements']['probeToken'])
        self.assertEqual(ann.record(saved, [post(NOW-2, text='New limit')], NOW+1), [])
        latest = post(NOW+2, text='NEW LIMIT')
        self.assertEqual(ann.record(saved, [latest], NOW+3), [latest['id']])
        self.assertEqual(ann.record(saved, [latest], NOW+4), [])

    def test_account_identity_activation_and_timestamp_are_enforced(self):
        with self.assertRaises(ValueError):
            ann.record(self.s, [post(NOW-5, author='other')], NOW)
        with self.assertRaises(ValueError):
            ann.record(self.s, [post(NOW+100)], NOW)
        with self.assertRaises(ValueError):
            ann.prepare(self.s, self.observe(usage=usage(account='other')), NOW)
        with self.assertRaises(ValueError):
            ann.prepare(self.s, self.observe(observedAt=NOW-91), NOW)

    def test_complete_inventory_required_and_scope_excludes_control_remote_and_subagents(self):
        ann.record(self.s, [post(NOW-50)], NOW)
        with self.assertRaises(ValueError):
            ann.prepare(self.s, self.observe(inventoryComplete=False), NOW)
        targets = [target(), target('controller'), target('remote', hostId='remote'),
                   target('child', isSubagent=True), target('archive', archived=True)]
        ann.prepare(self.s, self.observe(threads=targets), NOW)
        event = next(iter(self.s['announcements']['events'].values()))
        self.assertEqual(set(event['targets']), {'task'})

    def test_completed_project_can_be_reviewed_once_with_exact_custom_prompt_and_model(self):
        event = self.event()
        self.s['settings']['announcementMessage'] = '  检查项目\n只修复已确认的问题。  '
        reserved = ann.reserve(self.s, event, 'task', self.observe(), NOW)
        self.assertTrue(reserved['accepted'])
        self.assertEqual(reserved['message'], self.s['settings']['announcementMessage'])
        self.assertEqual((reserved['model'], reserved['thinking']), ('gpt-6-astra', 'xhigh'))
        self.assertFalse(ann.reserve(self.s, event, 'task', self.observe(), NOW)['accepted'])
        service.commit(self.s, reserved['token'], 'uncertain', NOW)
        self.assertFalse(ann.reserve(self.s, event, 'task', self.observe(), NOW)['accepted'])

    def test_running_unknown_approval_paused_and_low_quota_cannot_dispatch(self):
        event = self.event()
        for changes in [{'status': 'active'}, {'assessment': 'unknown'}, {'assessment': 'awaiting_input'},
                        {'assessment': 'user_paused'}, {'archived': True}, {'isSubagent': True}]:
            self.assertFalse(ann.reserve(self.s, event, 'task', self.observe(threads=[target(**changes)]), NOW)['accepted'])
        self.assertFalse(ann.reserve(self.s, event, 'task', self.observe(usage=usage(five=100)), NOW)['accepted'])
        self.assertFalse(ann.reserve(self.s, event, 'task', self.observe(usage=usage(week=100)), NOW)['accepted'])

    def test_new_activation_cancels_pending_but_keeps_confirmed_or_uncertain_sends(self):
        event = self.event()
        reserved = ann.reserve(self.s, event, 'task', self.observe(), NOW)
        ann.activate(self.s, False, NOW+1)
        self.assertEqual(self.s['announcements']['events'][event]['targets']['task']['token'], reserved['token'])
        self.assertEqual(self.s['announcements']['events'][event]['targets']['task']['status'], 'reserved')
        ann.activate(self.s, True, NOW+2)
        self.assertIsNone(self.s['announcements']['quotaBaseline'])

    def test_full_reset_stops_and_cancels_queued_work_but_never_on_initial_full_quota(self):
        self.assertFalse(ann.watch_quota(self.s, usage(0, 0), NOW))
        self.assertFalse(ann.watch_quota(self.s, usage(90, 30), NOW+1))
        self.assertFalse(ann.watch_quota(self.s, usage(0, 30, reset=NOW+3000), NOW+2))
        event = self.event()
        self.s['queue']['old'] = {'status': 'pending'}
        self.assertTrue(ann.watch_quota(self.s, usage(0, 0, reset=NOW+4000), NOW+3))
        self.assertEqual(self.s['queue']['old']['status'], 'user_paused')
        self.assertEqual(self.s['announcements']['events'][event]['status'], 'cancelled_reset')
        self.assertFalse(ann.watch_quota(self.s, usage(0, 0, reset=NOW+4000), NOW+4))

    def test_offline_read_keeps_high_watermark_and_no_event_is_created(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            core.atomic_write(path, self.s)
            def offline():
                raise OSError('offline')
            ann.tick(path, offline, NOW)
            saved = core.load(path)
            self.assertFalse(saved['announcements']['events'])
            self.assertEqual(saved['announcements']['highWaterId'], '0')
            self.assertTrue(saved['announcements']['lastError'])


class ProfileParsing(unittest.TestCase):
    def html(self, text='RESET', author='thsottiaux', extra=''):
        identifier = post(NOW-5)['id']
        return f'<article><a href="/{author}">@{author}</a><a href="/{author}/status/{identifier}">time</a>{extra}<div data-testid="tweetText">{text}</div></article>'

    def test_only_own_original_post_body_is_used(self):
        html = self.html('No announcement')+'<div>reset in navigation</div>'
        parsed = ann.parse_profile(html)
        self.assertEqual(parsed[0]['text'], 'No announcement')
        self.assertEqual(parsed[0]['author'], 'thsottiaux')
        self.assertAlmostEqual(parsed[0]['publishedAt'], NOW-5, delta=.01)
        with self.assertRaises(ValueError):
            ann.parse_profile(self.html(author='other'))
        with self.assertRaises(ValueError):
            ann.parse_profile(self.html(extra='<span data-testid="socialContext">reposted</span>'))
        with self.assertRaises(ValueError):
            ann.parse_profile(self.html(extra='<span>Replying to @somebody</span>'))
        self.assertNotIn('reset', ann.parse_profile(self.html('Hello<blockquote>reset</blockquote>'))[0]['text'].lower())

    def test_login_error_and_empty_pages_are_not_valid_empty_timelines(self):
        for html in ['<html>Please log in</html>', '<html>reset reset reset</html>', '']:
            with self.assertRaises(ValueError):
                ann.parse_profile(html)


class FakeDesktop:
    def __init__(self, fail_stop=False):
        self.calls, self.fail_stop, self.interrupted = [], fail_stop, set()

    def verify(self, s):
        return usage()

    def models(self):
        return True

    def inventory(self, s):
        return [self.raw('task'), self.raw('active')]

    def raw(self, identifier):
        return {'id': identifier, 'name': 'Project', 'source': 'vscode', 'cwd': str(ROOT),
                'status': {'type': 'active' if identifier == 'active' and identifier not in self.interrupted else 'idle'}}

    def latest(self, identifier):
        if identifier in self.interrupted:
            status = 'interrupted'
        else:
            status = 'inProgress' if identifier == 'active' else 'completed'
        return {'id': identifier+'-turn', 'status': status,
                'items': [{'type': 'agentMessage', 'phase': 'final_answer', 'text': 'All requested work delivered.'}]}

    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'thread/read':
            return {'thread': self.raw(params['threadId'])}
        if method == 'thread/resume':
            return {}
        if method == 'turn/start':
            return {'turn': {'id': 'new-review', 'status': 'inProgress'}}
        if method == 'turn/interrupt':
            if self.fail_stop:
                raise TimeoutError('ambiguous interruption')
            self.interrupted.add(params['threadId'])
            return {}
        raise AssertionError(method)


class NativeControl(unittest.TestCase):
    def test_pending_or_uncertain_reset_stop_blocks_all_new_reviews(self):
        for stop_status in ['pending', 'needs_attention']:
            with self.subTest(status=stop_status), tempfile.TemporaryDirectory() as td:
                path = Path(td)/'state.json'
                s = state()
                ann.activate(s, True, NOW-100)
                ann.record(s, [post(NOW-50)], NOW)
                s['announcements']['stopRequest'] = {'status': stop_status}
                core.atomic_write(path, s)
                desktop = FakeDesktop()
                service.run_pending(path, desktop, NOW)
                self.assertFalse(desktop.calls)

    def test_client_disconnect_prevents_native_starts_and_interruptions(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            s = state()
            ann.activate(s, True, NOW-100)
            ann.record(s, [post(NOW-50)], NOW)
            core.atomic_write(path, s)
            desktop = FakeDesktop()
            service.run_pending(path, desktop, NOW, cancelled=lambda: True)
            self.assertFalse(any(m in {'turn/start', 'turn/interrupt'} for m, _ in desktop.calls))

    def test_native_broadcast_reserves_then_sends_once_with_no_permission_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            s = state()
            ann.activate(s, True, NOW-100)
            ann.record(s, [post(NOW-50)], NOW)
            core.atomic_write(path, s)
            desktop = FakeDesktop()
            service.run_pending(path, desktop, NOW)
            saved = core.load(path)
            event = next(iter(saved['announcements']['events'].values()))
            self.assertEqual(event['targets']['task']['status'], 'sent')
            self.assertTrue(event['targets']['task']['token'])
            starts = [args for method, args in desktop.calls if method == 'turn/start']
            self.assertEqual(len(starts), 1)
            self.assertEqual(set(starts[0]), {'threadId', 'input', 'model', 'effort'})
            self.assertEqual(starts[0]['input'][0]['text'], ann.DEFAULT_MESSAGE)
            service.run_pending(path, desktop, NOW)
            self.assertEqual(sum(method == 'turn/start' for method, _ in desktop.calls), 1)
            self.assertEqual(core.load(path)['announcements']['events'][event['id']]['targets']['active']['status'], 'deferred')

    def test_stop_confirmation_and_unknown_result_are_not_retried(self):
        for fail in [False, True]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as td:
                path = Path(td)/'state.json'
                s = state()
                ann.activate(s, True, NOW-100)
                ann.watch_quota(s, usage(80, 80), NOW)
                ann.watch_quota(s, usage(0, 0, reset=NOW+4000), NOW+1)
                core.atomic_write(path, s)
                desktop = FakeDesktop(fail_stop=fail)
                self.assertTrue(service.stop_running(path, desktop, NOW+2))
                request = core.load(path)['announcements']['stopRequest']
                self.assertEqual(request['targets']['active']['status'], 'uncertain' if fail else 'stopped')
                self.assertEqual(desktop.calls[-1], ('turn/interrupt', {'threadId': 'active', 'turnId': 'active-turn'}))
                service.stop_running(path, desktop, NOW+3)
                self.assertEqual(sum(m == 'turn/interrupt' for m, _ in desktop.calls), 1)
                self.assertFalse(any(m == 'turn/start' for m, _ in desktop.calls))

    def test_unsupported_client_rejects_enable_without_changing_any_settings(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            core.atomic_write(path, state())
            original = path.read_bytes()
            with patch.object(bridge, 'capability', return_value={'available': False, 'reason': 'unsupported'}):
                response = settings_mcp.dispatch('tools/call', {'name': 'settings.update', 'arguments': {
                    'set': {'announcementEnabled': True, 'announcementMessage': 'Changed'}}}, path)
            self.assertTrue(response['isError'])
            self.assertTrue(response['structuredContent']['activationBlocked'])
            self.assertEqual(response['structuredContent']['values'], preferences.values(path))
            self.assertEqual(path.read_bytes(), original)

    def test_unsaved_keyword_check_is_read_only_and_uses_preview_keyword(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'state.json'
            s = state()
            ann.activate(s, True, NOW-100)
            core.atomic_write(path, s)
            original = path.read_bytes()
            with patch.object(ann, 'fetch_posts', return_value=[post(NOW-5, text='New LIMIT')]), patch.object(
                    bridge, 'capability', return_value={'available': False, 'reason': 'unsupported'}):
                response = settings_mcp.dispatch('tools/call', {'name': 'announcements.check',
                    'arguments': {'keyword': ' limit '}}, path)
            self.assertFalse(response.get('isError'))
            self.assertEqual(response['structuredContent']['previewKeyword'], 'limit')
            self.assertEqual(response['structuredContent']['previewMatchCount'], 1)
            self.assertEqual(response['structuredContent']['keyword'], 'reset')
            self.assertEqual(path.read_bytes(), original)

    def test_direct_native_proof_rejects_unloaded_or_different_turn(self):
        class Probe:
            verify = bridge.Desktop.verify
            def call(self, method, params):
                if method == 'account/rateLimits/read':
                    return usage()
                if method == 'thread/loaded/list':
                    return {'data': ['controller']}
                raise AssertionError(method)
            def latest(self, sid):
                return {'id': 'different-turn'}
        with self.assertRaises(ValueError):
            Probe().verify(state())


if __name__ == '__main__':
    unittest.main()
