"""Explicit settings-button account migration. Never enables or sends work."""
import os
import stat
import time
import uuid

import hook
import preferences
import state as core


def rebind(path, confirmed, usage_provider=hook.read_usage, now=None):
    if confirmed is not True:
        raise ValueError('请点击“绑定当前账号”按钮确认关联。')
    before = core.load(path) if path.exists() else None
    if not before:
        raise ValueError('请先初始化一次控制对话，再使用账号绑定按钮。')
    # Resolve identity from the live account endpoint, never from UI text or a
    # cached quota. A second read rejects a login change during this operation.
    first = core.quota(usage_provider(), before['settings']['limitId'])
    usage = usage_provider()
    quota = core.quota(usage, before['settings']['limitId'])
    if first['accountId'] != quota['accountId']:
        raise ValueError('登录账号正在切换，未修改绑定。请登录稳定后重试。')
    now = time.time() if now is None else now
    with core.locked(path):
        s = core.load(path)
        if (s['accountId'], s['controllerId']) != (before['accountId'], before['controllerId']):
            raise ValueError('绑定已被其他操作修改，请刷新后重试。')
        changed = s['accountId'] != quota['accountId']
        if changed:
            # Keep a full durable backup, including pending/reserved/uncertain
            # records. Refuse links before creating any migration artifact.
            for item in [path, *path.parents]:
                attrs = item.lstat()
                if item.is_symlink() or getattr(attrs, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
                    raise ValueError('绑定路径包含链接，未修改账号。')
            backup = path.with_name('state.before-account-rebind-'+uuid.uuid4().hex+'.json')
            with backup.open('xb') as stream:
                stream.write(path.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            old_account = s['accountId']
            for item in s['queue'].values():
                if item.get('status') == 'pending':
                    item.update(status='excluded', exclusionReason='account_rebound', accountId=old_account)
            s.update(accountId=quota['accountId'], enabled=False, runtime={}, scheduledFor=None,
                scheduleWakeAt=None, scheduleVerifiedAt=None, prearmResetAt=None, armRequest=None,
                armLeaseUntil=0, nextArmAfter=0, scheduleError=None, probeToken=uuid.uuid4().hex,
                probeLeaseUntil=0, lastProbeAt=now, lastProbeError=None,
                lastQuota={**quota, 'observedAt': now}, lastLiveUsage=usage,
                lastProbeSuccessAt=now, lastProbeSource='account_rebind', serviceHeartbeatAt=0)
            core.event(s, now, 'account_rebound', previousAccountId=old_account,
                accountId=quota['accountId'], backup=backup.name, source='explicit account bind button')
            core.atomic_write(path, s)
    from local_catalog import monitor_state
    return {'changed': changed, 'values': preferences.values(path),
        'quota': {'remaining': quota['remaining'], 'resetAt': quota['reset'], 'readAt': now},
        'monitor': monitor_state(s, quota), 'sent': False}
