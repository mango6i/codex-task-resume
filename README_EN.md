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
- **Fast recovery:** query only recorded targets and native quota-failure flags without reading project history or scanning every chat. Completed tasks are skipped; exact saved prompts and persistent deduplication are preserved.
- **Threshold-only scheduling:** a native recovery check requires a running turn recorded strictly below the saved threshold. Healthy quota and quota exactly at the threshold do not create empty checks.
- **Local quota checks:** query quota while the plugin service is connected, including idle periods, without minute-by-minute model polling.
- **Actual reset timestamps:** show the account API's five-hour reset time and the planned check after your saved delay.
- **Chat management:** search, refresh, select targets, select the current list or clear selections.
- **Clear status:** distinguish saved targets, candidates, recent checks, task events and schedule records.
- **Pre-send checks:** require recovered quota, an elapsed delay and an explicit quota failure of the exact captured work turn. Historical failures, setup requests and uncertain sends are never replayed.

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

Requires **Windows, Python 3.10+**, and a Codex / ChatGPT Work client supporting local plugins. The installer locates the client's bundled or separately installed Codex CLI without requiring manual PATH changes.

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
5. Trusted task-start and tool events record running work and request one check only when quota is strictly below the threshold. Confirm that the native recovery check is enabled.

Settings and targets are managed globally. Each target chat does not need separate initialization.

An established native recovery schedule runs in the background; neither the controller chat nor the settings panel needs to remain visible. Retain the controller chat and keep the client and device running. Only work recorded below the threshold requests a check. The read-only service cannot independently create schedules or send messages; automatic wake is not guaranteed if quota is exhausted before scheduling succeeds.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| Quota threshold | 2% | Capture below this remaining five-hour quota; range 0.01–99% |
| Delay after reset | 2 minutes | Wait after the actual reset before checking; range 1–60 minutes |
| Continuation prompt | `继续任务` | Send the exact saved text, including newlines |
| Minimum check interval | 60 seconds | Shared throttle for service and task-event quota queries |
| Automatic continuation | Off | Explicit user activation after initialization and hook review |

For example, a reset at 19:13 and a two-minute delay make recovery eligible from 19:15. The native scheduler may wake later. A timer is confirmed only after its active status, controller and actual next wake are verified; displaying a schedule card does not enable it. The green confirmation's timestamp is the settings save time.

## Language

The project homepage defaults to Chinese; use the top link for English documentation. The plugin follows the Windows display language: Chinese variants use Simplified Chinese; other languages use English. Reopen the panel after changing the system language.

Chat titles, project paths, saved prompts and newlines remain unchanged.

## FAQ

**Why are there zero candidates after selecting chats?** Selection only limits the allowed scope and never enqueues or starts chats. Capture requires an identified work turn actually observed running at low quota in this window. A saved selection and an established schedule are separate states.

**Does “Turn ended” mean the task is complete?** It describes the latest task event only. Only recorded chats are checked through native turn status and usage-limit errors; no history is read. Completed, active, paused, blocked, archived and ambiguous tasks are skipped.

**What if quota refresh fails?** The query retries once. Recent successful data is explicitly marked as historical; otherwise the panel shows unavailable and you can retry later. Historical display data never authorizes sending.

**Does it use quota?** Settings and local read-only quota queries do not call the model. Scheduling, recovery checks and continued work use normal quota.

**Does it work with the client closed?** Local detection stops when the plugin service disconnects or the device sleeps; recovery may be delayed. Ordinary ChatGPT web, cloud tasks, remote hosts and subagents are unsupported.

**Can client updates affect it?** Hooks, UI resources and native scheduling interfaces may change. Restart after updating the plugin and review hook trust if prompted. Check both captured candidates and the verified recovery timer; an enabled switch does not establish a schedule. This build keeps earlier UI entrypoints readable and does not create an extra continuation turn at Stop.

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
