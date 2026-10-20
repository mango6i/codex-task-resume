# Codex Task Resume

<p align="center"><a href="README.md"><kbd>中文</kbd></a> &nbsp;|&nbsp; <a href="README_EN.md"><kbd>English</kbd></a></p>
<p align="center"><img src="assets/icon.svg" width="80" alt="Codex Task Resume"></p>

**Manage local tasks and continue eligible unfinished chats after quota resets.**

For local Codex / ChatGPT Work on Windows. Configure quota thresholds, reset delays and continuation prompts, and select local target chats.

[Download](https://github.com/mango6i/codex-task-resume/releases) · [Installation guide (Chinese)](docs/INSTALL.md) · [Install with an assistant](docs/INSTALL_WITH_CODEX.md#english) · [Report an issue](https://github.com/mango6i/codex-task-resume/issues)

Version **1.0 · Preview** · Author [果果](https://github.com/mango6i) · [MIT License](LICENSE)

> **Before use:** a native recovery check must be scheduled in advance. If quota is exhausted before scheduling, an automatic wake is not guaranteed. A complete real exhaustion, reset, wake and send cycle remains unverified; later quota windows also need a correctly timed schedule.

## Features

- **Global settings:** configure the threshold, reset delay and multiline prompt in one place.
- **Local quota checks:** query quota while the plugin service is connected, including idle periods, without minute-by-minute model polling.
- **Actual reset timestamps:** show the account API's five-hour reset time and the planned check after your saved delay.
- **Focused project chats:** detector hooks only record data. They inject no plugin instructions into working chats and never block task completion. Scheduling belongs to the controller.
- **Chat management:** search, refresh, select targets, select the current list or clear selections.
- **Recovery wake verification:** arm while quota remains available and verify the client's actual wake rather than relying on its nominal time. Missed and paused checks are shown separately.
- **Clear status:** distinguish saved targets, candidates, recent checks, task events and actual recovery wake times.
- **Pre-send checks:** require recovered quota, an elapsed delay and an explicit quota failure of the exact captured work turn. Historical failures, setup requests and uncertain sends are never replayed.

## Tibo reset announcements and full-reset stops

An optional, independent workflow reads [@thsottiaux](https://x.com/thsottiaux). A new original post containing the saved keyword (default `reset`, case insensitive) requests a project review in all eligible local root chats. This scope is independent of the continuation allowlist. Historical posts, reposts and quoted text do not trigger a review; each post is sent at most once per chat.

- **Custom keyword:** enter one word or phrase, matched as literal text without regular expressions. A change applies only to posts published after saving; historical posts are not replayed and send history remains deduplicated.
- **Custom instruction:** the default is `请帮我全量检查此项目是否还有可以优化的地方，隐藏的问题，修复好`. Whitespace and newlines are preserved.
- **Model:** GPT-6 Astra, Extra high (`gpt-6-astra` / `xhigh`). No substitute is selected if unavailable.
- **Running chats:** deferred; waiting-for-input/approval, paused, archived, remote and subagent chats are excluded. Completed projects can receive a new review.
- **Full quota reset:** when live five-hour and weekly usage transition to 100% remaining with reset timestamps advancing, stop all running eligible local project turns and cancel unsent reviews and old continuation candidates. The controller is excluded. Initial login with full quota does not trigger a stop. Already consumed quota cannot be refunded.
- **Confirmation:** interruption must be verified as `interrupted`. Ambiguous sends or stops are never retried automatically; an unresolved stop blocks further review starts. To clear an unresolved stop, verify the tasks manually, then disable and re-enable the feature.

**Compatibility:** this workflow requires an existing, verifiable native desktop control daemon and the requested model. The plugin checks both on each computer. It only connects through `codex app-server proxy`; it never starts a replacement server or enables remote control. If the installed client does not expose that channel, activation is blocked and the reason is displayed. This is not available on every current desktop build. Detection alone is not successful automation.

The connected plugin service checks the public profile every 5 minutes by default. Quota and stop checks use the saved quota interval (60 seconds by default). Detection and interruption have polling and network latency; exact-second execution is not guaranteed. The client and device must remain running. Read-only checks use no model inference. Review turns consume the account's ordinary quota.

### Enable the workflow

1. Initialize one controller on this computer and trust the hooks as described below.
2. Click **Check announcements & compatibility**. This action does not send messages or interrupt tasks.
3. Set the keyword, review instruction and announcement interval. The keyword defaults to `reset` and can be saved independently. **Check announcements & compatibility** can preview an unsaved keyword without dispatching reviews.
4. Enable **announcement reviews & reset stops**, at the right end of the announcement row, and save. Unsupported clients refuse activation atomically and restore the switch to its actual saved state. This is not a plugin permission toggle that can be forced on in the panel; the keyword and instruction can still be saved separately.

## Screenshots

These screenshots use isolated test data. Chat titles, quota values and times are examples.

### Continuation settings

![Quota threshold, actual reset time, delay and custom prompt](assets/settings-light.png)

### Chat management

![Monitoring status, continuation scope and local chats](assets/tasks-wide.png)

<details><summary>Small window, English UI and save confirmation</summary>

![Small-window settings](assets/settings-compact.png)

![English settings](assets/settings-en.png)

![Settings saved confirmation](assets/save-success.png)

</details>

## Installation

Requires **Windows, Python 3.10+, Codex CLI**, and a Codex / ChatGPT Work client supporting local plugins.

1. Download and extract `codex-task-resume-1.0.zip` from [Releases](https://github.com/mango6i/codex-task-resume/releases).
2. Fully exit the client and run `install.cmd` from the extracted folder.
3. Reopen the client after installation succeeds.
4. Open **Settings → Management → Manage Codex Task Resume** in plugin details, or use the global **Codex Task Resume · Manage** entry. Chinese systems display the corresponding Chinese labels.

Alternatively, attach the ZIP to a local Codex / ChatGPT Work chat with file access and use the [installation prompt](docs/INSTALL_WITH_CODEX.md#english). This package uses a local installer. GitHub's automatically generated Source code archives differ from the release package.

## Quick start

1. Initialize once in one retained local controller chat:

   ```text
   Use $quota-resume to initialize settings, preserve my saved values and leave continuation disabled.
   ```

2. Review and trust the plugin's hooks through the native client.
3. Configure settings, explicitly enable continuation and save.
4. Choose the scope and targets in Chats, then **Save selection**.
5. While quota remains available, ask the controller to establish a native recovery check and confirm the schedule status.

Settings and targets are managed globally. Each target chat does not need separate initialization.

An established native recovery schedule runs in the background; neither the controller chat nor the settings panel needs to remain visible. Keep the client and device running. The plugin service performs read-only checks and cannot independently create native schedules or send messages; initial scheduling and later re-arming still require native assistant capabilities.

“Check pre-armed only” means a controller check was scheduled in advance. It does not mean the low-quota threshold was reached or a continuation was sent. Candidate capture still requires remaining quota strictly below the saved threshold. If the controller cannot wake automatically, detection records remain persisted; another project chat is never used to perform scheduling.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| Quota threshold | 2% | Capture below this remaining five-hour quota; range 0.01–99% |
| Delay after reset | 2 minutes | Wait after the actual reset before checking; range 1–60 minutes |
| Continuation prompt | `继续任务` | Send the exact saved text, including newlines |
| Minimum check interval | 60 seconds | Shared throttle for service and task-event quota queries |
| Automatic continuation | Off | Explicit user activation after initialization and hook review |

For example, a reset at 19:13 and a two-minute delay make 19:15 the earliest eligible check. Native wakes can fire early or late, so the plugin adds a nominal timing margin and verifies that the actual wake is not before 19:15. The check may occur later; see the panel for its verified time. Unverifiable schedules are not marked ready. The green confirmation's timestamp is the settings save time.

## Language

The project homepage defaults to Chinese; use the top link for English documentation. The plugin follows the Windows display language: Chinese variants use Simplified Chinese; other languages use English. Reopen the panel after changing the system language.

Chat titles, project paths, saved prompts and newlines remain unchanged.

## FAQ

**Why are there zero candidates after selecting chats?** Selection only limits the allowed scope and never enqueues or starts chats. Capture requires an identified work turn actually observed running at low quota in this window. A saved selection and an established schedule are separate states.

**Does “Turn ended” mean the task is complete?** It describes the latest task event only. Native status and context are checked before sending. Completed, active, paused, blocked, archived and ambiguous tasks are skipped.

**What if quota refresh fails?** The query retries once. Recent successful data is explicitly marked as historical; otherwise the panel shows unavailable and you can retry later. Historical display data never authorizes sending.

**Does it use quota?** Settings and local read-only quota queries do not call the model. Scheduling, recovery checks and continued work use normal quota.

**Does it work with the client closed?** Local detection stops when the plugin service disconnects or the device sleeps; recovery may be delayed. Ordinary ChatGPT web, cloud tasks, remote hosts and subagents are unsupported.

**How do I stop?** Disable continuation and save. To pause the native check too, tell the controller: `Use $quota-resume to stop monitoring and automatic continuation.`

## Data and privacy

Settings and runtime records are stored locally under `%LOCALAPPDATA%/SkillHubQuotaResume/`. Updates preserve settings, selections, candidates and send records. Source and releases exclude actual user settings, account associations, task queues and private chats.

This is a third-party local plugin. Screenshots use sample data; redact personal chat titles, paths and account information in reports.

## Development

```powershell
python -m unittest discover -s tests -v
python scripts/build_release.py
```

The release package and SHA-256 checksums are written to `dist/`. See [verification scope (Chinese)](docs/VERIFICATION.md).

## License

[MIT License](LICENSE) · Author [果果](https://github.com/mango6i).
