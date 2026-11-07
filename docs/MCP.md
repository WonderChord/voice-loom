# MCP 与本机模型环境

Drama3 表演稿可在 `speech.direction` 写不朗读的自然语言指导，`delivery` 选 `normal/whisper/soft/emphasis/stress/forceful/slow/fast`，点状声音单列 `event`；`text` 保持纯台词。先查询当前模型能力，再编排，详见 [Drama3 编排指南](DRAMA3.md)。确认合成仍使用原有独立操作。

## AI 助手连接

在软件右上角“更多工具 → 连接 AI 助手”复制与当前安装路径匹配的配置。Codex 使用 `[mcp_servers.voice_loom]` 下的 `command` 和 `args`；其他客户端可使用提供的 `mcpServers` JSON。配置不包含 API Key 或本机会话令牌。

安装包命令为 `service/VoiceLoom.Service.exe --mcp`，可附加 `--data`、`--port`。源码命令为 Python 3.12 运行 `desktop/service.py --mcp`。MCP 使用[官方 Python SDK](https://py.sdk.modelcontextprotocol.io/) 的 stdio 传输；Codex 配置字段见 [OpenAI MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

提供的工具覆盖状态、配置、参考音色导入和转写、文字/录音提交、记录查询、确认合成、播放、监听、字幕出口、会话导出、本机模型状态与启停，以及启动调试信息。文字和录音工具默认预览，实际合成默认保存文件；调用播放工具或明确设置 `play_audio=true` 后播放。

查询与默认设置工具：

- `list_models`：列出 ASR、LLM、TTS 连接，标出当前选用、自检 LLM 和是否本机。
- `list_devices`：列出输入输出设备和当前选择；WDM-KS 设备带 `unstable=true`，优先选 WASAPI 或 MME。
- `get_tts_tag_specs`：读取各 TTS 的耳语、情绪、动作标签规范。
- `set_defaults`：设置参考音色、ASR、LLM、自检 LLM、TTS、输入输出设备和润色/自检开关。参数接受完整 ID 或名称；部分匹配只在唯一时生效，多个候选会返回候选列表，配置保持不变。
- `list_characters` / `use_character`：列出角色卡；按 ID 或名称切换角色卡（同时切换音色、TTS、人设、语言和专属读音）。
- `preview_pronunciation`：查看读音词典替换后实际送给 TTS 的文字。
- `get_usage(days=30)`：返回本场 `session`、指定天数的按服务累计 `providers`、按日累计 `daily`，支持 1～90 天。字段包括 `requests`、`failures`、`tokens_in`、`tokens_out`、`chars`、`input_s`、`audio_s`、`fallbacks`；`save_error` 提示磁盘写入异常。

`update_settings` 修改人设、提示词、语言、角色卡、读音词典、音频、字幕和 `hotkeys` 等工作台设置。`active.tts_fallback` 指定备用 TTS 服务 ID，空字符串关闭；`hotkeys.enabled` 开关全局快捷键，其余字段为 `toggle_listen`、`flush`、`stop_playback`、`next_character`、`previous_character`；角色卡的 `hotkey` 为专属组合键。对象按键合并，例如 `{"active":{"tts_fallback":"elevenlabs"},"hotkeys":{"enabled":true}}`。模型的服务地址和 API Key 只在界面的“模型连接”页修改，避免已保存的密钥被发往新地址。

`get_status` 中的 `tts_failover` 显示主、备用服务 ID 与名称、`since` 和 `reason`，正常时为 null；`hotkeys` 按动作返回 `{key, ok, message}`，角色动作名为 `character:<id>`。HTTP 对应接口为 `GET /api/usage?days=30` 和 `GET /api/state`，沿用本机会话授权。

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
