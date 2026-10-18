"""Shared native/desktop settings store. Saving preferences never implicitly enables monitoring."""
import argparse
import json
from pathlib import Path
import sys
import time
import state as core
import announcements

DEFAULTS = {'threshold': 2, 'delaySeconds': 120, 'checkSeconds': 60,
            'expiryHours': 72, 'message': '继续任务', 'limitId': 'codex',
            'announcementEnabled': False, 'announcementCheckMinutes': 5,
            'announcementMessage': announcements.DEFAULT_MESSAGE}


def preference_path(state_path):
    return state_path.with_name('preferences.json')


def effective(state_path):
    cfg = dict(DEFAULTS)
    pref = preference_path(state_path)
    if pref.exists():
        raw = core.read_json(pref)
        if not isinstance(raw, dict):
            raise ValueError('设置文件格式不正确')
        cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    s = core.load(state_path) if state_path.exists() else None
    if s:
        cfg.update(s['settings'])
    return cfg, s


def values(state_path):
    cfg, s = effective(state_path)
    return {'threshold': cfg['threshold'], 'delayMinutes': cfg['delaySeconds']/60,
            'checkSeconds': cfg['checkSeconds'], 'message': cfg['message'],
            'monitoringEnabled': bool(s and s['enabled']),
            'announcementEnabled': cfg['announcementEnabled'],
            'announcementCheckMinutes': cfg['announcementCheckMinutes'],
            'announcementMessage': cfg['announcementMessage']}


def update(state_path, patch, now=None):
    now = time.time() if now is None else now
    allowed = {'threshold', 'delayMinutes', 'checkSeconds', 'message', 'monitoringEnabled',
               'announcementEnabled', 'announcementCheckMinutes', 'announcementMessage'}
    if not isinstance(patch, dict) or not patch or set(patch)-allowed:
        raise ValueError('设置为空或包含不支持的字段')
    converted = {}
    for key, lo, hi in [('threshold', 0.01, 99), ('delayMinutes', 1, 60), ('checkSeconds', 30, 3600),
                        ('announcementCheckMinutes', 1, 60)]:
        if key in patch:
            converted[key] = core.number(patch[key], key, lo, hi)
    if 'checkSeconds' in converted and int(converted['checkSeconds']) != converted['checkSeconds']:
        raise ValueError('检查间隔必须是整数秒')
    for message_key in ['message', 'announcementMessage']:
        if message_key not in patch:
            continue
        message = patch[message_key]
        if not isinstance(message, str) or not message.strip() or len(message) > 12000:
            raise ValueError('自动发送内容需为 1 到 12000 字符，不能全为空格')
        converted[message_key] = message
    for switch in ['monitoringEnabled', 'announcementEnabled']:
        if switch in patch and not isinstance(patch[switch], bool):
            raise ValueError('启用开关必须是布尔值')
    if 'announcementCheckMinutes' in converted and int(converted['announcementCheckMinutes']) != converted['announcementCheckMinutes']:
        raise ValueError('公告检查间隔必须是整数分钟')
    with core.locked(state_path):
        cfg, s = effective(state_path)
        if (patch.get('monitoringEnabled') is True or patch.get('announcementEnabled') is True) and (not s or not s.get('accountId') or not s.get('controllerId')):
            raise ValueError('请先在控制对话运行一次“使用 $quota-resume 初始化设置”，再开启自动续接。')
        old_delay = cfg['delaySeconds']
        for key in ['threshold', 'checkSeconds', 'message', 'announcementMessage', 'announcementCheckMinutes']:
            if key in converted:
                cfg[key] = converted[key]
        if 'delayMinutes' in converted:
            cfg['delaySeconds'] = int(round(converted['delayMinutes']*60))
        if s:
            previous_announcement = announcements.enabled(s)
            if 'announcementEnabled' in patch:
                cfg['announcementEnabled'] = patch['announcementEnabled']
            s['settings'] = cfg
            if previous_announcement != cfg['announcementEnabled']:
                announcements.activate(s, cfg['announcementEnabled'], now)
            if cfg['delaySeconds'] != old_delay:
                for item in s['queue'].values():
                    if item['status'] == 'pending':
                        item['dueAt'] = item['resetAt'] + cfg['delaySeconds']
                        item['expiresAt'] = item['dueAt'] + cfg['expiryHours']*3600
                s['scheduledFor'] = None
                s['armLeaseUntil'] = 0
                s['nextArmAfter'] = 0
            if 'monitoringEnabled' in patch:
                was_enabled = s['enabled']
                s['enabled'] = patch['monitoringEnabled']
                if s['enabled'] and not was_enabled:
                    core.start_monitoring(s, now)
                if s['enabled']:
                    s['authorization'] = {'at': now, 'scope': 'local active root codex chats at threshold',
                        'messageConfiguredByUser': True, 'source': 'explicit settings switch'}
            core.event(s, now, 'settings_saved')
            core.atomic_write(state_path, s)
        core.atomic_write(preference_path(state_path), cfg)
        return values(state_path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('read')
    save = sub.add_parser('save')
    save.add_argument('--input', type=Path, required=True)
    args = p.parse_args()
    path = (args.state or core.data_dir()/'state.json').resolve()
    result = values(path) if args.command == 'read' else update(path, core.read_json(args.input))
    print(json.dumps({'values': result, 'configured': path.exists()}, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
