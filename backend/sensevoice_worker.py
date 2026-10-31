"""Private stdin/stdout worker, run by the existing model Python environment."""
from __future__ import annotations
import argparse
import base64
import contextlib
import json
import os
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--packages", required=True, type=Path)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    sys.path.insert(0, str(args.packages))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np
            import torch
            from funasr import AutoModel
            torch.set_num_threads(4)
            model = AutoModel(model=str(args.model), device="cpu", disable_update=True, disable_pbar=True, ncpu=4,
                              frontend_conf={"cmvn_file": str(args.model / "am.mvn")})
            if getattr(model.kwargs["frontend"], "cmvn", None) is None:
                raise ValueError("CMVN missing")
        print(json.dumps({"ready": True}), flush=True)
    except Exception:
        print(json.dumps({"error": "SenseVoice模型加载失败，请检查模型环境与归一化文件"}, ensure_ascii=False), flush=True)
        return 1
    while True:
        line = sys.stdin.buffer.readline(6 * 1024 * 1024 + 1)
        if not line:
            return 0
        try:
            if len(line) > 6 * 1024 * 1024 or not line.endswith(b"\n"):
                raise ValueError("input limit")
            request = json.loads(line)
            language = request.get("language", "auto")
            if language not in {"auto", "zh", "yue", "en", "ja", "ko"}:
                raise ValueError("language")
            samples = np.frombuffer(base64.b64decode(request["float32"], validate=True), dtype="<f4")
            if not 1 <= len(samples) <= 16000 * 60 or not np.isfinite(samples).all():
                raise ValueError("audio limit")
            chunks = []
            with contextlib.redirect_stdout(sys.stderr):
                for start in range(0, len(samples), 16000 * 25):
                    raw = model.generate(input=samples[start:start + 16000 * 25], cache={}, language=language, use_itn=True, fs=16000)
                    chunks.append(re.sub(r"<\|[^|]+\|>", "", raw[0]["text"]).strip())
            print(json.dumps({"text": "".join(chunks)}, ensure_ascii=False), flush=True)
        except Exception:
            print(json.dumps({"error": "SenseVoice识别失败，请检查音频与语言设置"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
