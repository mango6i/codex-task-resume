# 让 Codex / ChatGPT Work 帮你安装

[English](#english) · [中文](#让-codex--chatgpt-work-帮你安装)

在**能够访问本机文件的本地 Codex / ChatGPT Work 对话**中，附上发行 ZIP，或提供已下载 ZIP 的完整路径，然后发送：

```text
请帮我安装我提供的“Codex 任务续接”ZIP 到当前本地客户端。先解压并阅读包内 README.md 和 docs/INSTALL_WITH_CODEX.md，检查 Python 3.10+ 与 Codex CLI，再使用包内安装器安装。保留原有设置、控制对话、勾选范围、任务队列及发送记录，不清理其他插件或客户端公共缓存。安装失败恢复旧版。不要自动开启监控，不要向任何任务发送消息。安装后核对插件文件和设置入口；需要我重启客户端或审核脚本时明确告诉我。
```

普通 ChatGPT 网页对话没有本机安装权限。这个包含本地 MCP 服务和 Hook 的发行包采用本地安装流程；不能把上传 ZIP 成功当作本地服务已经安装并运行。依赖缺失时应说明具体缺少什么，不静默安装系统级运行环境。

## 给安装助手的执行步骤

1. 检查 ZIP 文件清单与路径边界，不跟随目录联接、符号链接，不把归档路径展开到目标目录外。
2. 在普通本地目录解压。确认内部清单身份为 `skillhub-quota-resume`，版本 `1.0.0`，项目地址为 `https://github.com/mango6i/codex-task-resume`。
3. 阅读 `install.ps1` 与 `maintenance.ps1`；检查 Python 3.10+、Codex CLI。使用当前用户运行包内安装器，不提升为管理员。
4. 当前客户端或本插件设置服务占用缓存时，先准备可核对的安装包，解释需要关闭客户端后完成；不要强行结束客户端主进程或正在运行的任务。安装器失败后应确认回滚，不把失败当作成功。
5. 安装器会注册本地插件来源，按本机 Python 路径准备 Hook、按系统显示语言准备详情文案，并调用原生插件安装与文件校验。沿用内部身份，保留旧配置。
6. 成功后核对实际安装目录和缓存、版本、项目地址、管理资源与用户偏好。清理仅限已经确认属于本项目的过期副本。
7. 让用户重新打开客户端，再验证 MCP 连接与设置入口。安装不等于启用；不得擅自信任 Hook、开启监控、建立排程或发送续接消息。

## English

Attach the release ZIP, or provide its absolute local path, in a **local Codex / ChatGPT Work chat with access to this computer**, then send:

```text
Install the Codex Task Resume ZIP I provided into this local client. Extract it and read README_EN.md and docs/INSTALL_WITH_CODEX.md first. Check Python 3.10+ and Codex CLI, then use the bundled installer. Preserve existing settings, controller ownership, selections, candidates and send records; do not clean other plugins or shared client caches. Restore the previous installation if installation fails. Do not enable monitoring or send messages to any task. Verify the installed files and settings entry. Tell me if I need to restart the client or review hooks.
```

Ordinary ChatGPT web cannot install local software. Uploading a ZIP does not prove its local MCP service is installed and running. Extract within a checked local boundary, verify package identity/version/project URL, review the installer, and use it as the current user. Explain missing dependencies instead of silently installing system runtimes. Do not forcibly close the desktop client or active tasks. Verify rollback after failures, preserve user data, and verify the MCP connection after the user reopens the client. Installation alone grants no authorization to enable monitoring, trust hooks, schedule checks or send continuation messages.
