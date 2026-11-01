#!/bin/bash
# 启动云端 Breeze：模型服务只听 127.0.0.1，经鉴权反代 + cloudflared 隧道对外提供 HTTPS。
set -euo pipefail
DRIVE=/content/drive/MyDrive/VoiceLoom_Cloud
WORK=/content/voiceloom
KEY_FILE="$DRIVE/api_key.txt"
[ -s "$KEY_FILE" ] || python3 -c "import secrets;print(secrets.token_urlsafe(32))" > "$KEY_FILE"
cp "$DRIVE/auth_proxy.py" "$WORK/auth_proxy.py"
pkill -f "[b]reeze/breeze-server" || true
pkill -f "[a]uth_proxy.py --listen 8790" || true
pkill -f "[c]loudflared tunnel --url http://127.0.0.1:8790" || true
cd "$WORK/gguf"
LD_LIBRARY_PATH="$WORK/breeze:/usr/lib64-nvidia:${LD_LIBRARY_PATH:-}" nohup "$WORK/breeze/breeze-server" "$WORK/gguf/breeze-tts-2-q8_0.gguf" \
  --host 127.0.0.1 --port 7871 --voices-dir "$DRIVE/voices" --ws-port -1 > "$WORK/breeze.log" 2>&1 &
nohup python3 "$WORK/auth_proxy.py" --listen 8790 --upstream http://127.0.0.1:7871 --key-file "$KEY_FILE" > "$WORK/proxy.log" 2>&1 &
rm -f "$WORK/tunnel.log"; nohup cloudflared tunnel --url http://127.0.0.1:8790 > "$WORK/tunnel.log" 2>&1 &
for i in $(seq 1 60); do
  URL=$(grep -aoE 'https://[-a-z0-9]+\.trycloudflare\.com' "$WORK/tunnel.log" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
for i in $(seq 1 120); do
  curl -sf http://127.0.0.1:7871/health >/dev/null && break
  sleep 1
done
echo "{\"url\": \"$URL\"}" > "$DRIVE/tts_tunnel.json"
echo "TTS_URL=$URL"
curl -s http://127.0.0.1:7871/health; echo
