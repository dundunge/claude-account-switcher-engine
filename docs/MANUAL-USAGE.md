# 多账号额度查询 / Multi-account usage queries

Windows / Linux 的终端、Python / ConPTY、Electron 组件准备与实测边界见 [CROSS-PLATFORM.md](CROSS-PLATFORM.md)。下方 Terminal 与 Application Support 路径示例以 macOS 为准；原生辅助功能面板读取仅适用于 macOS。

## 默认手动，可选定时刷新

- 主页账号行点击只选中，不触发切号。每个账号最右侧固定显示“切换”按钮，旁边的
  刷新图标仅查询该账号。顶部“刷新全部”直接串行查询全部账号，不再打开额度二级页面；
  查询期间按钮显示忙碌状态，重复点击不叠加查询。批量查询中一个账号失败后继续其余账号，
  最后显示成功/未获取数量。刷新不会切换账号或启动登录。
- “设置 → 额度查询”放置逐账号查询、独立授权、“打开独立 CLI”和查询错误详情。
  打开设置只读取 CASE 当前状态，不发起额度请求；两个窗口按递增状态版本同步读数。
  原有账号右键/底部“操作”菜单和明确的键盘切换快捷键继续可用。
- 每个账号点击“授权独立查询”，Terminal 启动**未修改的官方 Claude Code** 的
  `claude auth login --claudeai`。官方授权链接通过其 `BROWSER` 入口交给 CASE 的独立
  Electron 窗口；请在标题对应的账号窗口中登录并确认授权。之后在 Terminal 完成首次设置和
  `/usage`，然后用 `/exit` 退出。
- 各账号独立窗口使用 `usage-monitor/<hash-of-case-id>/browser/`，不复用系统浏览器或
  Claude Desktop 的 Cookie。重新授权复用本账号的窗口数据；不同账号的数据不混用。
  Electron 是 Chromium 桌面框架，不修改指纹、不绑定代理。若窗口中选错号，account/org 校验
  会拒绝该登录；请在此独立窗口中切换到正确账号后重试。
  CASE 不调用 Cookie API，不导入/导出浏览器数据，不拦截授权码或代替用户批准 OAuth。
  授权 URL 仅在内存和本次私有本地套接字中转，不写入日志、脚本、文件或 Electron 参数。
  同一账号同时只允许一个授权流程，CLI 登录结束后只关闭本次创建的 Electron 子进程组。
- 登录完成后在主页点击该账号的刷新图标、顶部“刷新全部”，或在设置中点击“查询额度”。
  即使 Desktop 中该账号未打开，也可查询。默认关闭定时刷新；页面打开、状态轮询和时间重绘不请求额度。
  在“设置 → 额度查询”可显式开启定时刷新，自定义分钟／小时或选 2 小时、5 小时。
  CASE 运行时由一个后端计时器串行查询；开启或改间隔后不立即查询，错过多轮最多补一轮。
  查询失败不立即重试，不自动发起授权或切号；依赖、身份、失败空值规则与手动查询一致。
  详见[定时刷新说明](SCHEDULED-USAGE.md)。
- 已连接账号始终显示“打开独立 CLI”，直接打开该账号的独立 Terminal 和 `/usage`，
  复用现有登录；“重新授权”另设按钮，均位于设置的额度查询页。额度查询失败后也可直接重新打开 CLI，
  不必先走授权。终端提示显示 CASE 账号名称；普通 Terminal 直接运行 `claude` 仍使用默认配置。
  这个入口校验 account/org，不重新执行 `auth login`，不替换身份绑定，
  也不依赖独立 Electron 授权组件。
- 授权成功仍出现 `CLI_WORKSPACE_TRUST_REQUIRED` 时，表示官方 CLI 尚需首次确认 CASE 的
  空白查询工作目录。点击“打开独立 CLI”，核对目录后由用户确认官方信任提示，
  查看 `/usage` 后输入 `/exit`，再回到 CASE 手动查询。CASE 不代替用户接受信任提示。
  官方登录状态正常不代表 `/usage` 接口可用；通用额度获取错误不会要求用户先重新授权。
- CLI 必须单独安装；CASE 不静默安装。连接失败时会显示官方安装说明入口。
- 独立窗口另需本机 Electron 42.4.0 组件，位置为
  `~/Library/Application Support/CASE/auth-runtime/42.4.0/Electron.app`。
  缺失时明确报 `AUTH_BROWSER_NOT_INSTALLED`，不会退回系统默认浏览器。
  组件使用官方 release 的固定 SHA-256 压缩包，经显式运行
  `python3 tools/prepare_auth_runtime.py /absolute/path/to/electron-v42.4.0-darwin-arm64.zip`
  安装（Intel Mac 使用 `darwin-x64`）。脚本无网络访问，不覆盖已安装组件；只安装公开运行时，
  不读取 Cockpit 或其他应用的登录状态。应用与组件均为本地临时签名。
- 各账号 CLI 配置位于 CASE 自己的数据目录：
  `~/Library/Application Support/CASE/usage-monitor/<hash-of-case-id>/cli/`。
  原有 Desktop `--user-data-dir` 和 `~/.claude` 登录/历史继续使用原有路径。
- CASE 只读取这个新目录的非凭据账号元数据（`.claude.json` 或旧版 `.config.json` 中的
  `oauthAccount.accountUuid`、`organizationUuid`）和官方 `auth status --json`。
  官方 CLI 自己保存、读取和刷新其隔离凭据。CASE 不读取/复制既有 token、Cookie 或钥匙串项目。
- 绑定记录包括 Desktop profile 路径、account/org 的 SHA-256 和绑定修订号。
  查询前后及状态展示时校验身份；不以 email 或全局默认 CLI 登录代替 UUID 校验。
  账号删除、重新授权或 profile/identity 变化后，旧查询结果不发布。

## 查询行为

CLI 在各账号独立的空工作目录和 PTY 中运行，直接将 `/usage` 作为官方启动参数，
不等待首页快捷键提示，也不模拟输入命令或确认键。查询子进程使用官方普通终端渲染，
避免新版全屏终端的启动协商与布局变化影响采集。
继承环境采用允许列表，排除 API keys、gateway
端点、OAuth token、注入的 account UUID、`NODE_OPTIONS` 等认证/执行覆盖项。
在 macOS 上，若 CASE 没有继承显式的 HTTP(S)/ALL_PROXY 环境变量，会读取当前已启用的
系统 HTTP/HTTPS 代理并传给官方 CLI；不修改系统网络设置，不硬编码 Clash 端口。
显式代理环境变量优先，已有 `NO_PROXY` 保留；不执行 PAC，也不读取代理认证凭据。
因此关闭 Clash IPv6 后，使用系统代理的查询不会因 CLI 忽略系统设置而走原生 IPv6 直连。
若只启用 TUN 而未开启系统 HTTP/HTTPS 代理，本机制不会凭空添加代理。
禁用用户 hooks 和 Remote Control，严格排除用户 MCP 配置，允许工具列表为空。
继续设置 `DISABLE_AUTOUPDATER=1`。使用 `DISABLE_TELEMETRY=1`、
`DISABLE_ERROR_REPORTING=1`、`DO_NOT_TRACK=1` 分别关闭遥测与错误上报。
不设置或继承 `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`：官方 CLI 2.1.310 的这个总开关
也会阻止 `/api/oauth/usage`，导致首次打开失败；已存在的官方缓存有时会掩盖该错误。
仅运行 `/usage`，不发送聊天提示词。首次信任/设置提示由用户在可见 Terminal 完成；查询不代为批准提示。

只解析 `Current session`、`Current week (all models)` 对应的已用百分比。
模型单独限额、context 和 insights 百分比不会替代这两个窗口。PTY 屏幕重绘会覆盖旧单元格；
终端私有协议（例如 Kitty 的 `CSI ? u` 键盘能力查询）不会按普通光标保存/恢复命令处理。
加载中、错误、冲突或未知布局不会被猜成成功。数据来源显示为“官方 CLI”。
后台采集使用临时私有 FIFO 接收官方 `--debug-file` 输出，仅在内存识别固定的请求成功和缓存
标记。原始终端/debug 不写磁盘，也不返回 UI。必须同时有完整读数和官方 HTTP 200 证据，
或官方明确采用不足 60 秒的同账号快照；短期快照沿用元数据中的原始采样时间，不能显示成刚查询。
请求失败后回退到旧百分比、HTTP 200 但内容无效、来源不明或错号缓存都返回失败空值。
额度采用 Claude 风格的两行布局：5 小时额度、每周 · 所有模型。每行显示重置提示、
已用百分比和细进度条；悬停百分比可见剩余比例，悬停重置提示可见完整日期、时区及原文。
5 小时优先显示“3 小时 38 分钟后重置”，周额度显示“周二 20:00 重置”。
完整日期使用本机时区和重置当日的 UTC 偏移，正确处理跨天、跨年及夏令时变化。

“Resets in …”以本次观察时间为基准换算，明确标为“预计”；页面重绘不会把目标时间往后推。
官方明确给出的日期、时钟或星期按对应时区解析，时钟/星期缺失的日期只在对应额度窗口内推定。
未知格式、时区不明、夏令时歧义、超出窗口或未返回重置时间时显示“重置时间未获取”，不补造日期。
相应窗口的使用量缺失时也不借用另一个窗口的重置时间。重置时间已过后提示手动查询，
不会自动把已用比例改成 0%。原始提示仍保留；采样时间不代表服务器内部刷新时间。

Helper 的整个查询期限为 45 秒（其中登录状态检查至多 8 秒），Rust 外层限制为 55 秒，
输出收集另有限时。各账号防止并发重入；本次失败不立即重试，已启用的定时任务可在下周期再次尝试。
只回收本次新建的查询子进程组，
不检查或结束既有 Claude 客户端。CASE 不保存原始终端输出、登录输出或会话正文。

## 状态与当前值

`usage_refresh` 保持六种状态：

| 状态 | 含义 |
| --- | --- |
| `success` | 本次查询/显式本地读取成功 |
| `auth_failed` | 未授权、登录失效或 account/org 不匹配 |
| `unavailable` | 依赖缺失、官方服务/界面不可用、限流或未识别 |
| `in_progress` | 该账号已有查询正在执行 |
| `failed` | 内部、保存或通信错误 |
| `timeout` | 查询超过明确的期限 |

每次失败都返回 `usage: null`。当前状态由 Rust 统一保存；账号列表轮询不会重新读缓存并覆盖失败。
递增的状态版本防止较慢的旧状态响应覆盖新失败。通信异常不会一律标成 timeout。
没有独立查询绑定时，仍允许用户明确点击“重读本地记录”；历史缓存本身不因失败删除，
但不能被后台自动恢复为当前值。CLI 查询结果仅在本次 CASE 进程内保存；重启后需重新查询，
可手动点击，也可等待已启用的定时任务。

## 保留的原生面板路径

尚未连接独立查询的账号可“从面板读取”。macOS Accessibility 识别指定 Desktop profile 的
进程和 Usage 子树，必要时短暂开关该弹窗。只读当前会话与 Weekly · all models；缺失/歧义时返回错误。
原生观察缓存仍绑定 profile/account/org，保存在 `CASE/native-usage/`，权限 0600。
关闭的账号不会为了读取面板而被打开。首次使用此路径需要用户授予 CASE 辅助功能权限。

## 离线验证与真实验收

离线测试使用临时目录和模拟 CLI，覆盖三账号隔离、auth/account/org 检查、环境隔离、
首次启动隐私开关、请求失败回退缓存的拒绝、短期快照原始时间和私有 FIFO 清理、
屏幕重绘、错误优先于旧数字、超时回收、迟到结果丢弃及失败不回填，
以及两个窗口独立重置、跨天/跨年/夏令时、估算标记和缺失时间。
交互测试覆盖账号行单击/双击仅选中、固定切换按钮、逐账号刷新、顶部直接批量刷新、
按钮上的 Enter 不触发全局切号、批量串行/重复点击/单号失败不中止其余查询，
以及没有后端快照的失败不被旧状态轮询回填。
这些测试不读取真实凭据、不启动登录；真实账号验收单独记录。

2026-10-25（America/Los_Angeles）本机验证共 214 项通过。候选版及最终安装版
三个独立授权账号的手动查询均成功；两个额度窗口显示各自使用量和重置时间。
候选版核对了行选择、单号刷新仅更新目标账号、全号串行刷新及设置中的独立 CLI 入口；
最终安装版再次确认了全号查询成功和设置管理入口。独立 CLI 复用既有登录，
新进程工作目录匹配目标账号，账号列表、hook 和绑定文件哈希未变化。
本轮没有通过真实账号制造错号或网络故障，也没有执行真实 Desktop 切号；
这些约束以离线测试验证，不能描述为真实故障注入已通过。
本地部署及验收证据保存在 `out/`，不提交账号文件或私人截图；
审计边界见 [GROK-AUDIT-20261025.md](GROK-AUDIT-20261025.md)。

官方参考：

- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/settings
- https://code.claude.com/docs/en/legal-and-compliance
- https://code.claude.com/docs/en/costs
