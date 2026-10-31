# 架构与接口约定

`backend/app.py` 负责本机 HTTP 边界、输入限制、认证和静态界面；`pipeline.py` 负责串行识别、编排、自检、合成和取消；`providers.py` 适配外部模型；`local_runtime.py` 管理已登记的本机模型进程。`store.py` 原子保存配置、参考和历史，使用 Windows DPAPI 保存密钥。`subtitles.py` 提供审核后纯台词、到期清屏和有界 SSE 重播。

`frontend/src` 是工作台界面，`desktop/VoiceLoom.cs` 是 Windows 原生窗口和生命周期管理，`desktop/service.py` 是冻结后台入口。`launcher.py` 支持源码开发时在浏览器打开工作台。

桌面程序向健康接口发送随机挑战，核验数据目录中当前用户加密授权的 HMAC 证明后，通过 WebView2 CookieManager 写入 HttpOnly、SameSite=Strict 会话。浏览器启动器以 URL fragment 传递派生会话，前端立即清除并换成 Cookie。独立字幕凭证只允许读取字幕页面、当前值和事件。

私有接口包括 `/api/config`、`/api/state`、`/api/references`、`/api/process/text`、`/api/process/audio`、`/api/preview`、`/api/records` 与音频文件。字段验证以 `backend/models.py` 为准；接口行为由 `tests/` 覆盖。`GET /api/access/outputs` 返回当前字幕集成地址。`POST /api/listen/stop` 取消队列与后续播放；`POST /api/desktop/shutdown` 只在桌面后台入口生效。

所有任务在入队时快照模型、参考、目标语言和风格。审核未通过时保留待确认稿；纯声音动作通过能力检查后可合成。低能量片段在 ASR 前跳过。停止后迟到的模型返回值按取消状态处理。

参考音频和输入音频最多 60 秒、25 MiB，音频解码按固定帧块累计实际时长；上传最多并行 2 条。JSON 请求最多 4 MiB，授权引导最多 4 KiB。路径解析限定在数据或报告目录内，隐藏文件与 NTFS 流路径拒绝访问。

桌面版将自己启动的后台和后代绑定 Windows Job Object。正常退出先停止监听和后台，异常退出由系统回收该进程树。已独立启动的受管模型由登记身份和原有生命周期管理。
