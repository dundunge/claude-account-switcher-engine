# 发布兼容矩阵 / Release compatibility matrix

CASE 的 App、原生额度读取模块、本地同步 engine 和 bridge 是一组版本化组件。发布前在仓库根目录运行：

```zsh
python3 tools/compatibility_manifest.py \
  --app target/release/bundle/macos/CASE.app \
  --output artifacts/<release-id>/compatibility.json
```

该清单保存 App 二进制、engine、bridge、原生额度源文件的 SHA-256，以及配置和快照 schema。没有待发布的 App bundle 时，App 的 hash 会保持 `null`，不能把源码 hash 当成已打包二进制 hash。

| 组件 | 当前版本 | 兼容边界 |
| --- | --- | --- |
| CASE App | `4.2.1`（Cargo package） | macOS 原生额度读取；Windows 仅保留编译检查 |
| Session sync engine | `0.3.0` | bridge 通过 `engine_sha256` 固定源码；不读凭据或正文 |
| CASE bridge | `0.3.0` | hook `schema_version: 1`；新安装写入 `bridge_sha256` |
| Runtime config | `schema_version: 2` | 多 profile / account / organization 分区 |
| Snapshot manifest | `schema_version: 1` 或 `2` | 未完成快照必须先恢复；恢复中断可继续 |
| Native usage | App 内模块 | Accessibility 读取已打开 Claude 的 Usage 子树；不走 OAuth、Cookie 或独立 API |

已有 hook 没有 `bridge_sha256` 时仍可读取，升级安装会补上该字段；如果字段存在但 hash 不匹配，bridge 会阻止切号。发布记录必须把本文件生成的 `compatibility.json` 与 App、engine、bridge 一起归档，不能只记录 App 版本号。
