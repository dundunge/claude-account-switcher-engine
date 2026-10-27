# 跨平台支持 / Platform support

**本项目主要由 macOS 开发。Windows 和 Linux 包未经真实账号、安装和桌面操作实测，属于实验支持。**
GitHub Actions 的编译、静态检查和模拟测试只验证对应自动化范围。
本项目仅使用 Claude Pro 账号验证过；没有 Max 订阅用于验收。

| 平台 | 发布格式 | 实现与限制 |
| --- | --- | --- |
| macOS Apple Silicon / Intel | universal `.zip` | 保留已使用的 Desktop 集成；CLI 查询及原生额度面板备选；本地临时签名，未公证 |
| Windows x64 | `setup.exe`、便携 `.zip` | 独立 profile、正常关窗、同步事务、官方 CLI ConPTY 查询、独立 Electron 授权；未做真实使用验收 |
| Linux x64 | `.deb`、`.AppImage` | 针对官方 Ubuntu / Debian Desktop Beta；独立 profile、同步、官方 CLI PTY 查询、Electron 授权；未做真实使用验收 |

官方 Linux 客户端说明：[安装 Claude Desktop](https://support.claude.com/en/articles/10065433-installing-claude-desktop)。
官方列出的系统为 Ubuntu 22.04 LTS+、Debian 12+。当前 CASE Linux 构建使用 Ubuntu 22.04；其他发行版不作兼容保证。
Windows 请使用官方独立安装版。Microsoft Store / MSIX 的独立账号目录启动尚未支持。
Windows ARM64、Linux ARM64 本轮不发布原生包。

## 账号目录与系统功能

| 数据 | macOS | Windows | Linux |
| --- | --- | --- | --- |
| CASE 配置 | `~/Library/Application Support/CASE` | `%APPDATA%\CASE` | `$XDG_CONFIG_HOME/CASE`（默认 `~/.config/CASE`） |
| 新建 Desktop profile | Application Support 下 `Claude-*` | `%LOCALAPPDATA%\Claude-*` | 配置目录下 `Claude-*` |
| 同步 hook | `Application Support/ClaudeSessionSync/CASE/hook.json` | `%APPDATA%\ClaudeSessionSync\CASE\hook.json` | `~/.config/ClaudeSessionSync/CASE/hook.json`（遵循 XDG） |

账号目录继续使用 `--user-data-dir` 与独立登录状态；额度查询使用每账号独立的官方 CLI 配置，
前后核对 account/org UUID 的哈希。CASE 不读取 token、Cookie 或钥匙串。
Linux 使用 Ctrl 快捷键、XDG 应用菜单和用户自启动；Wayland 全局快捷键/置前取决于桌面环境。
请求正常关闭或置前 Linux Claude 窗口需要 X11 与 `wmctrl`。Wayland 或无法请求正常关闭时，
请先在 Claude 内退出，再切号。没有强制结束现有 Claude 的后备路径。

## 首次准备

从同一 Release 下载 `local-sync-source.tar.gz` 并解压；以下命令在解压目录运行。
不要把其他用户的账号配置、token、登录目录上传到 GitHub Actions。

1. 安装 CASE 及[官方 Claude Desktop](https://claude.com/download)。在 CASE 添加账号。
2. 正常退出 Claude 后，运行 `python3 tools/prepare_local_sync.py` 查看预览。
   核对后加 `--apply` 才写入 engine / bridge / hook。Windows 用下方独立 Python。
   已有 hook 或 runtime 时拒绝覆盖，需要单独审核升级；不要重跑 Cockpit 首次迁移。
3. 通过 CASE 打开各账号完成官方登录，创建简短 Code 会话，然后退出再切换。
   第一、第二账号尚未产生索引时只启动，不伪造索引；至少两个已初始化分区后才合并。
4. 安装[官方 Claude Code](https://code.claude.com/docs/en/setup) 的原生版本。
   Windows 使用 `%USERPROFILE%\.local\bin\claude.exe`，也支持从 PATH 发现原生 exe。
5. 为额度授权准备 Electron 42.4.0：从 [Electron 官方 Release](https://github.com/electron/electron/releases/tag/v42.4.0)
   下载对应的 `darwin-arm64` / `darwin-x64` / `win32-x64` / `linux-x64` 压缩包。
   显式运行 `python3 tools/prepare_auth_runtime.py /absolute/path/to/electron-v42.4.0-PLATFORM.zip`。
   安装器校验固定 SHA-256，不访问网络、不覆盖已有组件、不关闭 Chromium sandbox。
6. 在 CASE 设置中逐账号“授权独立查询”。官方目录信任与 OAuth 确认由用户完成。
   查询或打开已有账号 CLI 不必重新授权；定时刷新默认关闭，可自定义时间。

### Windows Python / ConPTY

在项目解压目录的 PowerShell 运行（需要已有 Python 3.10+）：

```powershell
python -m venv "$env:APPDATA\CASE\python"
& "$env:APPDATA\CASE\python\Scripts\python.exe" -m pip install -r tools/requirements-windows.txt
& "$env:APPDATA\CASE\python\Scripts\python.exe" tools/prepare_local_sync.py
# 核对预览、正常退出 Claude 后，显式初始化
& "$env:APPDATA\CASE\python\Scripts\python.exe" tools/prepare_local_sync.py --apply
```

之后用同一 Python 运行 `prepare_auth_runtime.py`，再从 CASE 发起授权。
依赖安装访问 PyPI；CLI 安装/授权由官方程序处理。CASE 不修改系统 PATH 或 PowerShell 策略。
ConPTY 读取通过仅当前用户可访问的临时命名管道核对请求成功证据，原始 debug 不落盘。
独立授权使用带随机密钥的 loopback IPC，不依赖系统浏览器的登录状态。

## 同步与更新边界

新同步引擎 / bridge 为 **0.4.0**，保留 schema 1/2、删除标记、父子关系、快照及恢复门槛。
Linux 修正 `renameat2` 的目录描述符常量并使用原子不覆盖改名；Windows 使用不覆盖 rename、
字节锁、重解析点拒绝和目标文件 DACL 保留。Windows 对每个文件执行 flush，但没有 POSIX 目录 fsync 等价保证，
断电后的持久性不宣称与 macOS 完全相同；有未恢复事务时停止继续写入。

已有 macOS 0.3.0 hook 可以继续使用原外部引擎；替换 CASE 应用不会自动替换外部同步引擎。
不要直接覆盖已部署的 engine / bridge；源码 hash 绑定会拒绝混装。先备份再审核升级配置。
Linux 更新通过下载新包/包管理器完成，本轮不提供应用内替换二进制。

## English summary

Developed primarily on macOS. Windows and Linux packages are **experimental and have not been tested
with real accounts, installer interaction or desktop switching**. Automated compilation and synthetic
tests cover only their stated scope. Only Claude Pro has been used for real-account verification.
Use official standalone Claude Desktop on Windows and official Ubuntu/Debian Desktop beta on Linux.
First use needs explicit local-sync initialization, official Claude Code, Python, and a checksum-pinned
Electron authorization runtime. Windows additionally needs pywinpty / timezone dependencies in CASE's
isolated Python environment. No proxy, credential extraction or force termination of existing Claude
processes is used. Linux window management requires X11/wmctrl; quit Claude manually when unavailable.
