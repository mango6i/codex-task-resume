# Orchestration and CLI

The local detector calls a documented read-only quota endpoint through a separate stdio app-server. It never starts a turn, resumes a thread, or calls inference. A separate process cannot establish whether desktop tasks are active, so only native desktop tools determine final task status and send messages.

Native hooks must be reviewed/trusted by the user. They run on session start/resume, user prompt, tool completion, turn stop, interrupt, and session end. There is no generic window-open event. The MCP service runs a model-free worker only for the life of its client connection. Tool-completion hooks are asynchronous; the other checks may briefly wait for the read-only query. A shared throttle and query lease prevent concurrent/repeated network checks.

## CLI

Version 1.0 uses `server/settings_mcp.py` (local stdio, official `openai/settings` capability) and the official host settings form only. Native read/update tools use `preferences.py`. Read operations create no state, query no quota and request no inference. Partial updates retain the other values. A preference edit never activates monitoring; only the explicit monitoring switch can enable an initialized state. Capability schema: https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#structured-settings

Before first activation, saved preferences are kept beside state in `preferences.json`. First initialization imports them without enabling when `--preserve-enabled-state` is passed. A different account/controller still requires explicit ownership migration. Native settings changes to pending delay clear the scheduling marker so the next local task event can re-arm; they never cause an early send. Turning the switch off blocks local dispatch immediately; an already scheduled native check may still wake once and then exit/pause itself.

Use absolute paths and actual process arguments. Do not interpolate titles/messages into executable shell syntax. Prefer UTF-8 JSON/text files. Global options go before the subcommand.

The fast runner sends observations through `recovery_fast.py --payload-base64` using ASCII-encoded JSON. Never put native error text directly in a PowerShell literal: curly apostrophes such as the one in `You’ve` are also delimiters. Startup paths use ASCII JSON decoded after shell parsing, preserving Unicode and quotes. `--json` remains for legacy/manual use; it is not the automated transport. A helper process failure reports the action and exit code and never authorizes sending.

```text
python <state.py> paths
python <state.py> --state <absolute-state.json> init --account <id> --controller <id> --authorized --threshold 2 --delay-seconds 120 --check-seconds 60 --message-file <UTF-8.txt>
python <state.py> --state <state.json> status
python <state.py> --state <state.json> recovery-status
python <state.py> --state <state.json> arm-plan
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

Require explicit local identity and exclusion flags at capture. Capturing an active turn requires turnId, turnStartedAt and purpose=task. Selection never captures inactive chats. For dispatch, latestTurn.id must exactly match the captured local root turn and that turn must have failed specifically from quota exhaustion within the captured quota window. Include the real completedAt; old failures and plugin setup requests never qualify. Native status accepts active, idle, notLoaded, systemError and needsAttention; only idle/notLoaded/systemError can dispatch. Unknown status, inaccessible targets and ambiguous evidence defer. The fast runner retains the local root binding from that exact captured turn, requires a live local native identity and rejects explicit archived/subagent flags; it never guesses a new target from titles or inventory.

Snapshot freshness: 90 seconds maximum, 5 seconds future tolerance. Refresh before each reserve. The five-hour window may be primary or secondary; select by duration=300 and configured bucket. Missing/malformed quota fails closed. A reset timestamp must advance by more than 60 seconds before recovery; tiny backend rounding changes are not reset events. A fresh valid snapshot, elapsed due time, available quota, and task evidence are all required.

`observe` returns `due`, `changes`, `pendingCount`, `nextWakeAt`, and `scheduleMode`. `reset` means schedule the returned future timestamp. `event` means no periodic schedule: after processing, pause the heartbeat and rely on later local task events. `disabled` prevents dispatch and requires pausing the native schedule. A weekly exhaustion may require a later reset even though the five-hour window refreshed.

## Scheduling

Use native structured `automation_update` with `arm-plan`'s prepared arguments. It reuses the native record's name, prompt, controller and notification policy. Never add `executionEnvironment` to an update: the current update schema rejects it. `mode=view` only displays a card and cannot enable a paused timer. Do not override models/thinking, edit native TOML/SQLite, or show raw recurrence syntax to the user.

`arm-plan` requires captured pending turns and uses their ORIGINAL reset plus CURRENT delay. There is no fixed extra five-minute wait. Only an observed early native offset adjusts the nominal recurrence; re-read `arm-plan` after an early-wake verification failure and correct the SAME timer, with at most two corrections. Convert to the CLIENT time zone when different from the returned local time. `set-automation` reads only native timer metadata and persists success only when actual next_run_at is ACTIVE, targets the saved controller, is not early, and is within the intended cycle. An early recovery wake must re-arm, not consume or discard candidates. After the check pause it unless a real exhausted window requires one later wake; the daily recurrence must not remain as an idle poll.

`recovery-status` is read-only and returns current ownership, saved scope/threshold/delay, exclusions and pending captured turns. Routine recovery instead uses `recovery_fast.py bootstrap` and its native-tool runner in the current functions session. It checks only recorded IDs and native state/error flags, reserves immediately before sending and commits immediately afterward. It never reads chat history or scans all chats. A native completed turn or final-answer flag skips the candidate; an exact-turn native usage-limit error provides interruption evidence without interpreting project text. A new target turn must be verified before reporting execution; a controller wake or delivery receipt alone does not establish it.

The detector persists an arm lease to avoid several active chats scheduling the same wake. The native skill must still search matching existing automations before create, because a process can fail after creating a schedule but before saving its ID.

Eligible work requests early arming at UserPromptSubmit or PostToolUse while quota is still usable. `reason=active_work_prearm` is not a captured candidate: it only prepares the current reset-plus-delay check. If quota later runs out, Stop can record that exact turn and the prepared native check can run without any further user message. A fresh quota window must not move an outstanding original check forward. If no eligible captured turn remains at the wake, pause rather than keeping daily empty runs.

There is still no guarantee if quota is already exhausted before any successful arm, hooks are untrusted, the controller was deleted, or the machine/client is unavailable. The local queue survives. The read-only worker does not independently create native schedules or send. Do not call early-arm tests proof of a full real-quota automatic recovery cycle, and do not introduce periodic model checks.

## Saved native heartbeat prompt

Generate the exact compact prompt using `recovery_schedule.fast_prompt(absolute_state_path)`. `arm-plan` migrates recognized older plugin prompts automatically, while preserving unrelated custom timer prompts. The generated prompt loads `recovery_fast.py bootstrap` and executes its reviewed `native_dispatch.js` body with native tools inside `functions.exec`; no standalone process can send messages. Current settings and exact message are read from durable state at reservation time. Never embed user titles or messages as executable code.

Do not ask every wake to load this reference or reconstruct observations using conversation text. For a specific compatibility failure, inspect only the failing native envelope/schema; leave reservations and uncertain deliveries unreplayed. Client scheduling and model startup can still delay the wake; this path removes repeated context review after it starts.

## Sources

- Packaging and native hook trust: https://developers.openai.com/plugins/build/plugins
- Lifecycle hooks and limits: https://learn.chatgpt.com/docs/hooks
- Native schedules: https://learn.chatgpt.com/docs/automations
- Read-only account/rateLimits/read: https://learn.chatgpt.com/docs/app-server

A native scheduled model turn and the target continuation consume normal quota. Read-only local quota queries request no inference. Installation/trust/activation in the user's client remains necessary; source-level tests and a quota probe do not prove that an installed plugin has fired or completed a real reset cycle.
