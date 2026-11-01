#!/bin/bash
# 启动云端 Drama3：audio.cpp 只听 127.0.0.1，经鉴权反代 + cloudflared 隧道对外提供 HTTPS。
# 预置音色放在 Drive 的 drama3/voices/：<名字>.wav，参考文字写进同目录 prompt_text（每行 名字|文字）。
set -euo pipefail
DRIVE=/content/drive/MyDrive/VoiceLoom_Cloud
WORK=/content/voiceloom
MODEL=${DRAMA3_GGUF:-drama-3-backbone-8bit-experts-Q4_K_S.gguf}
KEY_FILE="$DRIVE/api_key.txt"
[ -s "$KEY_FILE" ] || python3 -c "import secrets;print(secrets.token_urlsafe(32))" > "$KEY_FILE"
cp "$DRIVE/auth_proxy.py" "$WORK/auth_proxy.py"
mkdir -p "$DRIVE/drama3/voices"
pkill -f "[a]udiocpp/audiocpp_server" || true
pkill -f "[a]uth_proxy.py --listen 8791" || true
pkill -f "[c]loudflared tunnel --url http://127.0.0.1:8791" || true
cat > "$WORK/drama3/server.json" <<EOF
{
  "host": "127.0.0.1", "port": 17863, "backend": "cuda", "threads": 8,
  "lazy_load": false, "max_loaded_models": 1, "idle_unload_ms": 0, "ui": false,
  "voice_dir": "$DRIVE/drama3/voices",
  "models": [{
    "id": "drama-3", "family": "fish_drama3", "path": "$WORK/drama3/$MODEL", "task": "tts", "mode": "offline",
    "session_options": {
      "fish_drama3.disk_experts": true, "fish_drama3.expert_cache_size": ${DRAMA3_EXPERT_CACHE:-64},
      "fish_drama3.cpu_expert_layers": 0, "fish_drama3.pin_experts": false,
      "fish_drama3.context_length": 2048, "fish_drama3.backbone_prefill_cache_mb": 128,
      "fish_drama3.tower_prefill_cache_mb": 64
    },
    "default_request_options": {"max_tokens": 1024, "seed": 42}
  }]
}
EOF
LD_LIBRARY_PATH="$WORK/audiocpp:/usr/lib64-nvidia:${LD_LIBRARY_PATH:-}" nohup "$WORK/audiocpp/audiocpp_server" \
  --config "$WORK/drama3/server.json" > "$WORK/drama3.log" 2>&1 &
nohup python3 "$WORK/auth_proxy.py" --listen 8791 --upstream http://127.0.0.1:17863 --key-file "$KEY_FILE" > "$WORK/proxy_drama3.log" 2>&1 &
rm -f "$WORK/tunnel_drama3.log"
nohup cloudflared tunnel --url http://127.0.0.1:8791 > "$WORK/tunnel_drama3.log" 2>&1 &
URL=""
for i in $(seq 1 60); do
  URL=$(grep -aoE 'https://[-a-z0-9]+\.trycloudflare\.com' "$WORK/tunnel_drama3.log" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
for i in $(seq 1 300); do
  curl -sf http://127.0.0.1:17863/v1/models >/dev/null && break
  sleep 1
done
echo "{\"url\": \"$URL\"}" > "$DRIVE/drama3_tunnel.json"
echo "DRAMA3_URL=$URL"
curl -s http://127.0.0.1:17863/v1/models | head -c 300; echo
