"""Public announcement detection and durable broadcast ledger; never sends messages."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
import urllib.request
import uuid

import state as core

PROFILE = 'https://x.com/thsottiaux'
HANDLE = 'thsottiaux'
DEFAULT_KEYWORD = 'reset'
DEFAULT_MESSAGE = '请帮我全量检查此项目是否还有可以优化的地方，隐藏的问题，修复好'
MODEL = 'gpt-6-astra'
EFFORT = 'xhigh'


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        if self.tag in {'script', 'style', 'blockquote'}:
            return ''
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children)


class Timeline(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'img', 'br', 'input', 'meta', 'link', 'hr', 'source', 'wbr', 'area', 'embed', 'param', 'track', 'base', 'col'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_profile(html):
    """Read original post bodies, never page-wide text, quotes, replies or reposts."""
    parser = Timeline()
    parser.feed(html)
    posts = {}
    for article in (n for n in parser.root.walk() if n.tag == 'article'):
        nodes = list(article.walk())
        links = [n.attrs.get('href', '') for n in nodes if n.tag == 'a']
        authors = [h for h in links if re.fullmatch(r'/[A-Za-z0-9_]{1,15}', h)]
        if not authors or authors[0].lower() != '/'+HANDLE:
            continue
        # The first status link belongs to the outer post, not a quoted card.
        status = next((re.fullmatch(r'/([A-Za-z0-9_]+)/status/(\d{10,25})', h) for h in links
                       if re.fullmatch(r'/([A-Za-z0-9_]+)/status/(\d{10,25})', h)), None)
        if not status or status[1].lower() != HANDLE:
            continue
        social = ' '.join(n.text() for n in nodes if n.attrs.get('data-testid') == 'socialContext')
        if re.search(r'repost|retweet|转帖|转发|轉發', social, re.I):
            continue
        body = next((n for n in nodes if n.attrs.get('data-testid') == 'tweetText'
                     or 'whitespace-pre-wrap' in n.attrs.get('class', '').split()), None)
        if not body:
            continue
        # Reply indicators live before the body; ignore a reset inside a reply.
        prefix = ' '.join(n.text() if isinstance(n, Node) else n for n in article.children if n is not body)
        if re.search(r'\bReplying to\b|回复给|回覆給', prefix, re.I):
            continue
        identifier = status[2]
        created = ((int(identifier) >> 22) + 1288834974657)/1000
        text = body.text().strip()
        if text and created > 0:
            posts[identifier] = {'id': identifier, 'author': HANDLE, 'text': text,
                                 'publishedAt': created, 'url': PROFILE+'/status/'+identifier}
    if not posts:
        raise ValueError('未读取到可验证的原创帖子；页面可能要求登录或结构已改变。')
    return sorted(posts.values(), key=lambda p: int(p['id']), reverse=True)


def fetch_posts():
    request = urllib.request.Request(PROFILE, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; CodexTaskResume/1.0)',
        'Accept': 'text/html', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.geturl().split('?')[0].rstrip('/') != PROFILE:
            raise ValueError('公告页面跳转到其他地址；未确认公告来源。')
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError('公告页面过大，未处理。')
        return parse_profile(data.decode('utf-8', 'replace'))


def enabled(s):
    return s.get('settings', {}).get('announcementEnabled') is True


def normalize_keyword(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 100
            or any(ord(c) < 32 for c in value)):
        raise ValueError('公告关键词需为 1 到 100 字符，不能全为空格或包含换行。')
    return value.strip()


def keyword(s):
    return normalize_keyword(s.get('settings', {}).get('announcementKeyword', DEFAULT_KEYWORD))


def matches(text, value=DEFAULT_KEYWORD):
    return normalize_keyword(value).casefold() in text.casefold()


def change_keyword(s, now):
    a = s.get('announcements')
    if not a:
        return
    # Keep high-water and send history. A keyword edit must not replay old posts
    # or dispatch unsent events selected under the previous rule.
    for event in a.get('events', {}).values():
        if event.get('status') in {'pending', 'deferred'}:
            event['status'] = 'keyword_changed'
        for target in event.get('targets', {}).values():
            if target['status'] in {'pending', 'deferred'}:
                target['status'] = 'keyword_changed'
    a.update(keywordChangedAt=now, lastCheckAt=0, probeLeaseUntil=0, probeToken=None)


def activate(s, value, now):
    a = s.setdefault('announcements', {'events': {}})
    for event in a.setdefault('events', {}).values():
        if event.get('status') in {'pending', 'deferred'}:
            event['status'] = 'disabled'
        for target in event.get('targets', {}).values():
            if target['status'] in {'pending', 'deferred'}:
                target['status'] = 'disabled'
    if value:
        if a.get('stopRequest'):
            a.setdefault('stopHistory', []).append(a.pop('stopRequest'))
        a.update(activationId=uuid.uuid4().hex, enabledAt=now, baselineComplete=False,
                 highWaterId='0', lastCheckAt=0, probeLeaseUntil=0, lastError=None,
                 quotaBaseline=None,
                 authorization={'scope': 'all eligible local root Codex chats',
                                'model': MODEL, 'thinking': EFFORT,
                                'source': 'explicit announcement settings switch'})


def pending_events(s, now=None):
    now = time.time() if now is None else now
    a = s.get('announcements', {})
    return [e for e in a.get('events', {}).values() if e.get('status') == 'pending'
            and e.get('activationId') == a.get('activationId') and now <= e['expiresAt']]


def record(s, posts, now):
    if not enabled(s):
        return []
    a = s.setdefault('announcements', {'events': {}})
    if not a.get('activationId') or not a.get('enabledAt'):
        raise ValueError('公告监控未明确授权，请重新开启并保存。')
    old_high = int(a.get('highWaterId', '0'))
    valid = []
    for p in posts:
        if (p.get('author') != HANDLE or not re.fullmatch(r'\d{10,25}', str(p.get('id', '')))
                or p.get('url') != PROFILE+'/status/'+p['id']
                or not isinstance(p.get('text'), str)
                or not isinstance(p.get('publishedAt'), (int, float)) or p['publishedAt'] > now+5):
            raise ValueError('公告来源或时间无法确认。')
        valid.append(p)
    if not valid:
        raise ValueError('公告列表为空，未更新基线。')
    created = []
    current_keyword = keyword(s)
    since = max(a['enabledAt'], a.get('keywordChangedAt', 0))
    for p in sorted(valid, key=lambda p: int(p['id'])):
        if (int(p['id']) <= old_high or p['publishedAt'] < since
                or not matches(p['text'], current_keyword) or p['id'] in a['events']):
            continue
        a['events'][p['id']] = {**p, 'keyword': current_keyword, 'activationId': a['activationId'], 'detectedAt': now,
            'expiresAt': now+72*3600, 'status': 'pending', 'prepared': False, 'targets': {}}
        created.append(p['id'])
    a.update(highWaterId=str(max(old_high, *(int(p['id']) for p in valid))),
             baselineComplete=True, lastPost=max(valid, key=lambda p: int(p['id'])),
             lastSuccessAt=now, lastError=None)
    return created


def tick(path, provider=fetch_posts, now=None, force=False):
    now = time.time() if now is None else now
    if not path.exists():
        return {'enabled': False}
    token = uuid.uuid4().hex
    with core.locked(path):
        s = core.load(path)
        if not enabled(s):
            return status(s)
        a = s.setdefault('announcements', {})
        if now < a.get('probeLeaseUntil', 0) or (not force and now-a.get('lastCheckAt', 0) < s['settings'].get('announcementCheckMinutes', 5)*60):
            return status(s)
        a.update(probeToken=token, probeLeaseUntil=now+25, lastCheckAt=now)
        activation = a.get('activationId')
        core.atomic_write(path, s)
    try:
        posts = provider()
        failure = None
    except Exception:
        posts, failure = None, '公告读取失败；未触发发送，稍后重试或打开来源页面检查。'
    with core.locked(path):
        s = core.load(path)
        a = s.get('announcements', {})
        if a.get('probeToken') != token or a.get('activationId') != activation or not enabled(s):
            return status(s)
        a['probeLeaseUntil'] = 0
        try:
            if failure:
                raise ValueError(failure)
            record(s, posts, now)
        except ValueError as error:
            a['lastError'] = str(error)
        core.atomic_write(path, s)
        return status(s)


def status(s):
    a = s.get('announcements', {})
    events = list(a.get('events', {}).values())
    targets = [t for e in events for t in e.get('targets', {}).values()]
    return {'enabled': enabled(s), 'profile': PROFILE, 'keyword': keyword(s), 'model': MODEL,
        'thinking': EFFORT, 'baselineComplete': bool(a.get('baselineComplete')),
        'lastCheckAt': a.get('lastCheckAt'), 'lastSuccessAt': a.get('lastSuccessAt'),
        'lastError': a.get('lastError'), 'lastPost': a.get('lastPost'),
        'pendingEvents': len(pending_events(s)),
        'pendingTasks': sum(t['status'] in {'pending', 'deferred'} for t in targets),
        'sent': sum(t['status'] == 'sent' for t in targets),
        'uncertain': sum(t['status'] in {'reserved', 'uncertain'} for t in targets),
        'scheduledFor': a.get('scheduledFor'), 'control': a.get('control'),
        'resetStop': a.get('stopRequest'), 'events': pending_events(s)}


def watch_quota(s, usage, now):
    """A full reset is a transition of BOTH windows to 100%, not initial login."""
    if not enabled(s):
        return False
    q = core.quota(usage, s['settings']['limitId'])
    if q['accountId'] != s['accountId']:
        raise ValueError('账号不匹配，停止请求未授权。')
    bucket = usage.get('rateLimitsByLimitId', {}).get(s['settings']['limitId']) or usage.get('rateLimits', {})
    windows = [bucket.get(k) for k in ('primary', 'secondary')]
    windows = [w for w in windows if isinstance(w, dict)]
    five = next((w for w in windows if w.get('windowDurationMins') == 300), None)
    week = next((w for w in windows if w.get('windowDurationMins') == 10080), None)
    if not five or not week:
        raise ValueError('缺少五小时或每周真实额度，不能判断全部重置。')
    current = {'fiveUsed': core.number(five.get('usedPercent'), 'usedPercent', 0, 100),
               'weekUsed': core.number(week.get('usedPercent'), 'usedPercent', 0, 100),
               'fiveReset': five['resetsAt'], 'weekReset': week['resetsAt']}
    a = s.setdefault('announcements', {'events': {}})
    previous = a.get('quotaBaseline')
    a['quotaBaseline'] = current
    if (not previous or current['fiveUsed'] != 0 or current['weekUsed'] != 0
            or not (previous['fiveUsed'] > 0 or previous['weekUsed'] > 0)):
        return False
    if not (current['fiveReset'] > previous['fiveReset']+60 or current['weekReset'] > previous['weekReset']+60):
        return False
    key = str(current['fiveReset'])+':'+str(current['weekReset'])
    if a.get('stopRequest', {}).get('window') == key:
        return False
    a['stopRequest'] = {'window': key, 'detectedAt': now, 'status': 'pending', 'targets': {}}
    # Stop has priority. Do not automatically restart jobs just cancelled by
    # this reset, even if an older broadcast or continuation is still queued.
    for item in s['queue'].values():
        if item['status'] == 'pending':
            item.update(status='user_paused', pauseReason='full_reset_stop')
    for event in a.get('events', {}).values():
        if event['status'] in {'pending', 'deferred'}:
            event['status'] = 'cancelled_reset'
        for target in event.get('targets', {}).values():
            if target['status'] in {'pending', 'deferred'}:
                target['status'] = 'cancelled_reset'
    return True


def validate(s, obs, now):
    if not enabled(s):
        raise ValueError('公告监控已关闭。')
    at = core.number(obs.get('observedAt'), 'observedAt', 1)
    if not -5 <= now-at <= 90:
        raise ValueError('原生状态已过期。')
    q = core.quota(obs.get('usage'), s['settings']['limitId'])
    if q['accountId'] != s['accountId']:
        raise ValueError('账号不匹配，未授权发送。')
    return q


def identity(s, t):
    return (t.get('kind') == 'codex' and t.get('hostId') == 'local'
            and t.get('archived') is False and t.get('isSubagent') is False
            and t.get('id') != s['controllerId'] and t.get('id') not in s['excluded'])


def prepare(s, obs, now):
    validate(s, obs, now)
    if obs.get('inventoryComplete') is not True:
        raise ValueError('需要完整的原生本地对话列表，不能只发送当前页。')
    threads = obs.get('threads')
    if not isinstance(threads, list) or len({t.get('id') for t in threads}) != len(threads):
        raise ValueError('原生对话列表无效或存在重复。')
    for e in pending_events(s, now):
        if e.get('prepared'):
            continue
        e['targets'] = {t['id']: {'threadId': t['id'], 'title': t.get('title', t['id']),
            'status': 'pending', 'preparedAt': now} for t in threads if identity(s, t)}
        e['prepared'] = True
        if not e['targets']:
            e['status'] = 'done'
    return status(s)


def reserve(s, event_id, thread_id, obs, now):
    q = validate(s, obs, now)
    e = next((e for e in pending_events(s, now) if e['id'] == event_id), None)
    if not e or not e.get('prepared'):
        return {'accepted': False}
    item = e['targets'].get(thread_id)
    if not item or item['status'] != 'pending':
        return {'accepted': False}
    t = next((t for t in obs.get('threads', []) if t.get('id') == thread_id), {})
    if not identity(s, t) or t.get('status') not in {'idle', 'notLoaded', 'systemError'} or t.get('assessment') != 'ready_for_review' or not t.get('turnId') or not t.get('evidence'):
        return {'accepted': False}
    if not q['allowed'] or q['remaining'] < s['settings']['threshold']:
        return {'accepted': False, 'blockingReset': q['blockingReset']}
    item.update(status='reserved', token=uuid.uuid4().hex, reservedAt=now,
        message=s['settings'].get('announcementMessage', DEFAULT_MESSAGE), model=MODEL, thinking=EFFORT)
    return {'accepted': True, 'token': item['token'], 'threadId': thread_id, 'hostId': 'local',
            'message': item['message'], 'model': MODEL, 'thinking': EFFORT}


def settle(s, event_id, thread_id, outcome, now):
    if outcome not in {'skipped', 'deferred'}:
        raise ValueError('不支持的目标状态。')
    e = s.get('announcements', {}).get('events', {}).get(event_id, {})
    item = e.get('targets', {}).get(thread_id)
    if item and item['status'] == 'pending':
        item.update(status=outcome, checkedAt=now)
        finish(e)


def finish(e):
    items = list(e.get('targets', {}).values())
    if items and all(t['status'] not in {'pending', 'deferred'} for t in items):
        e['status'] = 'done'
    elif items and not any(t['status'] == 'pending' for t in items):
        e['status'] = 'deferred'


def on_event(s, sid, event_name, now):
    if not enabled(s):
        return
    for e in s.get('announcements', {}).get('events', {}).values():
        if now > e['expiresAt'] and e['status'] in {'pending', 'deferred'}:
            e['status'] = 'expired'
        target = e.get('targets', {}).get(sid)
        if e['status'] in {'deferred', 'pending'} and target and target['status'] == 'deferred' and event_name == 'Stop':
            target['status'], e['status'] = 'pending', 'pending'
            s['announcements']['scheduledFor'] = None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, default=core.data_dir()/'state.json')
    subs = p.add_subparsers(dest='command', required=True)
    subs.add_parser('status')
    for command in ['prepare', 'reserve']:
        sub = subs.add_parser(command)
        sub.add_argument('--input', type=Path, required=True)
        if command == 'reserve':
            sub.add_argument('--event', required=True)
            sub.add_argument('--thread', required=True)
    sub = subs.add_parser('settle')
    sub.add_argument('--event', required=True)
    sub.add_argument('--thread', required=True)
    sub.add_argument('--outcome', choices=['skipped', 'deferred'], required=True)
    sub = subs.add_parser('commit')
    sub.add_argument('--token', required=True)
    sub.add_argument('--outcome', choices=['sent', 'uncertain'], required=True)
    sub = subs.add_parser('mark-scheduled')
    sub.add_argument('--id', required=True)
    sub.add_argument('--due-at', type=float, required=True)
    args, now = p.parse_args(), time.time()
    with core.locked(args.state):
        s = core.load(args.state)
        if args.command == 'status':
            result = status(s)
        elif args.command == 'prepare':
            result = prepare(s, core.read_json(args.input), now)
        elif args.command == 'reserve':
            result = reserve(s, args.event, args.thread, core.read_json(args.input), now)
        elif args.command == 'settle':
            settle(s, args.event, args.thread, args.outcome, now)
            result = status(s)
        elif args.command == 'mark-scheduled':
            if not enabled(s):
                raise ValueError('公告监控已关闭。')
            core.number(args.due_at, 'dueAt', 1)
            s['announcements'].update(scheduledFor=args.due_at,
                scheduledEventIds=[e['id'] for e in pending_events(s, now)])
            s.update(automationId=args.id, scheduledFor=args.due_at, armLeaseUntil=0)
            result = {'automationId': args.id, 'scheduledFor': args.due_at}
        else:
            match = next(((e, t) for e in s.get('announcements', {}).get('events', {}).values()
                          for t in e.get('targets', {}).values() if t.get('token') == args.token), None)
            if not match or match[1]['status'] != 'reserved':
                raise ValueError('预留记录不存在或已经提交，不得重发。')
            e, item = match
            item.update(status=args.outcome, committedAt=now)
            finish(e)
            result = {'status': args.outcome, 'threadId': item['threadId']}
        if args.command != 'status':
            core.atomic_write(args.state, s)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
