# 云端 TTS 部署

声织在副机上运行，TTS 模型放在云端 GPU 实例（如 Google Cloud）。声织按普通 HTTPS 服务调用云端实例；只有 `localhost` / `127.0.0.1` 地址会由声织启动和管理。

## Colab L4 一键启动（已验证）

`deploy/colab/` 里的脚本放到 Google Drive 的 `MyDrive/VoiceLoom_Cloud/`，Colab 选 L4 GPU 后运行（`breeze` 或 `drama3`）：

```python
from google.colab import drive
drive.mount('/content/drive')
!bash /content/drive/MyDrive/VoiceLoom_Cloud/launch.sh breeze
```

- 首次运行在 L4 上编译（Breeze 约 8 分钟，audio.cpp 约 10 分钟），并从 Hugging Face 下载 GGUF。编译产物、模型、预置音色和密钥都存在 Drive 里，之后换新运行时只需恢复和启动。
- 输出的 URL 是 cloudflared 临时隧道地址（每次启动都会变）；`api_key.txt` 首次随机生成，两个引擎共用、之后一直沿用。声织「模型连接」里把对应引擎的服务地址改成该 URL，API 密钥填 `api_key.txt` 的内容。
- `auth_proxy.py` 校验 `Authorization: Bearer` 后转发给只监听 `127.0.0.1` 的模型服务；隧道提供 HTTPS。
- Colab 空闲时会回收运行时，开播前运行一次即可。

2026-11-01 实测（副机 → Cloudflare 隧道 → L4，建连约 5 秒，声织对远端地址的连接超时为 20 秒）：

| 引擎 | 显存 | 启动 | 热身后每句耗时 |
|---|---|---|---|
| Breeze Q8 | 4.1 GB | 约 1 分钟 | 约等于音频时长（2～6 秒句子） |
| Drama3 Q4 专家 / Q8 主干 | 14.5 GB（专家缓存 128） | 模型复制到本地 SSD 约 2 分钟，首句 70 秒左右 | 服务端 RTF 约 2.7（3 秒音频约 8 秒，加隧道约 12 秒） |

Drama3 在 L4 上明显慢于本机 5090D：23 GB 的模型放不进 L4 的 22 GB 显存，专家权重要从磁盘按需调入。直播实时对话用 Breeze；Drama3 适合不赶时间的预生成台词，或换 A100 / H100 运行时。`DRAMA3_EXPERT_CACHE` 环境变量可调专家缓存槽数（默认 64，L4 上 128 首句更快）。

云端 Drama3 的编译打了与本机相同的采样补丁 `audiocpp_drama3_sampling.patch`（采样对齐 Fish 官方实现），预置音色放在 Drive 的 `drama3/voices/`：`<名字>.wav` 加同目录 `prompt_text`（每行 `名字|参考文字`），无需重启即可新增。

## 配置备用云端 API

Colab 运行时或隧道掉线时，可自动切换到 Fish Audio、ElevenLabs 或 OpenAI 兼容 TTS。在「模型连接」保存备用 API 的地址、密钥、模型和声音 ID，再在工作台「模型与人设 → 备用 TTS」选中它。参考音色中的 `provider_voice_ids` 可按服务 ID 为每个角色绑定备用声音；未绑定时使用该服务的默认声音 ID。

主服务发生连接错误、超时或 HTTP 502/503/504/520–530 时，当前整句会用备用 API 重新合成。之后 60 秒内直接使用备用；监听时每 30 秒检查主服务，探活成功则下一句切回，也会在冷却后通过下一句重试主服务。鉴权、额度和参数错误显示在记录里，供修正配置。工作台横幅显示主服务、备用服务、切换时间和原因。

Colab 新运行时更换了隧道地址时，更新原主服务的地址后保存；工作台清除旧状态，监听期间立即重新探活。备用合成的字数、音频秒数与次数归入实际使用的云端服务，可在「记录与用量 → 用量统计」查看。

## 参考音色：两种用法

| 用法 | 声织里怎么填 | 每句请求发送 |
|---|---|---|
| **服务端预置（推荐）** | 参考语音编辑框里，对应 TTS 一栏填预置音色名 | 只发音色名 |
| 本机参考上传 | 预置栏留空，选好参考音频和参考文字 | Breeze：同一实例只注册一次，实例重启后自动重新注册；Drama3：每句内联 base64（≤5 MiB） |

预置音色在实例启动时就加载好，直播中每句只多传几个字节；云端实例换机或重启后也不用重新上传。

## Breeze-TTS-2.cpp

启动时指定音色目录，目录里的 `<名字>.breeze` 会全部载入：

```bash
breeze-server breeze-tts-2-q8_0.gguf --host 127.0.0.1 --port 7871 --voices-dir /srv/voices --ws-port -1
```

在实例上制作一次预置音色（保存为 `/srv/voices/raven.breeze`）：

```bash
curl -F ref_audio=@raven.wav -F "ref_text=参考音频对应的文字" -F name=raven http://127.0.0.1:7871/v1/voices
```

声织里该参考语音的「Breeze · 预置音色名」填 `raven`。

## Drama3（audio.cpp）

`server.json` 里给模型加 `voice_presets`（参考文字随预置保存在服务端）：

```json
"models": [{
  "id": "drama-3", "...": "...",
  "voice_presets": {
    "raven": {"voice_ref": "/srv/voices/raven.wav", "reference_text": "参考音频对应的文字"}
  },
  "default_voice_preset": "raven"
}]
```

也可以在顶层设 `"voice_dir": "/srv/voices"`，目录里放 `raven.wav` 和 `prompt_text`（每行 `raven|参考音频对应的文字`），音色名同样填 `raven`。

云端或预置音色走 `/v1/audio/speech`；本机直连仍走 `/v1/tasks/run`，多一道「触及长度上限」检查。云端请把单段台词控制在 `max_tokens` 能覆盖的长度内。

## GPT-SoVITS（api_v2）

`api_v2` 只认服务器上的文件路径。把参考音频放到实例上，声织里「服务器参考音频路径」填 `/srv/voices/raven.wav`，参考文字取声织里这条参考语音的文字。

## 对外暴露：HTTPS + 密钥

Breeze 和 audio.cpp 自身没有鉴权。模型服务只监听 `127.0.0.1`，前面放一层反向代理负责 HTTPS 和密钥校验。Caddy 示例（自动申请证书）：

```
tts.example.com {
    @authed header Authorization "Bearer 你的长随机密钥"
    handle @authed {
        reverse_proxy 127.0.0.1:7871
    }
    respond 401
}
```

GCP 防火墙只放行 443，可按需限定来源 IP 为主播家里的公网地址。声织「模型连接」里：

- 服务地址填 `https://tts.example.com`（非本机地址必须是 HTTPS）
- API 密钥填同一个密钥，声织会以 `Authorization: Bearer …` 发送
- 点「测试连接」：Breeze 检查 `/health`，Drama3 检查 `/v1/models`，均不触发生成
