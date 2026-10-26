# CASE 本地修改审计入口

> 这份文档保留原审计包的范围与验收批次。现已收到 Grok 报告，三项问题的修复、
> 220 项离线验证和待复核边界见[审计修复记录](AUDIT-FOLLOWUP-20261025.md)。

## 审计目标

请对压缩包中的实际源码做独立只读审计。实现者的测试和验收记录是待核对的证据，
不能代替你的结论。请报告可复现的问题、严重程度、文件及行号、触发条件和建议修复。
原审计包出包时尚未收到独立审计结果；不要把“测试通过”写成“Grok 已审计通过”。

## 用户最终确定的交互

1. 保留紧凑横向账号列表。点击账号行只选中，单击和双击都不切号。
2. 账号最右侧原回车/数字提示位置固定显示“切换”按钮，左键点击才执行该账号切换。
3. “切换”旁边的刷新图标只查询本账号，不能切号、发起授权或查询其他账号。
4. 顶部“刷新全部”在主页直接串行查询全部账号，一个失败后继续其余账号；重复点击不叠加。
5. 逐账号详细查询、授权、重新授权、打开独立 CLI 和错误详情位于“设置 → 额度查询”。
6. 两个额度窗口都显示已用比例和重置时间，按本机时区显示，缺失值不能补成 0%。
7. 只允许手动查询；启动、打开设置、状态轮询和定时重绘均不触发额度请求。

账号右键与底部“操作”菜单继续作为管理入口。原有明确的 Enter、数字切换快捷键保留；
焦点在刷新或其他按钮时，Enter 由该按钮处理，不能被全局切换快捷键截获。

## 本轮交互实现

| 文件 | 重点 |
| --- | --- |
| `ui/launcher.js` | 行选择、固定切换/单号刷新按钮、顶部全号刷新、键盘事件与焦点保持 |
| `ui/quota.js` | 两个窗口共用手动查询控制器、请求状态、批量串行与结果统计、错误文案和管理卡片 |
| `ui/settings.js` | 额度查询分类、管理卡片、只读状态同步 |
| `ui/common.js` | 失败状态合并、主页已用比例与重置时间、原始采样时间 |
| `ui/index.html` / `ui/settings.html` | 共用脚本加载顺序 |
| `ui/styles.css` | 窄窗横向布局、固定按钮与重置提示 |
| `tests/account_interaction.test.cjs` | 7 项行为测试：防误切、单号、全号、Enter、串行、失败与旧状态 |
| `tests/quota_state.test.cjs` / `tests/bridge.rs` | 失败不回填、独立 CLI 与授权分离、脚本与后端命令契约 |

## 必须保持的额度和身份约束

- `usage_refresh` 六种状态：`success`、`auth_failed`、`unavailable`、`in_progress`、`failed`、`timeout`。
- 失败始终 `usage: null`；较慢的状态响应和其他窗口的轮询不能恢复旧百分比。
- Desktop 独立 `--user-data-dir`、CLI 独立目录、account/org 身份绑定继续保留。
- 普通 Terminal 直接运行 `claude` 使用它自己的默认配置；CASE 的独立 CLI 是另一入口。
- 打开已授权账号 CLI 不要求先重新授权，也不替换原身份绑定。
- CASE 不读取 token、Cookie、凭据文件或钥匙串；官方 CLI 自行管理隔离凭据。
- 额度授权和查询不代替用户完成 OAuth 确认或官方目录信任，不退出现有 Claude，不重载 Clash/Mihomo。
  Desktop 的明确切号操作仍会请求 Claude 正常退出，完成索引同步后启动目标账号。
- 不提供或注入反向代理、模型 API 网关或请求中转，不改写 Claude API 地址。
  Electron 只显示隔离的官方授权页面；OAuth 回调、凭据兑换及刷新由官方 CLI 处理。
  用户已有系统代理或代理环境变量与 CASE 的账号隔离设计是两回事。

## 同一工作区内的前序修改

工作区基于 `9d80396`，在本轮开始前已有未提交的额度查询和授权改动。
`interaction.patch` 仅描述本轮交互调整相对开始时源码的变化；
`all-working-tree.patch` 与完整源码包含全部待审改动，不能把它们都算成本轮新写的内容。

前序关键路径是 `src/usage_monitor.rs`、`tools/claude_usage_probe.py`、
`tools/claude_auth_browser.cjs`。首次 `/usage` 失败已定位到官方 CLI 2.1.310 的
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` 总开关：它会阻止额度接口，官方缓存有时掩盖失败。
现改成分别关闭遥测与错误上报。后台采集要求完整读数加 HTTP 200 证据，或官方明确采用
不足 60 秒的同账号短快照；短快照使用原始采样时间。请求失败回退旧百分比不能算成功。

请重点核对两个窗口并发、状态版本顺序、身份变化后的迟到结果，以及批量查询期间账号列表变化。
原始终端/debug 内容不能落盘或返回 UI；用于来源校验的临时 FIFO 必须清理。

## 已执行的验证及边界

- Python 118 项、Rust 63 项单元测试 + 3 项 bridge、Node 30 项，共 **214 项通过**。
- `cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`、UI JavaScript 语法和 `git diff --check` 通过。
- 本机候选版 UI：账号行点击仅改变选中项；第二号单独刷新只更新第二号；顶部全号刷新后三个账号均成功。
- “设置 → 额度查询”显示每个账号的管理入口；第三号“打开独立 CLI”复用已有授权，
  新进程工作目录匹配第三号隔离 workspace，账号列表、hook 与三个 binding 的哈希未变化。
- 最终安装版再次手动刷新全部，三个账号均成功，显示两项使用量及各自重置时间；
  设置中的查询、独立 CLI 和重新授权入口再次核对。运行源码与验收哈希清单中 42 个文件一致。
- 真正的 Desktop 切号会退出 Claude，本轮未点击真实“切换”执行该动作；事件路由以离线测试验证。
- 没有通过操作真实账号制造故障；失败、超时和迟到结果使用离线模拟验证。
- 官方服务或网络仍可能返回错误；这些验证不保证之后的每次请求成功。

本机安装、签名和回滚位置以本次 `out/account-interaction-*/deployment.json` 为准。
审计包不含账号文件、凭据、真实授权 URL、原始终端输出或真实账号截图。

本次 GitHub 更新包含源码及中英文 README，不创建新 Release。README 解释了不使用反代，
以及 Desktop 登录与独立 CLI 授权分开、查询不打断工作、失败不触发切号和身份/新鲜度校验的原因。
本地源码审计包包含 `source/`、上述两个 patch、`VERIFICATION.md` 和文件 SHA-256 清单；
GitHub 仓库中的这份文档也可直接作为审计入口。

## 建议复验

在 macOS / zsh 的项目根目录执行（需要已有工具）：

```sh
node --test tests/*.test.cjs
python3 -m unittest discover -s tests/python -q
cargo test
cargo fmt --check
cargo clippy --all-targets -- -D warnings
git diff --check
```

本机 Cargo 位于 `~/.cargo/bin/cargo`，SDK 为
`/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk`。
Python 的会话引擎测试依赖本仓库的 `engine/claude_session_sync.py`，审计包保留该相对目录结构。
不要为复验登录真实账号、读取现有凭据、切换账号或操作代理服务。
