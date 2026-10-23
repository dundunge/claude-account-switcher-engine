# CASE 独立维护版

[English](README.en.md)

基于 [Patryk Starostka 的 CASE](https://github.com/pstarostka/claude-account-switcher-engine)
4.1.0 开发，保留原 MIT 版权声明。来源和基线见 [NOTICE.md](NOTICE.md)。

本仓库独立维护：**更新检查、源码和发行页面只指向
[dundunge/claude-account-switcher-engine](https://github.com/dundunge/claude-account-switcher-engine)**，
不会自动合并上游代码。

## 当前功能

- 为各个 Claude Desktop 账号使用独立的官方登录目录。
- 设置 → 通用中切换中文和 English；主窗口、设置、菜单随即切换，重启后保留。
- “额度”页面分别显示 5 小时、周额度的**已用比例**、采样时间和来源。
- “刷新本地额度”重新读取 Claude 已有记录，不会促使它生成新采样。
- “尝试官方查询”使用现有登录状态向固定的 Claude 官方用量接口发起 GET；不发送模型消息。
  macOS 可能提示钥匙串授权。每账号至少间隔 60 秒。接口可能返回 401、403、限流或浏览器验证要求；
  查询失败保留旧读数，缺失读数显示未知。**当前实际验证遇到浏览器验证，官方实时刷新未验收成功。**
- 独立本地同步引擎合并 Claude Code Desktop 会话索引，保留父子关系；正文继续由 Claude 自己保存。

没有反向代理，不修改 Claude 程序包，不上传会话正文，不迁移云端聊天，不重置或增加账号额度。
本地索引同步也不能把一个账号的模型上下文缓存转给另一个账号；长任务宜使用本地交接文件。

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
node --test tests/i18n.test.cjs
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
