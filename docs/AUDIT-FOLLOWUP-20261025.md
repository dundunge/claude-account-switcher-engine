# Grok 审计修复记录（2026-10-25）

## 审计与验证范围

用户提供的 Grok 报告审计了 `CASE-Grok-audit-20261025.zip`：

```text
SHA-256: f81745841cd8599ca393cf9d2da8f86d74bd64afc9cb5c470d7c42646e679cee
```

该包对应 GitHub 更新前的源码验收批次，随后提交为 `103dd9f`。
报告核对了 77 项文件哈希，重跑了 15 项 Node 测试，没有重跑全部 214 项、Rust 或 Python，
也没有登录真实账号或执行真实切号。原始审计范围见[审计入口](GROK-AUDIT-20261025.md)。

以下为实现者对三项问题的修复和离线验证，**尚待独立复核**。

## 1. 面板全局锁冲突造成错误的“查询中”状态

- 审计级别：中等。
- 原因：另一个账号占用面板读取锁时，本次调用并未开始读取，却被写成 `in_progress`。
- 修复：`src/main.rs` 将 `REFRESH_IN_PROGRESS` 映射为 `unavailable`；
  UI 明确说明本次未获取额度，可稍后手动重试，不再声称本账号正在查询。
- 保留既定语义：本次失败仍返回 `usage: null`，不恢复旧百分比。
  审计报告建议保留上次成功读数，这一点与当前产品要求冲突，因此没有采用。
- 回归验证：Rust 验证错误码分类，Node 验证读数清空、状态可重试及后续成功。

## 2. 后台下载等提示误伤完整 `/usage` 面板

- 审计级别：中等。
- 原因：对整屏做 `loading` 子串判断，`downloading`、`uploading`、`reloading` 也会命中。
- 修复：`tools/claude_usage_probe.py` 仅识别明确的额度加载提示，或额度块内独立的
  `Loading…` / “加载中”。完整额度旁的后台下载、上传和插件加载不再被整体丢弃。
- 回归验证：Python 覆盖中英文背景活动、完整两档读数，以及真正加载中仍拒绝旧数字。

## 3. 跨窗口同号查询被批量刷新误计为失败

- 审计级别：低。
- 原因：另一个窗口已在查询时，后点的“刷新全部”收到 `in_progress` 却立即计为未获取。
- 修复：`ui/quota.js` 等待已有查询完成再统计并处理下一个账号，不额外发起该账号额度请求。
  每 500 ms 只读本地状态，最长等待 60 秒；状态读取失败或届时仍在查询，单独计为 `pending`。
- 回归验证：Node 验证等待期间不查下一个账号、最终成功统计，以及本地状态读取失败不伪造查询失败。

## 最新源码验证

| 验证 | 结果 |
| --- | --- |
| Python | 120 项通过 |
| Rust unit | 64 项通过 |
| Rust bridge | 3 项通过 |
| Node | 33 项通过 |
| 合计 | **220 项通过** |
| `cargo fmt --check` | 通过 |
| `cargo clippy --all-targets -- -D warnings` | 通过 |

复验命令在 macOS 的 Terminal / zsh、仓库根目录执行，需要已有相应运行环境：

```zsh
python3 -m unittest discover -s tests/python -q
cargo test
node --test tests/*.test.cjs
cargo fmt --check
cargo clippy --all-targets -- -D warnings
git diff --check
```

## 安装与剩余边界

本次修复仅更新源码，没有替换此前 `103dd9f` 批次的本机 CASE 安装，也没有重新执行真实账号查询。
此前三账号查询成功与 214 项测试记录属于上一批次，不能替代这些修复的真实应用验收。
模拟网页版及五张截图仅展示 UI，不作为真实额度或登录状态证据。

审计还提到助手超时与进程组终止之间的收尾余量较窄，但未复现临时 FIFO 残留。
本轮没有据此宣称强制终止后的清理路径已得到额外验证。

没有读取凭据、替用户完成 OAuth 或目录信任、切换真实账号、重启 Claude，或重载／重启 Clash/Mihomo。
没有创建新的 GitHub Release。后续独立复核和真实应用验收应分别记录。
