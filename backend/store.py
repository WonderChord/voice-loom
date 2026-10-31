"""原子 JSON 存储、Windows 当前用户 DPAPI 密钥保护、参考音库。"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import copy
import json
import os
from pathlib import Path
import shutil
import threading
import uuid

from .models import Config


def atomic_json(path: Path, value) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class SecretVault:
    """DPAPI 绑定当前 Windows 用户；其他系统显式拒绝存储密钥。"""

    @staticmethod
    def _crypt(data: bytes, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise ValueError("此构建的密钥保护需要 Windows DPAPI")

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        buffer = ctypes.create_string_buffer(data)
        source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        result = Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.restype = wintypes.BOOL
        # CRYPTPROTECT_UI_FORBIDDEN keeps a background service from opening prompts.
        ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
        if not ok:
            raise ValueError("Windows 密钥保护失败，请在保存密钥的 Windows 账户中运行")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
            kernel32.LocalFree(result.pbData)

    def seal(self, value: str) -> str:
        return "dpapi:" + base64.b64encode(self._crypt(value.encode("utf-8"), False)).decode("ascii")

    def open(self, value: str) -> str:
        if not value.startswith("dpapi:"):
            raise ValueError("密钥格式错误，请重新保存密钥")
        return self._crypt(base64.b64decode(value[6:]), True).decode("utf-8")


class Store:
    def __init__(self, root: Path | str, vault=None, seed_reference: bool = True):
        self.root = Path(root).resolve()
        self.runs = self.root / "runs"
        self.references_dir = self.root / "references"
        for folder in (self.root, self.runs, self.references_dir):
            folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.vault = vault or SecretVault()
        self.startup_warnings: list[str] = []
        self._secrets: dict[str, str] = {}
        self._config = Config().model_dump()
        self._references: list[dict] = []
        self._records: list[dict] = []
        self.records_revision: int = 1
        self._reversed_records_cache: list[dict] | None = None
        if (self.root / "config.json").exists():
            loaded = self._load("config.json")
            self._secrets = loaded.pop("_secrets", {})
            self._config = Config.model_validate(self._prune_unknown(loaded)).model_dump()
        if (self.root / "references.json").exists():
            self._references = self._load("references.json")
        elif seed_reference:
            self._seed_reference()
        if (self.root / "records.json").exists():
            self._records = self._load("records.json")
            for record in self._records:
                if record.get("status") not in ("done", "review", "error", "cancelled", "skipped"):
                    record.update(status="cancelled", error="服务已重启，未完成任务已取消")
            self._save_records()
        ids = {r["id"] for r in self._references}
        # The stored default is applied once at startup; live selections stay unchanged.
        self._config["active"]["asr"] = self._config["default_asr"]
        if self._config["active"]["reference"] not in ids:
            self._config["active"]["reference"] = self._references[0]["id"] if self._references else ""
        self._persist_config()

    def _load(self, name):
        try:
            return json.loads((self.root / name).read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError(f"{name} 无法读取，请保留文件并检查其内容") from exc

    @classmethod
    def _prune_unknown(cls, value, model=Config):
        """旧版程序读到新版配置时忽略不认识的字段，避免直接启动失败。"""
        if not isinstance(value, dict):
            return value
        pruned = {}
        for key, item in value.items():
            field = model.model_fields.get(key)
            if field is None:
                continue
            pruned[key] = cls._prune_field(item, field.annotation)
        return pruned

    @classmethod
    def _prune_field(cls, value, annotation):
        candidates = [annotation, *getattr(annotation, "__args__", ())]
        inner = [arg for arg in candidates if isinstance(arg, type) and hasattr(arg, "model_fields")]
        if inner:
            if isinstance(value, list):
                return [cls._prune_unknown(item, inner[0]) if isinstance(item, dict) else item for item in value]
            if isinstance(value, dict):
                return cls._prune_unknown(value, inner[0])
        return value

    def _persist_config(self):
        atomic_json(self.root / "config.json", {**self._config, "_secrets": self._secrets})

    def config(self, decrypted=False) -> dict:
        with self.lock:
            result = copy.deepcopy(self._config)
            for provider in result["providers"]:
                secret = self._secrets.get(provider["id"])
                provider["api_key"] = self.vault.open(secret) if decrypted and secret else ""
                provider["has_key"] = bool(secret)
                provider["clear_key"] = False
            return result

    def save_config(self, config: Config) -> dict:
        with self.lock:
            value = config.model_dump()
            if value["active"]["reference"] and value["active"]["reference"] not in {r["id"] for r in self._references}:
                raise ValueError("当前参考语音不存在，请重新选择")
            secrets = dict(self._secrets)
            for provider in value["providers"]:
                key = provider.pop("api_key", "").strip()
                if provider.pop("clear_key", False):
                    secrets.pop(provider["id"], None)
                elif key:
                    secrets[provider["id"]] = self.vault.seal(key)
                provider.update(api_key="", clear_key=False, has_key=provider["id"] in secrets)
            secrets = {key: secret for key, secret in secrets.items() if key in {p["id"] for p in value["providers"]}}
            # Write first: a disk failure leaves the in-memory state unchanged.
            atomic_json(self.root / "config.json", {**value, "_secrets": secrets})
            self._config, self._secrets = value, secrets
            return self.config()

    def provider(self, provider_id: str) -> dict:
        for provider in self.config(decrypted=True)["providers"]:
            if provider["id"] == provider_id:
                return provider
        raise KeyError("服务不存在")

    def references(self, private=False) -> list[dict]:
        with self.lock:
            result = copy.deepcopy(self._references)
        result.sort(key=lambda r: (not r["pinned"], not r["favorite"], r["name"]))
        if not private:
            for item in result:
                item.pop("path", None)
        return result

    def reference(self, reference_id: str, private=False) -> dict:
        for item in self.references(private):
            if item["id"] == reference_id:
                return item
        raise KeyError("参考语音不存在")

    def _commit_references(self, updated: list[dict], selected: str) -> None:
        config = copy.deepcopy(self._config)
        changed_selection = config["active"]["reference"] != selected
        config["active"]["reference"] = selected
        if changed_selection:
            atomic_json(self.root / "config.json", {**config, "_secrets": self._secrets})
        try:
            atomic_json(self.root / "references.json", updated)
        except Exception:
            if changed_selection:
                # A failed library write restores the previous saved selection.
                self._persist_config()
            raise
        # Expose the new library only after both persistent writes succeed.
        self._references, self._config = updated, config

    def add_reference(self, item: dict) -> dict:
        with self.lock:
            updated = [*self._references, copy.deepcopy(item)]
            self._commit_references(updated, self._config["active"]["reference"] or item["id"])
            return self.reference(item["id"])

    def patch_reference(self, reference_id, values):
        with self.lock:
            self.reference(reference_id)
            updated = copy.deepcopy(self._references)
            for item in updated:
                if item["id"] == reference_id:
                    item.update(values)
            atomic_json(self.root / "references.json", updated)
            self._references = updated
            return self.reference(reference_id)

    def delete_reference(self, reference_id):
        with self.lock:
            item = self.reference(reference_id, private=True)
            updated = [r for r in self._references if r["id"] != reference_id]
            selected = self._config["active"]["reference"]
            if selected == reference_id:
                selected = updated[0]["id"] if updated else ""
            self._commit_references(updated, selected)
            path = Path(item["path"]).resolve()
            if path.is_relative_to(self.references_dir):
                path.unlink(missing_ok=True)

    def records(self):
        with self.lock:
            return copy.deepcopy(self._records)

    def records_snapshot(self, since_revision: int | None = None) -> tuple[int, list[dict] | None]:
        with self.lock:
            if since_revision is not None and since_revision == self.records_revision:
                return self.records_revision, None
            if self._reversed_records_cache is None:
                self._reversed_records_cache = list(reversed(copy.deepcopy(self._records)))
            return self.records_revision, self._reversed_records_cache

    def record(self, record_id):
        with self.lock:
            for record in self._records:
                if record["id"] == record_id:
                    return copy.deepcopy(record)
        raise KeyError("记录不存在")

    def add_record(self, record):
        with self.lock:
            updated = [*self._records, copy.deepcopy(record)][-500:]
            atomic_json(self.root / "records.json", updated)
            self._records = updated
            self.records_revision += 1
            self._reversed_records_cache = None

    def update_record(self, record_id, **changes):
        with self.lock:
            for index, record in enumerate(self._records):
                if record["id"] == record_id:
                    updated = list(self._records)
                    merged = copy.deepcopy(record)
                    merged.update(copy.deepcopy(changes))
                    updated[index] = merged
                    atomic_json(self.root / "records.json", updated)
                    self._records = updated
                    self.records_revision += 1
                    self._reversed_records_cache = None
                    return

    def clear_records(self):
        with self.lock:
            atomic_json(self.root / "records.json", [])
            self._records = []
            self.records_revision += 1
            self._reversed_records_cache = None

    def _save_records(self):
        atomic_json(self.root / "records.json", self._records)
        self.records_revision += 1
        self._reversed_records_cache = None

    def audio_path(self, name: str) -> Path:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or any(c in name for c in ("\\", ":", "\x00")):
            raise ValueError("无效音频路径")
        path = (self.root / relative).resolve()
        if not (path.is_relative_to(self.runs) or path.is_relative_to(self.references_dir)) or not path.is_file():
            raise FileNotFoundError("音频不存在")
        if path.suffix.lower() not in (".wav", ".flac", ".ogg"):
            raise ValueError("无效音频文件")
        return path

    def audio_url(self, path: Path) -> str:
        return "/api/audio/" + path.resolve().relative_to(self.root).as_posix()

    def _seed_reference(self):
        source = Path("G:/AI/speech-chain/渡鸦参考.wav")
        if not source.is_file():
            return
        import numpy as np
        import soundfile as sf
        from .resample import resample_poly
        from math import gcd
        try:
            samples, rate = sf.read(source, dtype="float32", always_2d=True)
            mono = samples.mean(axis=1)
            divisor = gcd(rate, 16000)
            if rate != 16000:
                mono = resample_poly(mono, 16000 // divisor, rate // divisor)
            transcript = ""
            manifests = sorted(source.parent.glob("reference_cache/*/reference.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for manifest in manifests:
                try:
                    cached = json.loads(manifest.read_text(encoding="utf-8"))
                    reference_audio = Path(cached.get("path", ""))
                    if not reference_audio.is_file():
                        continue
                    other, other_rate = sf.read(reference_audio, dtype="float32", always_2d=True)
                    other = other.mean(axis=1)
                    if other_rate != 16000:
                        d = gcd(other_rate, 16000)
                        other = resample_poly(other, 16000 // d, other_rate // d)
                    # Legacy librosa and SciPy use different resampling filters.
                    # Match complete aligned samples with near-identical energy/correlation.
                    match = len(other) == len(mono) and np.allclose(other, mono, atol=4 / 32768, rtol=0)
                    if not match and len(other) == len(mono) and np.std(mono) > 0.001:
                        ratio = float(np.std(other) / np.std(mono))
                        match = 0.98 <= ratio <= 1.02 and float(np.corrcoef(other, mono)[0, 1]) >= 0.9995
                    if match:
                        transcript = str(cached.get("text", ""))[:12000]
                        break
                except (OSError, ValueError, RuntimeError):
                    continue
            reference_id = uuid.uuid4().hex
            target = self.references_dir / (reference_id + ".wav")
            shutil.copy2(source, target)
            self._references = [{"id": reference_id, "name": "渡鸦", "transcript": transcript, "language": "zh", "tags": ["本机参考"], "pinned": True, "favorite": True, "duration": round(len(samples) / rate, 3), "path": str(target), "audio_url": self.audio_url(target), "provider_voice_ids": {}}]
            atomic_json(self.root / "references.json", self._references)
        except (OSError, ValueError, RuntimeError) as exc:
            self.startup_warnings.append("默认参考导入失败，请从参考音库手动导入")
