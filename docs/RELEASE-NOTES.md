# CASE 4.3.0

**主要在 macOS 开发和使用；Windows、Linux 包未经真实账号、安装交互和桌面切号实测，属于实验支持。**
自动化构建与模拟测试不等于真实使用验收。仅使用 Claude Pro 验证过，没有 Max 订阅用于测试。

## 下载

| 系统 | 文件 |
| --- | --- |
| macOS Apple Silicon / Intel | `CASE-4.3.0-mac-universal.zip` |
| Windows x64 | `CASE-4.3.0-win-x64-setup.exe`，或便携 `CASE-4.3.0-win-x64.zip` |
| Linux x64 | `CASE-4.3.0-linux-x64.deb`，或 `CASE-4.3.0-linux-x64.AppImage` |
| 同步引擎、授权组件准备工具和说明 | `CASE-4.3.0-local-sync-source.tar.gz` |

每个文件均提供同名 `.sha256` 校验文件。macOS 为本地临时签名，未公证；Windows 未进行发行者签名。
首次打开请按系统正常安全提示处理。Linux 针对官方 Ubuntu / Debian Claude Desktop Beta，
Windows 需官方独立安装版，Microsoft Store / MSIX 的独立 profile 尚不支持。

## 本次内容

- 多账号独立 `--user-data-dir`，主页固定“切换”与单号刷新，顶部串行刷新全部账号。
- 官方 Claude Code `/usage` 独立账号查询，关闭的 Desktop 账号也能查；
  账号 / org 绑定、独立 Electron 授权窗口与直接打开已授权账号 CLI。
- 分别显示 5 小时、每周所有模型的已用比例与重置时间，未知不补造。
- 可选定时刷新，默认关闭；支持自定义 5 分钟至 7 天、2 小时 / 5 小时快捷设置。
- 所有失败返回 `usage: null`，保留六种明确状态，不显示旧百分比冒充当前值。
- Linux 增加官方 Desktop 启动、XDG 路径、应用菜单、自启动、Ctrl 快捷键及 PTY 查询接入。
- Windows 增加 ConPTY 查询、独立授权 IPC、Python 依赖路径和同步文件系统适配；
  移除现有 Desktop 正常退出失败后强制终止的后备行为。
- 同步 engine / bridge 0.4.0：跨平台锁、路径检查、快照恢复、Linux 原子改名和 Windows 目标 DACL 保留。
  会话正文仍由 Claude 自己保存；同步不迁移服务端上下文缓存或账号权限。

## 首次安装与升级

安装 CASE 应用后仍需显式准备同步 hook、官方 Claude Code 和校验固定 hash 的 Electron 运行时。
从本次 `local-sync-source.tar.gz` 解压的目录按 `docs/CROSS-PLATFORM.md` 操作。
新初始化工具先预览，`--apply` 才写入；已有配置拒绝覆盖。
Windows 额度查询还需独立 Python 环境中的 pywinpty 和时区依赖。
OAuth 与官方目录信任提示由用户本人确认，不在 CI 使用任何真实账号或凭据。

已有 macOS 0.3.0 外部 hook 可继续配合新 App 使用。替换应用不会自动升级外部 engine / bridge；
不要直接覆盖 hash 绑定的脚本，也不要重跑初次 Cockpit 迁移。
要升级外部同步环境，先保留应用、engine、bridge、hook 的备份，再单独审核配置和快照状态。

Linux 的窗口置前和请求正常退出需要 X11 / wmctrl；Wayland 或无法关闭时请先自行退出 Claude。
Windows 文件已 flush，但没有 POSIX 目录 fsync 等价保证，不能据此承诺断电持久性完全相同。
macOS 保留原生辅助功能读取额度面板的备选机制；其他平台使用独立官方 CLI 查询。

CASE 不使用反向代理、不读取已有 token / Cookie / 钥匙串，也不重启或重载 Clash / Mihomo。
Desktop 切号与额度查询分开：查询不会为了读取额度而退出或切换正在使用的 Desktop 账号。
详见仓库 README、跨平台说明、同步与额度查询文档。

## English

Developed primarily on macOS. **Windows and Linux packages are experimental and have not been
tested with real accounts, interactive installation or desktop account switching.** Automated
builds and synthetic tests do not establish real-world compatibility. Only Claude Pro has been used
for real-account verification; Max has not been tested.

This release adds platform adapters, Windows ConPTY usage queries, Linux PTY integration and
cross-platform local-sync primitives. Install the official standalone Claude Desktop on Windows,
or official Ubuntu/Debian Desktop beta on Linux. First use needs explicit sync initialization,
official Claude Code, Python and a checksum-pinned Electron runtime; Windows also needs the documented
ConPTY dependencies. Existing macOS 0.3.0 hooks remain compatible; external engine upgrades are manual.
Linux window control requires X11/wmctrl; otherwise quit Claude normally before switching.
No proxy or existing credential extraction is used. Each asset has its own SHA-256 file.
