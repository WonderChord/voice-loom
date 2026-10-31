# MCP 与本机模型环境

## AI 助手连接

在软件右上角“更多工具 → 连接 AI 助手”复制与当前安装路径匹配的配置。Codex 使用 `[mcp_servers.voice_loom]` 下的 `command` 和 `args`；其他客户端可使用提供的 `mcpServers` JSON。配置不包含 API Key 或本机会话令牌。

安装包命令为 `service/VoiceLoom.Service.exe --mcp`，可附加 `--data`、`--port`。源码命令为 Python 3.12 运行 `desktop/service.py --mcp`。MCP 使用[官方 Python SDK](https://py.sdk.modelcontextprotocol.io/) 的 stdio 传输；Codex 配置字段见 [OpenAI MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

提供的工具覆盖状态、配置、参考音色导入和转写、文字/录音提交、记录查询、确认合成、播放、监听、字幕出口、会话导出、本机模型状态与启停，以及启动调试信息。文字和录音工具默认预览，实际合成默认保存文件；调用播放工具或明确设置 `play_audio=true` 后播放。

MCP 和窗口共用本机后台及数据目录。MCP 可在窗口关闭时使用；客户端会管理自己启动的 MCP 进程和后代。连接前验证本机后台的随机挑战证明，再发送派生会话授权；端口被其他服务占用时返回可读错误。

## 导入已有环境

“更多工具 → 本机模型服务 → 导入启动环境”可创建或导入启动配置。先核对可执行文件、Python 脚本、工作目录、参数、模型文件、本机服务地址和健康检查路径，再检查并保存。保存只登记配置，点击启动或提交相应模型任务时执行启动。配置存放在个人数据目录的 `model-environments.json`，可随个人数据备份。

启动程序采用参数数组直接调用，支持现有 `python.exe + .py` 或原生模型服务 EXE。启动参数使用模型服务原本支持的选项，监听地址选择 `127.0.0.1`。服务类型选择相应协议，在模型连接中填写相同地址；OpenAI 兼容服务允许在接口地址后加 `/v1`。

例如 llama.cpp 的启动环境包含 `llama-server.exe`、工作目录、GGUF 权重、`-m` 和 `--host 127.0.0.1 --port ...` 参数，健康路径为 `/health`。使用 `--api-key-file` 指定个人密钥文件，在模型连接页填写对应密钥；`--cors-origins` 指定工作台本机地址。Python 服务额外指定现有环境的 `python.exe` 和启动脚本。检查模型文件用于确认路径完整，实际权重参数仍按该模型服务填写。

后台只回收自己登记并核验进程身份的模型。切换受管本机 TTS 时释放上一套 TTS；独立启动且未登记的服务可复用，并显示“外部启动”。

## 独立命令与调试

在安装目录使用：

```powershell
& .\service\VoiceLoom.Service.exe --models status
& .\service\VoiceLoom.Service.exe --models start breeze
& .\service\VoiceLoom.Service.exe --models stop breeze
```

导入环境使用其 `local-...` ID。通过 `--data` 指向已有数据目录。模型日志写入数据目录的 `logs/`。工作台“调试”栏和 MCP `get_debug_status` 读取同一份启动、等待、就绪和失败事件，显示实际耗时及日志末尾。
