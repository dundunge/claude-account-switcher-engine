# 发布兼容矩阵 / Release compatibility matrix

CASE 的 App、原生额度读取模块、本地同步 engine 和 bridge 是一组版本化组件。发布前在仓库根目录运行：

```zsh
python3 tools/compatibility_manifest.py \
  --app target/release/bundle/macos/CASE.app \
  --output artifacts/<release-id>/compatibility.json
```

该清单保存 App 二进制、engine、bridge、原生额度与手动 CLI 查询源文件的 SHA-256，以及配置和快照 schema。没有待发布的 App bundle 时，App 的 hash 会保持 `null`，不能把源码 hash 当成已打包二进制 hash。

| 组件 | 当前版本 | 兼容边界 |
| --- | --- | --- |
| CASE App | `4.3.1`（Cargo package） | macOS 开发与使用；Windows / Linux 实验适配及打包，未经真实桌面/账号实测 |
| Session sync engine | `0.4.0` | bridge 通过 `engine_sha256` 固定源码；不读凭据或正文；保留旧 schema |
| CASE bridge | `0.4.0` | hook `schema_version: 1`；新安装写入 `bridge_sha256`；可读取既有 0.3.0 engine |
| Runtime config | `schema_version: 2` | 多 profile / account / organization 分区 |
| Snapshot manifest | `schema_version: 1` 或 `2` | 未完成快照必须先恢复；恢复中断可继续 |
| Native usage | App 内模块 | Accessibility 读取已打开 Claude 的 Usage 子树；不走 OAuth、Cookie 或独立 API |
| Official CLI usage | App 内模块和嵌入 helper | macOS / Windows / Linux 官方 CLI `/usage`；各号隔离授权、account/org 绑定；默认手动，可选定时 |

三平台安装、依赖及实测边界见 [CROSS-PLATFORM.md](CROSS-PLATFORM.md)。既有 macOS 0.3.0 外部 hook 可继续使用；不要未经审核覆盖脚本或重新运行初次迁移。

已有 hook 没有 `bridge_sha256` 时仍可读取，升级安装会补上该字段；如果字段存在但 hash 不匹配，bridge 会阻止切号。发布记录必须把本文件生成的 `compatibility.json` 与 App、engine、bridge 一起归档，不能只记录 App 版本号。
