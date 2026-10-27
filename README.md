# CASE 独立维护版

[English](README.en.md)

基于 [Patryk Starostka 的 CASE](https://github.com/pstarostka/claude-account-switcher-engine) 4.1.0 开发，保留原项目 MIT 许可证。原始基线与版权致谢详见 [NOTICE.md](NOTICE.md)。

本项目为**个人独立维护分支**：代码、更新检查与版本发布均固定指向 **[dundunge/claude-account-switcher-engine](https://github.com/dundunge/claude-account-switcher-engine)**，不会自动合并上游代码。

---

### 做这个工具的初衷

如果你手头有多个 Claude 账号，而且习惯日常在**官方 Claude Desktop** 原生应用里干活，你大概深有体会：反复登出登入太折腾，手动管理不同账号的本地会话也很麻烦。我不习惯用 CLI 聊天，也不想继续投入账号去测试反代方案，所以选择做一个本地切换工具。

**CASE 就是为了解决这个痛点而生：**
桌面端继续使用官方 Claude Desktop，CASE 在本地帮你处理三件事——**多账号切换**、**跨账号本地会话索引同步**，以及**额度查询（手动或可选定时）**。

> **核心原则：** CASE 不搭建反向代理或模型 API 中转，不修改 Claude 程序包，保留官方客户端与登录流程。
>
> ⚠️ **关于订阅档位：** 目前仅适配并验证 **Claude Pro**。维护者没有 Max 订阅号，尚未适配或验证 Max。

![CASE 中文主页演示：三个账号、切换按钮与两档额度](docs/media/case-overview-zh.jpg)

*（注：上图为模拟演示，账号名与额度均为虚构数据；演示复用本项目实际 UI，不作为真实账号验收。）*

想了解更多细节？欢迎查看 [全部界面截图与演示配图](docs/SCREENSHOTS.md)，或者在本地浏览器直接体验 [模拟网页版交互](docs/demo/README.md)。

---

## 核心特性一览

- 🔒 **独立账号登录目录**：为每个 Claude Desktop 账号配置独立的官方登录数据目录（`--user-data-dir`），分别保存登录状态；本地会话索引按同步配置共享。
- ⚡ **主界面简洁顺手**：
  - 点击账号整行仅作选中，右侧固定有明确的“切换”按钮，误触率更低。
  - 单号旁边带有独立刷新图标，想看哪个查哪个；顶部提供“刷新全部”串行查询，即便某一个账号查询超时或出错，也不会卡死其余账号。
  - 右键快捷菜单、底部操作栏以及键盘快捷键均已备齐。
- 🌐 **中英文支持**：在「设置 → 通用」中切换中文与 English，主界面、设置与系统原生菜单即时生效，重启后保留偏好；账号名称等用户数据不参与翻译。
- 📊 **Claude 原生风格进度条**：
  - 显示两档用量：**5 小时周期额度** 与 **每周周期（所有模型）**。
  - 展示各自的重置提示与**已用比例**，标明采样时间与来源。
  - 鼠标悬停可查看完整重置日期、时区与剩余比例；推算时间标注“预计”，缺失或无法识别时明确显示“未获取”。
- 🔑 **独立的额度查询通道**：
  - 「设置 → 额度查询」集中放置单号查询、授权、重新授权、打开独立 CLI 及错误详情。
  - 授权流程借助官方 Claude Code 在独立终端中登录，并在各账号完全隔离的 Electron 窗口中完成官方登录跳转。**绝不复用系统主浏览器或 Desktop 的登录 Cookie/Token**，CASE 自身也从不接触系统钥匙串凭据。
  - 每个账号拥有专属的 CLI 配置目录，登录账号/Org 与 Desktop Profile 严格双向校验。
- 💻 **一键打开账号独立 CLI**：已完成绑定的账号可在 Terminal 中打开自己的 CLI 环境并复用现有登录；普通终端运行 `claude` 仍使用默认配置。官方 Claude Code 需单独安装，独立授权窗口还需准备 Electron 组件；首次目录信任由用户本人确认，详见[授权指南](docs/MANUAL-USAGE.md)。
- ⏱️ **省心的定时刷新（可选开启）**：
  - 默认保持关闭，只在你主动点击时查询。
  - 支持按分钟或小时自定义 5 分钟至 7 天的间隔，精确到整分钟；提供 2 小时、5 小时快捷按钮，并显示下次刷新时间。
  - 仅在 CASE 运行期间执行。电脑唤醒或重新打开 CASE 后，错过多轮最多补一轮；账号串行查询，失败返回空读数，本轮不立即重试。开启或修改间隔后重新计时，关闭后当前查询收尾，其余账号停止。详情见[定时刷新说明](docs/SCHEDULED-USAGE.md)。
- 👁️ **备选机制：桌面面板读取**：未配置独立 CLI 授权时，可通过 macOS 辅助功能读取当前已打开账号的 Claude 额度面板；可能短暂开关弹窗。原始重置提示保留，推算时间以采样时刻为基准，不随界面重绘往后移动。
- 📜 **本地历史回溯**：一键重读 Claude 本地生成的 `plan-usage-history.json` 及 CASE 历史快照，用于本地排查和记录对照。
- 🔄 **本地会话索引同步**：独立的本地同步引擎合并已配置账号的 Claude Code Desktop 会话索引，保留可迁移的父子关系；会话正文仍由 Claude 自己保存，索引同步不复制正文。
- 🛡️ **切号阶段与恢复提示**：依次显示“等待退出 → 校验 → 同步 → 启动”；中断时保留快照或事务编号，检测到未恢复事务后停止继续写入并提示处理。

> 📌 **功能边界说明：**
>
> CASE 不修改 Claude 官方二进制文件，不上传聊天记录，不迁移云端对话，也不重置或增加额度。
>
> 本地索引同步**无法**跨账号迁移服务端的模型上下文缓存（Context Cache）；长任务可使用本地交接文档传递上下文。
>
> 查阅详细说明：[多账号授权与查询指南](docs/MANUAL-USAGE.md) ｜ [面板抓取边界与权限](docs/NATIVE-USAGE.md)（无需安装 CodexBar）。

---

## 为什么坚决不碰反代？

CASE 从架构设计上就明确排除了反向代理、API 网关中转或请求拦截篡改等方案：

1. **保留官方客户端**：继续使用官方 Claude Desktop；CASE 不改写其 API 地址或修改程序包。
2. **不提取既有凭据**：CASE 不读取、导出或复制 Desktop／系统浏览器的 Cookie、Token 或系统钥匙串凭据。官方 CLI 和独立 Electron 登录窗口自行保存本账号的授权数据。
3. **由官方 CLI 查询**：独立查询使用未修改的 Claude Code `/usage`，凭据由官方程序维护和刷新。CASE 读取非凭据身份元数据、官方登录状态和额度输出；不发送模型聊天提示词。
4. **独立 Electron 登录窗口**：每个账号使用独立且持久保存的 Chromium 登录目录，重新授权时复用本账号数据；不是无痕窗口。CASE 不伪装指纹、不为账号绑定代理、不注入页面脚本或拦截授权码。OAuth 确认和目录信任由用户完成，回调与凭据兑换由官方 CLI 处理。

*(注：“不使用反代”是指 CASE 本身不搭建任何中转中间层；官方客户端仍会正常遵从你自己电脑上的系统代理或环境变量，CASE 不会改动或重启你的 Clash/Mihomo。)*

---

## 为什么切号与额度查询要解耦？

切换 Desktop 账号与查询额度使用不同的登录状态，因此分别管理：

| 操作维度 | 账号与数据范围 | 触发方式与运行影响 |
| :--- | :--- | :--- |
| **Desktop 切号** | 每个账号专属的 `--user-data-dir` 独立目录 | 用户主动点击“切换”；请求正常退出 Claude，校验并同步本地索引，再启动目标账号 |
| **额度授权** | 每个账号隔离的 CLI 配置目录、空白工作区与 Electron 登录环境 | 在设置中单次显式授权；严格校验当前绑定的 Account / Org UUID 与 Desktop 保持一致 |
| **额度查询** | 调用已绑定账号作用域内的官方 CLI 执行 `/usage` | 手动刷新单号或全部，或显式开启定时刷新；**不切号、不退出当前 Desktop、不自动发起登录** |

**分开设计的四个核心考量：**

1. **登录状态不同**：Desktop 已登录不等于独立 CLI 已授权。各账号需要单独完成一次官方授权与首次设置，不能通过复制 Desktop 凭据来跳过，也不修改默认 CLI 的登录。
2. **查额度不应打断正在写代码的你**：你在账号 A 上干活写正起劲，想看看后台账号 B 额度满没满，点一下单号刷新就能查，完全不必退出当前的 A 账号。
3. **查询失败独立处理**：网络或服务错误只清空本次读数并报告原因；重查、打开账号 CLI、重新授权和切号是独立操作。查询失败也可直接打开已绑定账号的 CLI 排查，无需先重新授权。
4. **检查身份与数据时效**：结果绑定 Desktop profile 路径、account/org 的 SHA-256 和绑定修订号，查询前后均校验。后台还检查官方请求成功证据，拒绝失败后回退的旧百分比；不足 60 秒的官方同账号快照保留原始采样时间。

查询明确区分 `success`、`auth_failed`、`unavailable`、`in_progress`、`failed`、`timeout`。
失败返回 `usage: null`，后续状态轮询不会填回旧百分比。本地状态轮询和时间重绘不请求额度。

---

## 测试与代码验证

离线测试、安装版检查和独立审计分别记录：

- **定时刷新版本离线验证（2026-10-26）**：**237 项测试通过**（Python 120、Rust 单元 76 + bridge 3、Node 38），Clippy 通过。新增测试使用模拟环境；模拟网页版验证界面操作，不运行真实定时 worker。该版本已在本机替换应用并检查设置入口，尚未完成真实账号的定时到期查询验收。
- **Grok 审计与修复跟踪（2026-10-25）**：Grok 对此前审计包核对 77 项文件哈希，重跑 15 项 Node 测试；没有重跑全部测试或验证真实账号。实现者已修复面板锁状态、后台活动解析和跨窗口批量统计三项问题，**修复仍待独立复核**，见[审计跟进记录](docs/AUDIT-FOLLOWUP-20261025.md)。
- **此前安装版验收（`103dd9f`，2026-10-25）**：该批次完成 214 项验证；三个独立授权 Pro 账号的手动查询成功，显示两档使用量和重置时间，核对了账号行选择、单号／全部刷新及独立 CLI 入口。没有执行真实 Desktop 切号，也不代表后续审计修复或定时刷新已经完成真实账号验收。

当前兼容范围仅覆盖已验证的 Claude Pro 环境；官方客户端更新、网络状况和服务限流仍可能影响查询。

---

## 安装、构建与使用

**主要在 macOS 开发与使用。Windows、Linux 已增加平台适配与安装包构建，但未经真实账号及桌面操作实测，属于实验支持。** Windows 提供 x64 安装程序与便携包，Linux 提供 x64 `.deb` / `.AppImage`，macOS 提供 Apple Silicon / Intel 通用包。自动化编译和模拟测试通过不等于真实使用验收；详见[跨平台说明](docs/CROSS-PLATFORM.md)。

**首次使用需先准备同步配置、官方 Claude Code 和 Electron 授权组件。** 新安装可使用 `tools/prepare_local_sync.py` 先预览，再显式初始化；已有配置不会覆盖。Windows 额度查询另需独立 Python / ConPTY 组件。仅安装应用不等于完成初始化，步骤见[跨平台说明](docs/CROSS-PLATFORM.md)、[同步边界](docs/LOCAL-SYNC.md)与[授权准备](docs/MANUAL-USAGE.md)。

### 日常更新覆盖
如果已配置同步环境，且该次发布明确同步引擎未变：

1. 备份旧应用与外部引擎、bridge、hook；
2. 正常退出 CASE，将本次构建或发布的应用替换到 `~/Applications/CASE.app`；
3. 保留账号配置、`ClaudeSessionSync` 和各账号登录目录；不要重复运行首次迁移工具，也不要为了更新 CASE 强行结束 Claude。

👉 [访问 GitHub 自有 Releases 页面](https://github.com/dundunge/claude-account-switcher-engine/releases)

---

### 从源码手动编译

在 macOS 的 Terminal / zsh 中进入本仓库根目录。需要已有 Rust 和 Xcode Command Line Tools；未安装 Tauri CLI v2 时先执行安装命令：

```zsh
# 安装打包工具
cargo install tauri-cli --version '^2' --locked

# 编译独立安装包
cargo tauri build --bundles app --ci
```

编译产物位于 `target/release/bundle/macos/CASE.app`。应用采用本地临时签名，未经 Apple 公证；首次打开按 macOS 的正常安全提示处理。

**运行完整验证套件：**

```zsh
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
python3 -m unittest discover -s tests/python -q
node --test tests/*.test.cjs
```

---

## 本地同步引擎说明

相关脚本与离线自动化测试均已开源在仓库中：

- `engine/claude_session_sync.py`：0.3.0 版本的核心索引同步、快照备份与事故恢复引擎。
- `tools/case_sync_bridge.py`：负责与 CASE 交互、检查 Profile 分区健康度并安全执行串行切换。
- `tools/migrate_cockpit_to_case.py`：**已有 Cockpit 同步配置的首次迁移工具**，需要先准备经过审核的 `ClaudeSessionSync/config.json`。默认仅预览；`--apply` 才会写入，拒绝覆盖已有 CASE 或账号目录。已迁移用户**不要重复运行**。

> ⚠️ **关于首次同步的特别提醒：**
>
> 启动账号前必须有有效的 `ClaudeSessionSync/CASE/hook.json`。缺失或无效时停止打开；尚未初始化的账号在 hook 校验通过后可打开以完成官方登录。
>
> 写入索引前必须确认 Claude 已正常退出。无法退出或存在未恢复事务时停止操作，不强制终止进程。配置与更新边界见[本地同步说明](docs/LOCAL-SYNC.md)。

---

## 开源协议

本项目基于 [MIT 许可证](LICENSE) 分发。

原作者 Patryk Starostka 保留原项目版权；独立维护版修改归 dundunge，按相同许可证提供。

*声明：本项目为个人开源项目，与 Anthropic 官方无任何商业隶属或背书关系。*
