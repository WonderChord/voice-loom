# Windows 构建与验证

环境：Windows 10/11 x64、Python 3.12、Node.js LTS，以及系统 .NET Framework 4.8。构建脚本使用系统 C# 编译器；下载固定版本的 WebView2 SDK 和 NSIS，并验证 SHA256。

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix frontend ci
npm --prefix frontend audit
.venv\Scripts\python.exe -m pip_audit -r requirements-lock.txt
.venv\Scripts\python.exe -m bandit -r backend desktop launcher.py -ll
.venv\Scripts\python.exe desktop/build.py
```

构建输出位于 `release/`：便携 ZIP、当前用户安装 EXE、源码 ZIP 和 SHA256SUMS。便携 ZIP 带 `portable.mode`；安装包使用用户本地目录。两个包均包含核心后台、前端、原生窗口、依赖清单及第三方许可。源码 ZIP 只收 `git ls-files` 列出的文件，`.gitignore` 排除的用户数据、授权文件、录音、验收目录和构建产物不会进入；先提交再构建。AI 权重、Torch/FunASR 环境、录音、用户配置及对比报告由外部目录提供。

已下载过 WebView2 SDK 和 NSIS 时，可用 `--sdk-dir desktop/.cache/webview2-<版本>`、`--nsis-dir desktop/.cache/nsis-3.13/nsis-3.13` 跳过下载。

SenseVoice 可设置 `VOICE_LOOM_MODEL_PYTHON` 指向已有 Torch/FunASR Python。`backend/local_runtime.py` 集中管理 SenseVoice 模型路径与包目录、Qwen/Breeze/Drama3/GPT-SoVITS 的本机运行表；移植到其他路径时在此调整。已有 HTTP 服务可直接在“模型连接”配置。

## 桌面验收

在干净数据目录启动便携版，检查八个页面、导入、配置保存、Tab/Escape 对话框、未保存退出提示、重复启动，以及关闭后的后台退出。检查带空格、中文的安装路径；安装和卸载均使用当前用户权限，卸载保留个人数据。

将 Windows 缩放设为 100%、125%、150%、200%，跨显示器检查文字清晰度和操作区。自动渲染检查可运行：

```powershell
声织.exe --data C:\temp\voice-loom-test --port 17961 --diagnostics C:\temp\voice-loom-dpi
```

诊断输出包含进程 Per-Monitor V2 状态和四档渲染比例下的截图、宽度与溢出测量。该自动检查使用渲染器设备比例仿真；真实 Windows 多显示器切换需要另外操作验收。

## GitHub 源码

仓库根目录为本说明的上一级。`.gitignore` 排除用户数据、授权、模型权重、音频、设计参考、私有验收目录、前端产物、缓存和发行包。提交前检查 `git status`、暂存文件清单和凭证扫描结果。Actions 仅在手动触发或 PR 时构建 artifact，权限为 `contents: read`；维护者自行选择源码许可与代码签名证书后再发布。
