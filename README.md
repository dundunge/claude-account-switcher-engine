# CASE 独立维护版

[English](README.en.md)

基于 [Patryk Starostka 的 CASE](https://github.com/pstarostka/claude-account-switcher-engine)
4.1.0 开发，保留原 MIT 版权声明。来源和基线见 [NOTICE.md](NOTICE.md)。

本仓库独立维护：**更新检查、源码和发行页面只指向
[dundunge/claude-account-switcher-engine](https://github.com/dundunge/claude-account-switcher-engine)**，
不会自动合并上游代码。

CASE 面向习惯使用**官方 Claude Desktop**的多账号用户：桌面端负责聊天和工作，
CASE 负责本机账号切换、会话索引同步，以及手动或可选的定时额度查询。**不使用反向代理或模型 API 中转。**

**目前仅适配并验证 Claude Pro。** 维护者没有 Max 订阅号，尚未对 Max 做适配和验证。

![CASE 中文主页演示：三个账号、切换按钮与两档额度](docs/media/case-overview-zh.jpg)

*模拟演示，账号与额度均为虚构数据；界面复用本项目实际 UI。*
查看[全部截图与发帖配图](docs/SCREENSHOTS.md)，或[在本地打开模拟网页版](docs/demo/README.md)。

## 当前功能

- 为各个 Claude Desktop 账号使用独立的官方登录目录。
- 主页保留紧凑账号列表：点击账号行只选中；每行右侧固定的“切换”按钮执行切号，
  旁边刷新图标只查询这个账号；顶部“刷新全部”串行查询全部账号，一个失败后继续其余账号。
  账号右键菜单、底部“操作”和明确的键盘切换快捷键继续可用。
- 设置 → 通用中切换中文和 English；主窗口、设置、菜单随即切换，重启后保留。
- 主页各账号采用 Claude 风格的两行细进度条：5 小时额度、每周 · 所有模型，
  分别显示重置提示和**已用比例**，并注明采样时间和来源。
  悬停可见完整重置日期、时区及剩余比例；相对提示标为“预计”，缺失时间明确显示未获取。
- 设置 → 额度查询集中放置单号详细查询、授权、重新授权、打开独立 CLI 和错误详情。
  “授权独立查询”在 Terminal 中运行官方 Claude Code 登录，官方登录链接打开各账号独立的
  Electron 授权窗口，不复用系统浏览器或 Desktop 的登录状态。
  每个账号使用独立的 CLI 配置目录，
  登录的 account/org 必须与 Desktop profile 匹配。CASE 不读取 Cookie、token 或钥匙串凭据。
- 已授权账号的“打开独立 CLI”复用本账号登录，不要求先重新授权；
  普通 Terminal 直接运行 `claude` 仍使用其默认配置。
- 单号刷新、“刷新全部”或设置中的“查询额度”运行官方 CLI 的 `/usage`，未打开的账号也可查询。
  默认只在点击时查询。不发送模型消息，不自动切号。
  失败返回 `usage: null`；后续状态刷新不会把旧百分比填回来。
- 设置 → 额度查询可开启**定时刷新**，默认关闭。支持自定义分钟／小时间隔（5 分钟至 7 天，
  精确到整分钟），提供 2 小时和 5 小时快捷按钮，并显示下次刷新时间。
  仅在 CASE 运行期间串行查询已授权账号；休眠或退出错过多轮最多补一轮，失败不立即重试。
  开启或修改间隔后从现在重新计时；关闭后当前查询收尾，其余账号停止。详见[定时刷新说明](docs/SCHEDULED-USAGE.md)。
- 未连接独立查询时，“从面板读取”仍可观察已打开账号的 Claude 面板。
  首次使用此方式需允许 CASE 辅助功能权限；读取可能短暂开关额度弹窗。
  重置提示保留读取时的原文；推算时间固定在原始观察时刻，不因页面刷新而往后移动。
- “重读本地记录”读取 Claude 的 `plan-usage-history.json` 与 CASE 上次面板读数。
  这是显式的历史查看操作；文件可能长时间未更新，原始时间保留，缺失值显示未知。
- 独立本地同步引擎合并 Claude Code Desktop 会话索引，保留父子关系；正文继续由 Claude 自己保存。
- 切号时显示“等待退出 → 校验 → 同步 → 启动”阶段；中断会保留快照编号，下一次先提示恢复。

不修改 Claude 程序包，不上传会话正文，不迁移云端聊天，不重置或增加账号额度。
本地索引同步也不能把一个账号的模型上下文缓存转给另一个账号；长任务宜使用本地交接文件。

多账号查询与授权见 [docs/MANUAL-USAGE.md](docs/MANUAL-USAGE.md)，面板读取见
[docs/NATIVE-USAGE.md](docs/NATIVE-USAGE.md)。不需要安装 CodexBar。

## 不使用反代：保留官方客户端与登录流程

CASE 不提供或注入反向代理、模型 API 网关或请求转发服务，不改写 Claude 的 API 地址，
不抓取或复制 Desktop 的 Cookie、token、钥匙串凭据。桌面端继续使用官方 Claude Desktop；
额度授权和 `/usage` 由未修改的官方 Claude Code 完成，凭据由官方 CLI 自行保存和刷新。
CASE 只读取非凭据身份元数据、官方登录状态和额度输出。

Electron 仅提供每个账号隔离的官方登录窗口，是 Chromium 桌面框架；
这里不做指纹伪装、代理绑定、页面脚本注入或授权码拦截，也不代替用户确认 OAuth 或目录信任。
OAuth 本地回调和凭据兑换由官方 CLI 处理。

“不使用反代”指 CASE 自身不建立上述中转链路。官方客户端仍可能使用用户已有的系统代理或
代理环境变量；CASE 不修改、重载或重启 Clash/Mihomo。

## 为什么切号与额度查询分开

| 路径 | 登录和数据范围 | 触发与影响 |
| --- | --- | --- |
| Desktop 切号 | 各账号独立 `--user-data-dir`，使用官方 Desktop 登录状态 | 明确点击“切换”；正常退出 Claude，校验并同步本地索引，再启动目标账号 |
| 额度授权 | 各账号独立 CLI 配置、空白 workspace 和 Electron 登录目录 | 在设置中单独授权；校验 account/org 与目标 Desktop profile 一致 |
| 额度查询 | 已绑定账号的官方 CLI `/usage` | 手动刷新单号或全部，或显式开启定时刷新；不切号、不退出当前 Desktop、不自动发起登录 |

分开有四个原因：

1. **登录状态不同。** Desktop 已登录不等于独立 CLI 已授权。每个账号需要单独完成一次
   官方额度授权及首次设置，不能通过复制 Desktop 凭据来跳过，也不修改默认 CLI 的登录。
2. **查询不打断工作。** 未打开的账号也能查额度，无需切离正在使用的 Desktop 账号。
3. **失败不触发切号。** 网络或额度接口失败只清空本次读数并报告原因；重查、打开 CLI、
   重新授权和切号是独立操作。已授权账号可直接打开自己的 CLI 排查，无需重新走授权。
4. **避免串号和旧值误报。** 结果绑定 profile、account/org 和授权修订号，查询前后均校验。
   后台采集还检查官方请求成功证据；官方失败后回退的旧百分比不能算新查询成功。
   不足 60 秒的官方同账号短快照保留原始采样时间，不伪装成新读数。

`usage_refresh` 明确返回 `success`、`auth_failed`、`unavailable`、`in_progress`、`failed`、
`timeout`。失败始终返回 `usage: null`，后续状态轮询不会恢复旧百分比。
默认只在点击时查询；显式开启定时刷新后由一个后端计时器执行。本地状态轮询和时间重绘不请求额度。

## 验证状态

2026-10-26（America/Los_Angeles）定时刷新版本本地离线验证：Python 120、Rust 单元 76 + bridge 3、
Node 38，共 **237 项通过**；Clippy 通过。新增测试使用模拟查询，未开启真实账号定时刷新；
本轮源码与文档保留在本地，未上传 GitHub。功能边界与复核入口见[定时刷新说明](docs/SCHEDULED-USAGE.md)。

2026-10-25 的审计修复版本离线验证共 **220 项通过**；格式、Clippy 和 JavaScript 语法检查通过。
Grok 对前一版审计包核对了 77 项文件哈希，并重跑 15 项 Node 测试，提出三项问题。
当前源码已修复面板锁冲突的错误状态、后台下载提示导致的额度误判、跨窗口查询中的批量统计问题；
修复尚待独立复核。详见[审计修复记录](docs/AUDIT-FOLLOWUP-20261025.md)。

此前 `103dd9f` 对应版本完成了 **214 项**验证及本机构建安装，
在安装版中手动查询三个独立授权账号成功，显示两项使用量及各自重置时间。
行选择、单号刷新、全部刷新、设置管理入口和复用授权打开独立 CLI 已核对；
该轮没有执行真实 Desktop 切号，其事件路由以离线测试覆盖。
上述审计修复与本轮定时刷新均未替换本机安装，模拟网页版与截图也不作为真实账号验收。

实现者测试与独立审计的范围分别记录，不保证之后每次官方请求成功。
历史审计范围及复验入口见 [docs/GROK-AUDIT-20261025.md](docs/GROK-AUDIT-20261025.md)。
本次更新源码和文档，未创建新的 GitHub Release。

## 使用与更新

本次定制版的完整集成以 **macOS** 为目标。Windows 保留原项目代码编译检查，尚未完成同步和额度刷新集成，
当前发行工作流不发布 Windows 安装包。

已有本地同步安装：只更新 `~/Applications/CASE.app`；保留 `CASE/accounts.json`、
`ClaudeSessionSync` 和所有 `Claude-*` 登录目录。正常退出 CASE 后替换应用即可；
不要为了更新 CASE 强行结束 Claude。

同步引擎单独安装，应用内只检查更新和打开发行说明，不执行自动替换。
发行说明必须同时说明应用与同步引擎版本，避免新应用搭配不兼容引擎。

[自有发行页面](https://github.com/dundunge/claude-account-switcher-engine/releases)

## 从源码构建

在仓库目录的 macOS Terminal / zsh 中，安装 Rust、Tauri CLI 和 Xcode Command Line Tools 后：

```zsh
cargo install tauri-cli --version '^2' --locked
cargo tauri build --bundles app --ci
```

产物为 `target/release/bundle/macos/CASE.app`。本仓库构建始终启用定制版更新保护。
应用使用临时签名，未公证；请通过 macOS 的正常安全提示处理首次打开。

验证源码：

```zsh
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
python3 -m unittest discover -s tests/python -q
node --test tests/*.test.cjs
```

## 同步安装边界

同步代码与离线合成测试已一并纳入仓库：

- `engine/claude_session_sync.py`：0.3.0 索引同步、快照和恢复。
- `tools/case_sync_bridge.py`：读取 CASE 账号列表、检查账号分区、串行执行切换。
- `tools/migrate_cockpit_to_case.py`：**已有 Cockpit 同步配置的首次迁移工具**。
  默认只预览，需要已有且经过审核的 `ClaudeSessionSync/config.json`。
  `--apply` 会建立两个空白账号目录并安装 hook；拒绝覆盖现有 CASE 或账号目录。
  已迁移的用户**不要重复运行**。

应用启动其他账号前必须有有效的 `ClaudeSessionSync/CASE/hook.json`；缺失或无效时停止打开，
避免假装已经同步。全新安装需要先审核并准备同步配置，本版本没有通用的一键初始化器。

同步必须在 Claude 全部正常退出后写入索引。正常切号时，现有 bridge 会请求 Claude 正常退出；
无法退出或存在未恢复的事务时停止操作，不强制终止。实际工作中请先保存并结束任务。
详细边界见 [docs/LOCAL-SYNC.md](docs/LOCAL-SYNC.md)。

## 许可

[MIT](LICENSE)。原项目版权归 Patryk Starostka；独立维护版修改归 dundunge。
本项目与 Anthropic 无隶属关系。
