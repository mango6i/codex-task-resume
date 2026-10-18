# Announcement reviews and full-reset stops

This optional workflow is configured in the same official settings modal. It has its own exact multiline instruction and activation switch, placed in a separate trailing column of its workflow row. Defaults: disabled, public profile `https://x.com/thsottiaux`, user-editable case-insensitive literal keyword `reset`, five-minute profile interval, message `请帮我全量检查此项目是否还有可以优化的地方，隐藏的问题，修复好`, model `gpt-6-astra`, effort `xhigh`.

## Compatibility and activation

Use `announcements.check` to read the public profile and verify local native control and model availability. An optional `keyword` previews unsaved text without changing settings or recording events. It does not send, interrupt, or enable anything. `announcements.status` reads persisted status only. User settings and IDs belong to this computer; never distribute an existing state, controller ID or account in the package.

The native bridge connects ONLY through `codex app-server proxy` to an existing daemon. It checks account ownership and matches a loaded thread's latest turn to the actual local hook registry. A separate read-only server seeing saved metadata is insufficient. Never start another daemon or enable remote control to pass the check. Refuse activation atomically if control or the requested model/effort cannot be verified; do not substitute another model. A supported protocol and fixture tests do not establish that every desktop client exposes the control socket.

## Detection and dispatch

`scripts/announcements.py` parses only the target author's own original post body, verifies its status URL and timestamp, and keeps a high-water post ID. Historical posts and repeated/edited IDs never trigger another broadcast. Match the saved `announcementKeyword` as a literal case-insensitive substring, default `reset`; never treat it as a regular expression. A keyword change cancels unsent old-rule events, preserves reservations and send history, and only considers posts published after the change. The first successful check establishes the historical baseline; only posts published after explicit activation can trigger. Login pages, unavailable/changed HTML and read failures do not advance the baseline or authorize a send. Page text, images, links, replies and quoted instructions never become the configured task instruction.

`server/announcement_service.py` uses model-free reads while its MCP connection is alive. Each event freezes one complete native inventory of eligible local root chats. The continuation allowlist does not limit this separately authorized all-project review scope. Exclude controller, exclusions, archived, remote, ephemeral and subagent chats. Running chats defer until a later real task-stop event. Completed projects may get a new review; pending questions, approvals, manual interruptions, failed/unknown context and ambiguous state are skipped.

Before every start re-read native quota, account, the target's actual state and latest context. Require the selected model and effort. Persist a unique event/thread reservation before starting once; confirm the returned new native turn ID before committing `sent`. An error or ambiguous outcome is `uncertain` and is never replayed. Permission and sandbox settings are inherited, never overridden. The exact saved instruction is the only input; do not append announcement text or extra permissions.

The assistant must not manually repeat broadcasts already handled by the service. Inspect the separate announcement ledger. If using the optional CLI helpers for diagnostics, paths are resolved from the installed skill and shared state, never hardcoded to the original developer's computer:

```text
python <announcements.py> --state <state.json> status
python <announcements.py> --state <state.json> prepare --input <fresh-native-inventory.json>
python <announcements.py> --state <state.json> reserve --event <post-id> --thread <id> --input <fresh-native-observation.json>
python <announcements.py> --state <state.json> commit --token <token> --outcome sent|uncertain
```

`prepare` requires `inventoryComplete: true`, fresh `observedAt`, live `usage` and explicit identity/exclusion fields. Dispatch requires `assessment: ready_for_review`, a native latest `turnId`, an idle native state and contextual evidence. Missing evidence fails closed. Diagnostics never establish authorization to send.

## Full quota reset takes priority

After a non-full baseline, a live transition of BOTH five-hour and weekly windows to 100% remaining with reset advancement creates one persisted stop request. Merely logging in with full quota never triggers a stop. Ordinary five-hour refresh with weekly usage still present does not satisfy this rule. Read failures and account mismatch never authorize interruption.

At detection, cancel unsent review targets and mark old continuation candidates paused, preventing automatic restart after the stop. Snapshot currently running eligible local root turns; never stop the controller or another account/host. Before each interruption re-read identity and the exact active turn ID. Reserve once, request native `turn/interrupt`, and confirm that same turn becomes `interrupted`. A changed turn is superseded and skipped. An ambiguous stop is not retried, is displayed as requiring attention, and blocks later review starts. Do not report success solely from an RPC acknowledgement. Preserve the stop ledger when the user disables/re-enables after manually resolving it.

Both profile and quota checks are polling, so detection has the configured interval plus network latency. Already-used quota is not refundable. Keep the app and device available. No background model polling, extra heartbeat monitor, UI simulation, process termination, CLI resume, cloud API key or permission expansion is used.

Sources: [native protocol](https://learn.chatgpt.com/docs/app-server), [CLI reference](https://learn.chatgpt.com/docs/cli/reference), [plugin hook trust](https://developers.openai.com/plugins/build/plugins).
