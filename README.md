# 声织 Voice Loom

Windows 本机语音工作台：麦克风或音频文件 → 语音识别 → 整理、翻译与表演稿审核 → 语音合成 → 所选输出设备。

## 桌面版

提供 Windows x64 便携 ZIP 和当前用户安装 EXE。便携版完整解压后双击 **声织.exe**；安装版通过开始菜单或桌面快捷方式打开。需要 Windows 10/11、.NET Framework 4.8 和 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。工作台核心无需另装 Python。

窗口使用 Per-Monitor V2 DPI，字体由 WebView2 按实际像素渲染。右上角“更多工具”和 Ctrl++ / Ctrl+- / Ctrl+0 调整文字大小。窗口保留深色系统标题栏，工具入口融入工作台。启动时麦克风关闭，关闭窗口停止监听和本程序启动的后台。

首次使用先在“模型连接”配置服务，在“音频设备”选择输入输出，在“参考音色”导入参考音频。AI 权重及模型环境独立部署；安装包包含界面与工作台后台。

## 功能

- 文字、麦克风、最长 60 秒的音频文件输入；低能量过滤与有界任务队列。
- Qwen3-ASR、SenseVoice，本地 Breeze、Drama3、GPT-SoVITS；云端 OpenAI 兼容接口、ElevenLabs、Fish Audio。
- 模型表演规则、角色润色风格、通用语音偏好分别保存；提供风格预设。
- 角色卡一键切换参考音色、TTS、人设风格、目标语言和情绪强度，工作台可快速切换，监听中下一句生效。
- 读音词典在合成前把专有名词换成 TTS 读得准的写法（长词优先、只替换一次，可限定 TTS）；分全局词条和角色专属词条，字幕与记录保留原词。
- 保持原语言，或翻译为普通话、英语、日语、韩语、粤语和中英混说。翻译和润色经独立自检，歧义稿停在待确认。原文直通只做本地指令复核；在模型连接页设了「自检 LLM」时才另发一次 LLM 自检，自检服务未响应则退回本地复核。
- 参考音色库、录音与合成历史、重审与重新合成、字幕实时广播和导出。文字预览稿只用于试听，不进入对话上下文。麦克风和上传的输入音频处理后删除，合成结果保留在 `runs/`。
- 深浅主题、键盘对话框、未保存提示、自适应窄窗口。
- 导入已有的本机模型启动环境，支持 Python 脚本或原生程序、工作目录、启动参数、模型文件和健康检查；首次任务可按需启动。
- 可展开的调试栏显示模型启动、等待就绪、耗时、错误和日志，启动与失败时自动提示。
- Breeze、Drama3（audio.cpp）、GPT-SoVITS 可部署在云端 GPU 实例，以 HTTPS + 密钥连接；参考语音可绑定服务端预置音色，直播时每句只发音色名，部署见 [云端 TTS 部署](docs/CLOUD.md)。
- 随软件提供 stdio MCP，AI 助手可查询状态、使用音色、识别、合成和控制本机模型。合成默认保存文件，播放另有独立操作。

云端处理使用用户配置的服务和额度。选择模型前可先用文字预览检查配置；选择“开始监听”后采集麦克风。输出到虚拟声卡时，在其他应用中选择相应虚拟输入。

## 数据

便携版写入程序旁 `user-data/`；安装版写入 `%LOCALAPPDATA%/VoiceLoom/`。“更多工具 → 打开数据目录”可查看数据。退出后复制整个目录备份，卸载保留个人数据。API Key 和本机授权由当前 Windows 用户的 DPAPI 加密，换电脑或账户时重新输入密钥。

`desktop.json` 位于上述目录，支持 `data_directory`、`reports_directory`、`port` 和 `zoom`。可将 `data_directory` 指向已有声织数据目录。`reports_directory` 可连接个人对比报告目录；报告和录音保存在用户自己的磁盘。

工作台只监听 `127.0.0.1`，API 和录音需要本机会话授权。OBS 地址从“直播字幕”页复制；该地址带独立只读凭证。OBS 也可读取页面显示的 UTF-8 文本路径。

## 本机模型环境与 MCP

“更多工具 → 本机模型服务 → 导入启动环境”可填写已有环境的路径，或导入 JSON 后核对。检查文件通过后保存到个人数据目录的 `model-environments.json`。保存时保留现有 Python、依赖和权重；启动时使用所选程序及参数，以本机健康接口判断就绪。服务类型对应模型连接页的适配器，在模型连接页使用相同的本机地址即可按需启动；OpenAI 兼容地址也可带 `/v1`。

“更多工具 → 连接 AI 助手”提供当前安装位置的 Codex TOML 和通用 MCP JSON。客户端通过随包的 `service/VoiceLoom.Service.exe --mcp` 连接，无需另装 Python。配置和模型环境示例见 [MCP 与模型环境](docs/MCP.md)。MCP 可以在窗口关闭时使用；本机后台随 MCP 客户端连接管理。对话内容和音频继续保存在相同数据目录。

## 开发与构建

前端为 React/TypeScript，后台为 FastAPI，桌面外壳为 WinForms/WebView2。源码开发使用 Python 3.12 x64 和 Node.js LTS。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
npm --prefix frontend ci
npm --prefix frontend run build
.venv\Scripts\python.exe launcher.py
```

`launcher.py` 和 `启动声织.cmd` 优先使用项目内 `.venv`，也可用 `VOICE_LOOM_PYTHON` 指定。浏览器开发启动器会完成本机会话授权后打开 Edge 窗口。模型部署入口集中在 `backend/local_runtime.py`；默认管理路径适配已有的 `G:/AI` 部署，其他电脑可配置 HTTP 服务地址或修改部署表。SenseVoice 需要一个装有 Torch/FunASR 的独立 Python，用 `VOICE_LOOM_MODEL_PYTHON` 指定；未指定或路径不存在时，界面把 SenseVoice 显示为不可用。

测试、桌面构建、依赖审计和发布检查见 [构建说明](docs/BUILD.md)，安全行为见 [SECURITY.md](SECURITY.md)，接口与目录关系见 [CONTRACT.md](CONTRACT.md)。GitHub Actions 只生成构建产物，发布由维护者操作。项目源码许可待维护者选定；第三方许可随构建包提供。
