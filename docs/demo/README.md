# 本地模拟网页版 / Local browser demo

此页面用于查看界面和制作项目截图。它复用仓库的 `ui/` 文件，通过浏览器内的模拟 Tauri bridge
提供三个虚构账号，不需要安装 CASE、Electron、Rust 或 Claude CLI。

目前真实账号适配和验证仅覆盖 Claude Pro；没有 Max 订阅号，尚未适配和验证 Max。
演示数据不构成任何套餐的兼容性验证。

## 启动

在 macOS 的 Terminal / zsh 中，进入本仓库根目录。需要已有 Python 3：

```zsh
python3 --version
python3 tools/preview_server.py --port 0
```

终端会打印本地地址，例如 `http://127.0.0.1:57678/`；以实际打印的端口为准。
只监听 `127.0.0.1`。按 Ctrl+C 停止这个演示服务器。

## 视图

在打印的地址后加以下路径或查询参数：

| 路径 | 内容 |
| --- | --- |
| `/?view=overview` | 中文 16:9 封面 |
| `/?view=launcher` | 中文账号主页 |
| `/?view=usage` | 中文额度管理 |
| `/?view=english` | English 浅色 16:9 封面 |
| `/?view=portrait` | 中文 3:4 宣传图 |
| `/?view=x` | 中文 5:2 的 X 帖子横图（2000 × 800） |
| `/app/index.html?lang=zh-CN&theme=dark` | 可操作的模拟主页 |
| `/app/settings.html?lang=zh-CN&theme=dark&section=usage` | 可操作的模拟额度设置 |

直接 UI 的参数支持 `lang=en` 和 `theme=light`。在直接 UI 地址后加 `scenario=failed`，
可以模拟第二个账号刷新失败，显示空读数。重新加载页面会恢复虚构的初始状态。

## 模拟边界

- “切换”只更改页面内的运行标记，不启动或退出任何程序。
- “刷新”只等待短暂的页面动画并返回虚构数据，不执行 CLI、不联网查询、不发送模型消息。
- 定时开关、自定义间隔及下次时间只修改页面内模拟状态，不运行真实后台计时查询；重新加载即恢复默认关闭。
- 授权、增加账号和其他系统操作返回演示提示，不操作账号、文件或系统。
- 不读取浏览器登录、Cookie、token、钥匙串、真实账号或终端输出。
- 服务器只提供明确列入白名单的 UI 和演示静态资源，不开放目录列表、`.git`、`out/` 或运行数据。
  CSP 的 `connect-src 'none'` 禁止页面发起数据请求。

已导出的图片及尺寸见[截图图库](../SCREENSHOTS.md)。

This loopback-only demo reuses the actual UI with in-memory fictional state. It performs no native IPC,
authorization, real quota query or model request. Use it to inspect the interface, not to verify real accounts.
