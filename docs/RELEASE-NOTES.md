# CASE 4.2.1 独立维护版 / Independent edition

基于 Patryk Starostka 的 CASE 4.1.0 开发，保留 MIT 许可；详见 NOTICE.md。
本仓库独立维护，更新检查仅指向 dundunge/claude-account-switcher-engine。

- 中文 / English 即时切换，并保留选择。
- 分别显示 5 小时、周额度的已用比例、来源和采样时间。
- 直接读取已打开账号的 Claude 原生额度面板；各账号只在 Claude 中登录一次。
- 首次使用需允许 CASE 辅助功能权限。未打开账号显示上次读数和时间，不自动切号。
- 显示读取时的原始重置提示，不推算服务器刷新时间或倒计时。
- 使用系统界面读取，移除旧的独立 Cookie/API 查询路径；不需要 CodexBar 或 OAuth 额度授权。
- 本地同步引擎 0.3.0 保留父子会话关系，正文由 Claude 自己保存。
- 发布前用 `tools/compatibility_manifest.py` 记录 App、engine、bridge、原生额度模块和 schema 的 SHA-256；新 hook 也固定 bridge hash。

The usage reader observes Claude's native panel through macOS Accessibility. There is no extra account login,
cookie extraction or independent usage API call. Closed accounts retain dated observations. Read time is not
a server-update timestamp. See docs/NATIVE-USAGE.md for support boundaries and acceptance requirements.

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
