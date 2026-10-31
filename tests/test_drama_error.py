"""Safe local Drama3 failure reporting without loading a model or using a socket."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import requests
import soundfile as sf

from backend import providers


RUNTIME_ROOT = Path("G:/AI/drama3/original-runtime")
PROVIDER = {"id": "drama3-full", "type": "drama3_local", "base_url": "http://127.0.0.1:17865"}
GENERIC = "模型服务返回错误（HTTP 500）。"
STALLED = "Drama3 原版生成出现静音重复，已停止本次合成。请更换随机种子或参考语音后重试。"


def module_from_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(body, status=500):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return value


class DramaErrorAdapterTests(unittest.TestCase):
    def call_failure(self, body, provider=None, method="POST", path="/v1/tasks/run", status=500):
        with patch.object(requests.Session, "request", return_value=response(body, status)) as request:
            with self.assertRaises(providers.ProviderError) as error:
                providers._request(provider or PROVIDER, method, path)
        request.assert_called_once()
        return str(error.exception)

    def test_only_known_code_becomes_fixed_chinese_message(self):
        body = {"detail": {"error_code": "generation_stalled", "message": "C:/private/model.log traceback"}}
        self.assertEqual(self.call_failure(body), STALLED)

    def test_unknown_and_unstructured_failures_stay_generic(self):
        for body in ({"detail": {"error_code": "generation_failed", "message": "private path"}},
                     {"detail": {"error_code": "unexpected", "message": "private path"}},
                     {"detail": "The model entered a silent repetition; private path"},
                     {"error_code": "generation_stalled"}, {"detail": None}, [], "private path"):
            with self.subTest(body=body):
                self.assertEqual(self.call_failure(body), GENERIC)

    def test_other_service_route_and_status_cannot_use_mapping(self):
        body = {"detail": {"error_code": "generation_stalled"}}
        for provider, method, path in (
            ({**PROVIDER, "base_url": "http://127.0.0.1:17863"}, "POST", "/v1/tasks/run"),
            ({**PROVIDER, "base_url": "http://127.0.0.1:18865"}, "POST", "/v1/tasks/run"),
            ({**PROVIDER, "base_url": "https://example.test"}, "POST", "/v1/tasks/run"),
            ({**PROVIDER, "type": "qwen_local"}, "POST", "/v1/tasks/run"),
            (PROVIDER, "GET", "/health"), (PROVIDER, "POST", "/unknown")):
            with self.subTest(provider=provider, method=method, path=path):
                self.assertEqual(self.call_failure(body, provider, method, path), GENERIC)
        self.assertEqual(self.call_failure(body, status=400), "请求参数不被服务接受（HTTP 400）。")

    def test_malformed_error_json_stays_generic(self):
        value = requests.Response()
        value.status_code, value._content = 500, b"not-json private/model.log"
        with patch.object(requests.Session, "request", return_value=value):
            with self.assertRaisesRegex(providers.ProviderError, "^" + GENERIC + "$"):
                providers._request(PROVIDER, "POST", "/v1/tasks/run")


@unittest.skipUnless((RUNTIME_ROOT / "serve.py").is_file(), "Local original-runtime source is unavailable")
class DramaRuntimeErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime_module = module_from_file("voice_loom_drama_runtime_error_test", RUNTIME_ROOT / "serve.py")
        cls.errors = module_from_file("voice_loom_drama_errors_test", RUNTIME_ROOT / "upstream/portable/drama3_t8/errors.py")

    def injected_failure(self, exception):
        runtime = self.runtime_module.Runtime()
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            sf.write(reference, np.zeros(4800, dtype=np.float32), 16000)
            request = self.runtime_module.SpeechRequest(text="测试。", voice_ref=str(reference), reference_text="参考。")
            # Fail before Torch or the actual engine is imported or initialized.
            with patch.object(runtime, "load", side_effect=exception) as load, patch.object(self.runtime_module.LOG, "exception"):
                with self.assertRaises(self.runtime_module.HTTPException) as caught:
                    runtime.generate(request)
            load.assert_called_once()
        self.assertFalse(runtime.busy)
        return runtime, caught.exception

    def test_dedicated_failure_roundtrip_is_safe(self):
        runtime, error = self.injected_failure(self.errors.GenerationStalledError("C:/private/model.log traceback"))
        self.assertEqual(error.status_code, 500)
        self.assertEqual(error.detail, {"error_code": "generation_stalled"})
        self.assertEqual(runtime.last_error, "generation_stalled")
        with patch.object(requests.Session, "request", return_value=response({"detail": error.detail})):
            with self.assertRaises(providers.ProviderError) as caught:
                providers._request(PROVIDER, "POST", "/v1/tasks/run")
        self.assertEqual(str(caught.exception), STALLED)

    def test_other_runtime_exception_never_exposes_details(self):
        runtime, error = self.injected_failure(RuntimeError("The model entered a silent repetition; C:/private/weights"))
        self.assertEqual(error.status_code, 500)
        self.assertEqual(error.detail, {"error_code": "generation_failed"})
        self.assertEqual(runtime.last_error, "generation_failed")

    def test_actual_stall_guard_raises_dedicated_exception(self):
        # Execute the real method with a fake frame generator; no tensor code runs.
        path = RUNTIME_ROOT / "upstream/portable/drama3_t8/engine.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        engine = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DramaEngine")
        take = next(node for node in engine.body if isinstance(node, ast.FunctionDef) and node.name == "_take")
        def stopped(*args, **kwargs):
            kwargs["on_finish"]("stalled")
            return None
        namespace = {"time": time, "generate": stopped, "GenerationStalledError": self.errors.GenerationStalledError}
        exec(compile(ast.Module(body=[take], type_ignores=[]), str(path), "exec"), namespace)
        drama = SimpleNamespace(model=SimpleNamespace(model=SimpleNamespace(layers=[])))
        fake_engine = SimpleNamespace(load_model=Mock(return_value=drama))
        sampling = SimpleNamespace(validate=Mock(), max_frames=1024, temperature=.7, top_k=30, top_p=.7, seed=42)
        with self.assertRaises(self.errors.GenerationStalledError) as caught:
            namespace["_take"](fake_engine, None, "测试", "测试", sampling)
        self.assertEqual(caught.exception.error_code, "generation_stalled")


if __name__ == "__main__":
    unittest.main()
