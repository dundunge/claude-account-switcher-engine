# CASE 独立维护版 / Independent edition

基于 Patryk Starostka 的 CASE 4.1.0 开发，保留 MIT 许可；详见 NOTICE.md。
本仓库独立维护，更新检查仅指向 dundunge/claude-account-switcher-engine。

- 中文 / English 即时切换，并保留选择。
- 分别显示 5 小时、周额度的已用比例、来源和采样时间。
- 刷新本地记录；可手动尝试官方查询，失败保留旧数据。
- 本地同步引擎 0.3.0 保留父子会话关系，正文由 Claude 自己保存。

官方查询可能要求浏览器验证；当前实际查询遇到该限制，实时刷新未验收成功。
请在 Claude 的设置 → 用量中查看当前官方值。

## 已部署用户升级 / Existing installations

核对 zip 的 SHA-256，正常退出 CASE 后替换 `~/Applications/CASE.app`。
当前外置引擎仍为 0.3.0，已部署用户无需重跑迁移工具。
保留 CASE 配置、ClaudeSessionSync 和所有账号登录目录。
未公证的首次安装请使用正常的 macOS 安全提示。

Verify the published SHA-256, quit CASE normally, then replace the application.
Keep existing profiles, account lists and sync configuration. The external sync engine
remains at 0.3.0; already migrated users must not rerun the initial migration tool.
The app is ad-hoc signed, not notarized. Only macOS integration is released.

New installations require a reviewed sync configuration. See README.md and docs/LOCAL-SYNC.md.
The source archive is for review and rebuilding; it contains no account configuration or credentials.
