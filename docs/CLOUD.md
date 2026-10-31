# 云端 TTS 部署

声织在副机上运行，TTS 模型放在云端 GPU 实例（如 Google Cloud）。声织按普通 HTTPS 服务调用云端实例；只有 `localhost` / `127.0.0.1` 地址会由声织启动和管理。

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
