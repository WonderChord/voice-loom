#!/bin/bash
# 在 Colab L4 上部署 Breeze-TTS-2.cpp（CUDA）作为声织云端 TTS；产物缓存到 Drive，下次直接复用。
set -euo pipefail
DRIVE=/content/drive/MyDrive/VoiceLoom_Cloud
WORK=/content/voiceloom
mkdir -p "$DRIVE" "$WORK"
cd "$WORK"
log() { echo "[$(date +%H:%M:%S)] $*"; }

if [ -f "$DRIVE/breeze-bin.tar.gz" ]; then
  log "从 Drive 恢复已编译的 Breeze"
  tar -xzf "$DRIVE/breeze-bin.tar.gz" -C "$WORK"
else
  log "编译 Breeze-TTS-2.cpp (CUDA, sm_89)"
  rm -rf Breeze-TTS-2.cpp
  git clone --recursive --depth 1 https://github.com/HoppouAI/Breeze-TTS-2.cpp
  cd Breeze-TTS-2.cpp
  cmake -B build -DCMAKE_BUILD_TYPE=Release -DBREEZE_VULKAN=OFF -DBREEZE_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89
  cmake --build build -j 12
  cd "$WORK"
  mkdir -p breeze
  find Breeze-TTS-2.cpp/build -maxdepth 3 \( -name 'breeze-server' -o -name 'breeze-cli' -o -name '*.so*' \) -type f -exec cp -a {} breeze/ \;
  tar -czf "$DRIVE/breeze-bin.tar.gz" breeze
fi

if [ ! -f "$DRIVE/gguf/breeze-tts-2-q8_0.gguf" ]; then
  log "下载 Breeze GGUF 到 Drive"
  python3 - <<'EOF'
from huggingface_hub import snapshot_download
snapshot_download("HoppouAI/Breeze-TTS-2.cpp", local_dir="/content/drive/MyDrive/VoiceLoom_Cloud/gguf",
                  allow_patterns=["breeze-tts-2-q8_0.gguf", "audio_tokenizer/*"])
EOF
fi
log "复制模型到本地 SSD"
mkdir -p "$WORK/gguf" "$DRIVE/voices"
cp -r "$DRIVE/gguf/." "$WORK/gguf/"
log "完成"
ls -la "$WORK/breeze" "$WORK/gguf"
