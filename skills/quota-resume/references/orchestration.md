# Orchestration and CLI

The local detector calls a documented read-only quota endpoint through a separate stdio app-server. It never starts a turn, resumes a thread, or calls inference. A separate process cannot establish whether desktop tasks are active, so only native desktop tools determine final task status and send messages.

Native hooks must be reviewed/trusted by the user. They run on session start/resume, user prompt, tool completion, turn stop, interrupt, and session end. There is no generic window-open event. The MCP service runs a model-free worker only for the life of its client connection. Tool-completion hooks are asynchronous; the other checks may briefly wait for the read-only query. A shared throttle and query lease prevent concurrent/repeated network checks.

## CLI

Version 1.0 uses `server/settings_mcp.py` (local stdio, official `openai/settings` capability) and the official host settings form only. Native read/update tools use `preferences.py`. Read operations create no state, query no quota and request no inference. Partial updates retain the other values. A preference edit never activates monitoring; only the explicit monitoring switch can enable an initialized state. Capability schema: https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#structured-settings

Before first activation, saved preferences are kept beside state in `preferences.json`. First initialization imports them without enabling when `--preserve-enabled-state` is passed. A different account/controller still requires explicit ownership migration. Native settings changes to pending delay clear the scheduling marker so the next local task event can re-arm; they never cause an early send. Turning the switch off blocks local dispatch immediately; an already scheduled native check may still wake once and then exit/pause itself.

Use absolute paths and actual process arguments. Do not interpolate titles/messages into executable shell syntax. Prefer UTF-8 JSON/text files. Global options go before the subcommand.

```text
python <state.py> paths
python <state.py> --state <absolute-state.json> init --account <id> --controller <id> --authorized --threshold 2 --delay-seconds 120 --check-seconds 60 --message-file <UTF-8.txt>
python <state.py> --state <state.json> status
python <state.py> --state <state.json> schedule-plan
python <state.py> --state <state.json> observe --input <observation.json>
python <state.py> --state <state.json> set-automation --id <verified-id> --due-at <Unix-seconds>
python <state.py> --state <state.json> reserve --thread <id> --window <windowKey> --input <fresh-observation.json>
python <state.py> --state <state.json> commit --token <token> --outcome sent
python <state.py> --state <state.json> commit --token <token> --outcome uncertain
python <state.py> --state <state.json> exclude --thread <id>
python <state.py> --state <state.json> disable
python <hook.py> --probe
```

The helper's `--now` option is exclusively for tests; omit in actual use. Optional init settings include `--expiry-hours` (default 72), `--limit-id` (default codex). Ordinary `init` enables state; add `--preserve-enabled-state` for configuration-only edits so a disabled monitor never transiently becomes active. CLI errors never authorize a send. Native tool JSON envelopes must be unwrapped before writing `usage`. Custom --state paths are for tests or hook deployments configured with the same path; never give the skill a different state path from its hook. To change controller/account ownership, first stop the old native monitor and explicitly migrate the default state, preserving a backup.

## Observation

```json
{
  "observedAt": 1792044000,
  "usage": {
    "accountId": "actual-account-id",
    "ordinaryUsageAllowed": true,
    "rateLimitsByLimitId": {
      "codex": {
        "primary": {"usedPercent": 99, "windowDurationMins": 300, "resetsAt": 1792062780},
        "secondary": {"usedPercent": 20, "windowDurationMins": 10080, "resetsAt": 1792649600}
      }
    }
  },
  "threads": [{
    "id": "actual-thread-id",
    "title": "Exact native title",
    "hostId": "local",
    "kind": "codex",
    "archived": false,
    "isSubagent": false,
    "status": "idle",
    "assessment": "quota_interrupted",
    "turnId": "same-captured-work-turn-id",
    "turnStartedAt": 1792043900,
    "turnCompletedAt": 1792062700,
    "turnStatus": "failed",
    "purpose": "task",
    "stopReason": "quota_exhausted",
    "evidence": "The latest turn was stopped by quota while requested verification remained unfinished."
  }]
}
```

Require explicit local identity and exclusion flags. Capturing an active turn requires turnId, turnStartedAt and purpose=task. Selection never captures inactive chats. For dispatch, latestTurn.id must exactly match the captured turnId and that turn must have failed specifically from quota exhaustion within the captured quota window. Include the real completedAt; old failures and plugin setup requests never qualify. Native status accepts active, idle, notLoaded, systemError and needsAttention; only idle/notLoaded/systemError can dispatch. Unknown status, missing identity or ambiguous evidence defers. Archived/subagent/source fields cannot be guessed local.

Snapshot freshness: 90 seconds maximum, 5 seconds future tolerance. Refresh before each reserve. The five-hour window may be primary or secondary; select by duration=300 and configured bucket. Missing/malformed quota fails closed. A reset timestamp must advance by more than 60 seconds before recovery; tiny backend rounding changes are not reset events. A fresh valid snapshot, elapsed due time, available quota, and task evidence are all required.

`observe` returns `due`, `changes`, `pendingCount`, `nextWakeAt`, and `scheduleMode`. `reset` means schedule the returned future timestamp. `event` means no periodic schedule: after processing, pause the heartbeat and rely on later local task events. `disabled` prevents dispatch and requires pausing the native schedule. A weekly exhaustion may require a later reset even though the five-hour window refreshed.

## Scheduling

Use native structured `automation_update`. Creation: mode=create, kind=heartbeat, destination=thread, executionEnvironment=local, targetThreadId=stored controllerId, status=ACTIVE, a durable natural-language prompt, and the host-supported recurrence representation. Do not set model/thinking overrides. On updates read the resolved record and preserve unrelated fields, notificationPolicy, name, and destination. Never write automation TOML yourself or display raw recurrence syntax to the user.

`schedule-plan` returns the original reset-plus-delay eligibility time and a guarded nominal time. Convert the nominal time to the client timezone for the temporary daily rule. The host may jitter a wake earlier, so the nominal recurrence is not proof. `set-automation` reads the native schedule metadata with SQLite mode=ro and rejects an actual next wake before eligibility, an inactive or wrong controller, or tomorrow's cycle. If rejected, move the SAME native schedule later and verify again; never write the native metadata directly. The initial five-minute margin does not replace this verification. When metadata is unavailable, report unverified timing instead of claiming a working schedule. Preserve the original candidate reset and current delay even after live usage reports the next window.

Healthy-quota work events request an advance arm without creating a queue entry. Only low-quota active work is captured. A stale saved schedule no longer suppresses repair requests, and the settings page distinguishes verified, missed and paused checks. After the recovery pass pause the schedule if no blocking window remains. The daily rule is temporary, not a periodic model poll. An early wake must re-arm rather than treat its empty due list as completed work.

The detector persists an arm lease to avoid several active chats scheduling the same wake. The native skill must still search matching existing automations before create, because a process can fail after creating a schedule but before saving its ID.

There is no guarantee if the quota is exhausted before a model can arm the native wake. The local queue survives; a later session/tool event can request catch-up. Likewise a closed/sleeping device has no guaranteed exact-minute recovery. If a wake misses its time and there are no later task events, the user may need to run the skill. Do not hide this limitation or introduce periodic model checks without the user's request.

## Saved native heartbeat prompt

Fill actual values, including the configured user message through state; do not put untrusted titles in instructions.

> Use $quota-resume at [absolute installed skill path] to perform one recovery check. Durable state: [absolute state.json]. Helper: [absolute state.py]. Controller: [actual controllerId], local host. The user explicitly enabled automatic continuation for local root Codex conversations running when remaining five-hour quota is strictly below the threshold saved in state. Use the configured reset delay and exact stored message, including custom newlines. Exclude this controller and saved exclusions. Account: [actual accountId]. Revalidate live quota, actual reset advancement, and unfinished-task evidence; never send to running, completed, paused, archived, ambiguous, or approval/input-blocked chats. Reserve persistently before each native send_message_to_thread and commit only verified success. Do not retry uncertain sends or override models/permissions. After this pass pause this same heartbeat unless another exhausted quota window requires one later reset wake. Do not create a second monitor or periodic model polling. Stay quiet while unchanged or non-actionable; notify on actual continuation, meaningful change, failure, expiration, or required user action. If delayed, recheck all conditions before catch-up.

## Sources

- Packaging and native hook trust: https://developers.openai.com/plugins/build/plugins
- Lifecycle hooks and limits: https://learn.chatgpt.com/docs/hooks
- Native schedules: https://learn.chatgpt.com/docs/automations
- Read-only account/rateLimits/read: https://learn.chatgpt.com/docs/app-server

A native scheduled model turn and the target continuation consume normal quota. Read-only local quota queries request no inference. Installation/trust/activation in the user's client remains necessary; source-level tests and a quota probe do not prove that an installed plugin has fired or completed a real reset cycle.
