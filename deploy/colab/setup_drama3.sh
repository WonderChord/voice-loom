#!/bin/bash
# 在 Colab L4 上部署 audio.cpp + Drama3 GGUF；编译产物与模型缓存到 Drive，下次直接复用。
set -euo pipefail
DRIVE=/content/drive/MyDrive/VoiceLoom_Cloud
WORK=/content/voiceloom
MODEL=${DRAMA3_GGUF:-drama-3-backbone-8bit-experts-Q4_K_S.gguf}
AUDIOCPP_REV=5b772fe040d4102ec59a557e045ef29e3ab5480d
mkdir -p "$DRIVE/drama3/voices" "$WORK/drama3"
cd "$WORK"
log() { echo "[$(date +%H:%M:%S)] $*"; }

if [ -f "$DRIVE/drama3/audiocpp-bin.tar.gz" ]; then
  log "从 Drive 恢复已编译的 audio.cpp"
  tar -xzf "$DRIVE/drama3/audiocpp-bin.tar.gz" -C "$WORK"
else
  log "编译 audio.cpp（CUDA sm_89，只含 fish_drama3）"
  rm -rf audio.cpp
  git clone https://github.com/0xShug0/audio.cpp.git
  cd audio.cpp
  git checkout -q "$AUDIOCPP_REV"
  git submodule update --init --recursive --depth 1
  # 与本机部署一致：采样对齐 Fish 官方实现（top_k 4097、top_p 保留越界词），并开放预填充缓存选项。
  git apply "$DRIVE/audiocpp_drama3_sampling.patch"
  bash scripts/build_linux.sh --backend cuda --cuda-arch 89 --model-set custom --models fish_drama3 \
    --target audiocpp_server --build-dir build/l4
  cd "$WORK"
  rm -rf audiocpp && mkdir -p audiocpp
  find audio.cpp/build/l4 \( -name 'audiocpp_server' -o -name '*.so*' \) \( -type f -o -type l \) -exec cp -a {} audiocpp/ \;
  tar -czf "$DRIVE/drama3/audiocpp-bin.tar.gz" audiocpp
fi

if [ -f "$WORK/drama3/$MODEL" ]; then
  log "本地 SSD 已有模型"
elif [ -f "$DRIVE/drama3/$MODEL" ]; then
  log "从 Drive 复制模型到本地 SSD"
  cp "$DRIVE/drama3/$MODEL" "$WORK/drama3/$MODEL"
else
  # 先下到本地 SSD（快），再存一份到 Drive 供下次使用。
  log "下载 $MODEL"
  python3 -c "import sys; from huggingface_hub import hf_hub_download as d; d('Jmica/Drama-3-GGUF', sys.argv[1], local_dir='$WORK/drama3')" "$MODEL"
fi
if [ ! -f "$DRIVE/drama3/$MODEL" ]; then
  log "保存模型到 Drive"
  cp "$WORK/drama3/$MODEL" "$DRIVE/drama3/$MODEL"
fi
log "完成"
ls -la "$WORK/audiocpp" "$WORK/drama3"
