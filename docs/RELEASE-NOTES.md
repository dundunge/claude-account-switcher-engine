# CASE 4.2.1 独立维护版 / Independent edition

## 2026-10-25 源码更新：多账号手动查询与主页操作

增加各账号独立官方 CLI 授权和 `/usage` 查询，支持未打开的账号；仅手动点击，无定时查询或自动重试。
查询失败返回 null，状态刷新不回填旧百分比。Desktop 切号和同步路径保留。
额度改为 Claude 风格：两行细进度条分别展示 5 小时和每周 · 所有模型，
右侧为重置提示和已用百分比；悬停可查看完整日期、时区和剩余比例。
相对提示标为预计；未知重置时间不猜值，时间已过提示手动查询。
主页账号行点击只选中；每行固定“切换”和单号刷新按钮，顶部“刷新全部”直接串行查询全部账号。
授权、重新授权、独立 CLI 和详细单号管理位于设置的额度查询页；已授权 CLI 直接复用登录。
首次 `/usage` 失败已修复：不再设置会阻止官方额度接口的
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`；同时拒绝请求失败后回退的旧百分比。

需要单独安装官方 Claude Code、逐个授权并由用户完成首次设置。当前已构建、本机安装，
最终安装版三个账号手动查询成功；214 项离线测试通过，独立审计待进行。
本轮没有执行真实 Desktop 切号；本地安装验证不等于 GitHub 安装包发布。本次没有创建新 Release。
CASE 不使用反代；Desktop 切号与独立官方 CLI 查询分开的原因见 [README](../README.md)。
下方为既有 4.2.1 的面板读取说明；新行为和边界见 [MANUAL-USAGE.md](MANUAL-USAGE.md)。

## 既有 4.2.1 说明

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
