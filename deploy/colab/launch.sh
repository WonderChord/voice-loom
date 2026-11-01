#!/bin/bash
# Colab 一键启动声织云端 TTS：首次编译并缓存到 Drive，之后只恢复和启动（约 1 分钟）。
set -euo pipefail
DRIVE=/content/drive/MyDrive/VoiceLoom_Cloud
command -v cloudflared >/dev/null || {
  wget -q -nc -O /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
  dpkg -i /tmp/cloudflared.deb >/dev/null 2>&1
}
python3 -c "import aiohttp" 2>/dev/null || pip install -q aiohttp
[ -x /content/voiceloom/breeze/breeze-server ] && [ -f /content/voiceloom/gguf/breeze-tts-2-q8_0.gguf ] || bash "$DRIVE/setup_breeze.sh"
bash "$DRIVE/start_breeze.sh"
echo "声织里填：服务地址 = 上面的 TTS_URL；API 密钥 = $DRIVE/api_key.txt 的内容"
