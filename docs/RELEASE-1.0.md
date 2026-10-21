# Codex 任务续接 1.0 · 预览版

Windows 本地 Codex / ChatGPT Work 的任务续接管理插件。

## 功能

- 自定义额度阈值、刷新后等待时长和多行续接提示词。
- 服务连接期间只读检测额度，显示实际刷新时间和最近检测状态。
- 本地对话搜索、勾选、全选当前列表和清空选择。
- 恢复排程使用兼容的原生更新参数，并核验启用状态、控制对话和实际唤醒时间；查看卡片不会被当作已启用。
- 修复停止事件产生额外轮次、无关项目收到排程指令的问题。
- 保留旧版界面入口，兼容不同工具返回格式，读取失败时保留对话列表。
- 更新失败时同时恢复原源码与缓存，保留用户设置和发送记录。
- 跟随系统显示中英文，支持浅色、深色及不同窗口大小。

## 安装

下载 `codex-task-resume-1.0.zip` 与 `SHA256SUMS.txt`。确认已有 Python 3.10+ 和 Codex CLI，解压后完全退出客户端，运行 `install.cmd`，成功后重开客户端。也可以使用包内的本地助手安装提示词。已有设置与运行记录保留。

[中文使用说明](https://github.com/mango6i/codex-task-resume/blob/main/README.md) · [English](https://github.com/mango6i/codex-task-resume/blob/main/README_EN.md)

![续接设置](https://raw.githubusercontent.com/mango6i/codex-task-resume/main/assets/settings-light.png)

## 使用限制

恢复检查需要提前建立原生排程。若额度已耗尽且尚未成功排程，无法保证自动唤醒；后续额度周期仍需正确重新排程。完整的额度耗尽、刷新、唤醒与发送周期尚未验证。

只读检测不调用模型，建立排程、恢复检查和任务执行使用正常额度。客户端、插件服务及设备需要保持可用。

## English

Local Codex / ChatGPT Work task continuation management for Windows. Includes configurable thresholds, reset delays, exact multiline prompts, local target selection and an automatic Chinese/English UI.

Download the release ZIP and checksum file, then use the bundled installer or local-assistant installation prompt. Existing user data is preserved.

A native recovery schedule must be established in advance. Automatic wake is not guaranteed without it; later quota windows require correct rescheduling. A complete real exhaustion, reset, wake and send cycle remains unverified. Read-only checks do not call the model; scheduling, recovery and continued work use normal quota.

版本 / Version: 1.0 · 作者 / Author: 果果 · MIT
