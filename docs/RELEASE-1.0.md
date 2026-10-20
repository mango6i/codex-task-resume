# Codex 任务续接 1.0 · 预览版

Windows 本地 Codex / ChatGPT Work 插件，作者：果果。

## 本次更新

- 检测脚本不再向其他工作对话注入插件设置或排程指令，不再拦截任务结束；只保存供控制端处理的记录。
- 修复旧管理页入口升级后无法读取的问题，兼容已发布页面地址，并改用固定入口。
- 高额度且没有候选时明确显示“仅预排检查 · 未触发续接”；低额度记录仍严格使用用户阈值。
- 修复恢复检查提前唤醒的问题：健康额度时提前排程，保存前核对客户端实际触发时间，拒绝早于原始刷新时间加等待时长或误排到次日的检查。
- 对话管理显示实际检查时间，并区分漏检、暂停和已核验；旧排程记录不再冒充当前检查已就绪。
- 修复慢请求拖住对话列表的问题；加载期间保留已保存的选择，读取失败可重试，不再把未加载显示成空列表。
- Tibo 公告关键词可手动填写，默认 reset；支持词语和短语，忽略英文大小写。换词不补发历史帖，保留逐帖逐对话去重。
- 启用被客户端接口阻止时，开关回到真实保存状态；关键词和指令仍可独立保存。
- 独立保存项目检查指令，默认使用 GPT-6 Astra「极高」。
- 实际五小时和每周额度全部恢复 100% 后，停止运行项目并取消本轮未发送检查；初始满额度不误触发。
- 每个功能独占一行，开关位于该行最右侧；支持小窗口、宽屏、深浅色与系统中英文。
- 每台电脑独立验证账号、原生控制通道和模型；不打包用户配置、账号或私人对话。

## 兼容性

公告群发和重置停止依赖客户端开放可验证的原生共享控制通道。并非所有当前桌面版本都支持；接口不可用时拒绝启用新模式，保留原有设置和额度续接功能。模拟接口验证不等于真实客户端端到端验收。

额度与公告检查有已保存的轮询间隔及网络延迟，不保证精确到秒；停止不能退回已消耗额度。新的项目检查会使用正常额度。普通额度续接仍需提前建立原生恢复排程。

## 安装

下载 ZIP 与 SHA256SUMS.txt，解压后运行 install.cmd。需要 Windows、Python 3.10+、Codex CLI 和支持本地插件的客户端。安装成功后重开客户端，按原生提示审核脚本。设置、队列和发送记录保留。

[中文说明](https://github.com/mango6i/codex-task-resume/blob/main/README.md) · [English](https://github.com/mango6i/codex-task-resume/blob/main/README_EN.md)

![设置预览](https://raw.githubusercontent.com/mango6i/codex-task-resume/main/assets/settings-wide.png)

## English

Detector hooks no longer inject setup or scheduling instructions into project chats or block task completion. Only durable records are passed to the controller. Published settings-page addresses remain supported, and the management entrypoint is stable. Healthy quota with no candidates is explicitly shown as a pre-armed check, not triggered continuation.

Fixes early recovery wakes by pre-arming during healthy quota and validating the native actual wake against the original reset plus current delay. Rejects early wakes and accidental next-day recurrences. The panel distinguishes verified, missed and paused checks and shows the actual wake time. This timing protection does not prove a full live quota-recovery cycle.

Fixes chat-list loading behind slow requests. Saved selections remain visible while loading, failed refreshes retain the previous list, and retry is available without treating an unloaded list as empty.

Adds editable Tibo announcement keywords (default reset), clear activation failure feedback, deduplicated project reviews with exact custom instructions and GPT-6 Astra Extra high, and confirmed interruption after both quota windows fully reset. Workflow switches occupy separate trailing columns. Native desktop control and model support are verified per computer; unsupported clients block activation. Existing configuration and ledgers are retained.

The native control workflow is validated with isolated protocol fixtures; live availability depends on the installed desktop client. Quota/profile checks have polling and network latency. A stop cannot refund usage already incurred. Windows, Python 3.10+, Codex CLI and a compatible local plugin client are required.
