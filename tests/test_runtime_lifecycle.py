"""Persistent local-TTS ownership and GPU residency, using CPU-only processes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import psutil

from backend import local_runtime as runtime


class FakeProcess:
    def __init__(self, pid, events=None, parent=None):
        self.pid = pid
        self.created = float(pid)
        self.executable = "G:/models/server.exe"
        self.arguments = [self.executable, "--model", "local.gguf"]
        self.parent_pid = parent or os.getpid()
        self.alive = True
        self.descendants = []
        self.events = events if events is not None else []
        self.wait_error = None
        self.suspends = 0
        self.resumes = 0
        self.on_suspend = None

    def create_time(self):
        return self.created

    def exe(self):
        return self.executable

    def cmdline(self):
        return list(self.arguments)

    def ppid(self):
        return self.parent_pid

    def is_running(self):
        return self.alive

    def children(self, recursive=False):
        return [child for child in self.descendants if child.alive]

    def terminate(self):
        self.events.append(("terminate", self.pid))
        if self.wait_error is None:
            self.alive = False

    def suspend(self):
        self.suspends += 1
        if self.on_suspend:
            self.on_suspend()

    def resume(self):
        self.resumes += 1

    def wait(self, timeout=None):
        self.events.append(("wait", self.pid))
        if self.wait_error:
            raise self.wait_error
        return 0


class RuntimeLifecycleTests(unittest.TestCase):
    def test_drama_precision_resolves_separate_managed_deployments(self):
        self.assertEqual(runtime.runtime_name("drama3_local", "http://127.0.0.1:17863"), "drama3_local")
        self.assertEqual(runtime.runtime_name("drama3_local", "http://127.0.0.1:17865/"), "drama3_full")
        self.assertEqual(runtime.runtime_name("drama3_local", "http://127.0.0.1:19000"), "drama3_local")
        self.assertIn("drama3_full", runtime._TTS_NAMES)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.logs = Path(directory.name)
        self.patches = [patch.object(runtime, "LOG_DIR", self.logs),
                        patch.object(runtime, "_processes", {})]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.processes = {}
        process_patch = patch.object(runtime.psutil, "Process", side_effect=self.find_process)
        process_patch.start()
        self.addCleanup(process_patch.stop)

    def find_process(self, pid):
        process = self.processes.get(pid)
        if process is None or not process.alive:
            raise psutil.NoSuchProcess(pid)
        return process

    def record(self, name, *processes):
        for process in processes:
            self.processes[process.pid] = process
        result = {"owner": "voice-loom", "version": 1,
                  "project": str(runtime.PROJECT_ROOT.resolve()), "runtime": name,
                  "url": runtime.RUNTIMES[name]["url"],
                  "processes": [runtime._identity(process) for process in processes]}
        runtime._save_record(name, result)
        return result

    @patch.object(runtime, "healthy", return_value=True)
    def test_restart_reclaims_registered_tts_tree_and_keeps_asr(self, healthy):
        events = []
        parent = FakeProcess(91001, events)
        child = FakeProcess(91002, events, parent=parent.pid)
        parent.descendants = [child]
        asr = FakeProcess(91003, events)
        self.record("gptsovits_v5", parent, child)
        self.record("qwen_local", asr)
        runtime._processes.clear()  # Simulates a new app process with only disk evidence.
        runtime.ensure("drama3_local")
        self.assertEqual(events, [("terminate", child.pid), ("wait", child.pid),
                                  ("terminate", parent.pid), ("wait", parent.pid)])
        self.assertTrue(asr.alive)
        self.assertTrue(runtime._record_path("qwen_local").exists())
        self.assertFalse(runtime._record_path("gptsovits_v5").exists())

    @patch.object(runtime, "healthy", return_value=True)
    def test_orphaned_registered_child_is_reclaimed_after_parent_exits(self, healthy):
        parent, child = FakeProcess(91011), FakeProcess(91012, parent=91011)
        self.record("gptsovits_v5", parent, child)
        parent.alive = False
        runtime.ensure("breeze")
        self.assertFalse(child.alive)

    @patch.object(runtime, "healthy", return_value=True)
    def test_drama_precision_switch_releases_the_other_registered_model(self, healthy):
        for source, target in [("drama3_local", "drama3_full"), ("drama3_full", "drama3_local")]:
            with self.subTest(source=source):
                process = FakeProcess(91019)
                self.record(source, process)
                runtime.ensure(target)
                self.assertFalse(process.alive)
                self.assertFalse(runtime._record_path(source).exists())

    @patch.object(runtime, "healthy", return_value=True)
    def test_changed_identity_is_preserved(self, healthy):
        for field, value in [("created", 99999.0), ("executable", "G:/other/server.exe"),
                             ("arguments", ["G:/models/server.exe", "--model", "other.gguf"])]:
            with self.subTest(field=field):
                process = FakeProcess(91020)
                self.record("breeze", process)
                setattr(process, field, value)
                runtime.ensure("drama3_local")
                self.assertTrue(process.alive)
                self.assertEqual(process.events, [])
                self.assertTrue(runtime._record_path("breeze").exists())
                self.assertEqual(process.suspends, 0)
                self.assertEqual(process.resumes, 0)

    @patch.object(runtime, "healthy", return_value=True)
    def test_unregistered_same_command_process_is_preserved(self, healthy):
        process = FakeProcess(91030)
        self.processes[process.pid] = process
        runtime._processes["breeze"] = Mock(pid=process.pid)
        runtime.ensure("drama3_local")
        self.assertEqual(process.events, [])
        self.assertFalse(runtime._record_path("breeze").exists())

    @patch.object(runtime, "healthy", return_value=True)
    def test_custom_endpoint_and_asr_never_evict_tts(self, healthy):
        process = FakeProcess(91040)
        self.record("breeze", process)
        runtime.ensure("drama3_local", "http://127.0.0.1:19000")
        runtime.ensure("qwen_local")
        self.assertEqual(process.events, [])

    @patch.object(runtime, "healthy", return_value=False)
    def test_unhealthy_custom_endpoint_keeps_tts(self, healthy):
        process = FakeProcess(91041)
        self.record("breeze", process)
        with self.assertRaisesRegex(runtime.LocalRuntimeError, "自定义"):
            runtime.ensure("drama3_local", "http://127.0.0.1:19000")
        self.assertEqual(process.events, [])

    def test_stop_failure_blocks_new_model_loading(self):
        for error in (psutil.TimeoutExpired(15, pid=91050), psutil.AccessDenied(91050)):
            with self.subTest(error=type(error).__name__):
                process = FakeProcess(91050)
                process.wait_error = error
                self.record("breeze", process)
                with patch.object(runtime, "_ensure_runtime") as start:
                    with self.assertRaisesRegex(runtime.LocalRuntimeError, "暂缓加载"):
                        runtime.ensure("drama3_local")
                    start.assert_not_called()
                self.assertTrue(runtime._record_path("breeze").exists())
                self.assertEqual(process.suspends, 1)
                self.assertEqual(process.resumes, 1)

    @patch.object(runtime, "healthy", return_value=True)
    def test_corrupt_or_foreign_registry_does_not_grant_ownership(self, healthy):
        process = FakeProcess(91060)
        good = self.record("breeze", process)
        records = ["not json", "[]", json.dumps({**good, "project": "G:/other/app"}),
                   json.dumps({**good, "owner": "someone-else"}),
                   json.dumps({**good, "processes": [{"pid": process.pid}]})]
        for content in records:
            with self.subTest(content=content[:30]):
                runtime._record_path("breeze").write_text(content, encoding="utf-8")
                runtime.ensure("drama3_local")
                self.assertEqual(process.events, [])

    @patch.object(runtime, "healthy", return_value=True)
    def test_late_child_is_registered_before_stopping(self, healthy):
        parent, child = FakeProcess(91070), FakeProcess(91071, parent=91070)
        self.record("gptsovits_v5", parent)
        self.processes[child.pid] = child
        parent.descendants = [child]
        persisted = []
        save = runtime._save_record
        with patch.object(runtime, "_save_record", side_effect=lambda n, r: (persisted.append(copy.deepcopy(r)), save(n, r))):
            runtime.ensure("breeze")
        self.assertEqual({item["pid"] for item in persisted[0]["processes"]}, {parent.pid, child.pid})
        self.assertFalse(child.alive)

    @patch.object(runtime, "available", return_value=True)
    @patch.object(runtime, "healthy", side_effect=[False, False, True])
    def test_restart_reuses_registered_loading_process_without_duplicate(self, healthy, available):
        process = FakeProcess(91080)
        self.record("breeze", process)
        with patch.object(runtime.socket, "socket") as socket_factory, patch.object(runtime.subprocess, "Popen") as spawn:
            socket_factory.return_value.__enter__.return_value.connect_ex.return_value = 1
            runtime.ensure("breeze")
            spawn.assert_not_called()
        self.assertTrue(process.alive)

    def test_registration_write_failure_cleans_up_new_process(self):
        parent, child = FakeProcess(91090), FakeProcess(91091, parent=91090)
        parent.descendants = [child]
        self.processes.update({parent.pid: parent, child.pid: child})
        spec = {**runtime.RUNTIMES["breeze"], "exe": Path(parent.executable), "args": parent.arguments[1:]}
        with patch.dict(runtime.RUNTIMES, {"breeze": spec}), patch.object(runtime, "_save_record", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(runtime.LocalRuntimeError, "无法保存"):
                runtime._register_process("breeze", Mock(pid=parent.pid))
        self.assertFalse(parent.alive)
        self.assertFalse(child.alive)

    @patch.object(runtime, "healthy", return_value=True)
    def test_child_created_at_last_snapshot_is_frozen_and_reclaimed(self, healthy):
        parent, child = FakeProcess(91092), FakeProcess(91093, parent=91092)
        self.record("gptsovits_v5", parent)
        self.processes[child.pid] = child
        parent.on_suspend = lambda: parent.descendants.append(child)
        runtime.ensure("breeze")
        self.assertFalse(parent.alive)
        self.assertFalse(child.alive)
        self.assertEqual(child.suspends, 1)
        self.assertFalse(runtime._record_path("gptsovits_v5").exists())

    def test_failed_child_registration_retains_rollback_identity_after_parent_exit(self):
        parent, child = FakeProcess(91094), FakeProcess(91095, parent=91094)
        parent.descendants = [child]
        self.processes.update({parent.pid: parent, child.pid: child})
        spec = {**runtime.RUNTIMES["breeze"], "exe": Path(parent.executable), "args": parent.arguments[1:]}
        save = runtime._save_record

        def fail_child_save(name, record):
            if len(record["processes"]) > 1:
                parent.alive = False
                raise OSError("disk full")
            save(name, record)

        with patch.dict(runtime.RUNTIMES, {"breeze": spec}), patch.object(runtime, "_save_record", side_effect=fail_child_save):
            with self.assertRaisesRegex(runtime.LocalRuntimeError, "无法保存"):
                runtime._register_process("breeze", Mock(pid=parent.pid))
        self.assertFalse(child.alive)

    @patch.object(runtime, "available", return_value=True)
    @patch.object(runtime, "healthy", side_effect=[False, False, True])
    def test_ensure_persists_exact_identity_of_its_new_service(self, healthy, available):
        process = FakeProcess(91100)
        self.processes[process.pid] = process
        spec = {**runtime.RUNTIMES["breeze"], "exe": Path(process.executable), "args": process.arguments[1:]}
        with patch.dict(runtime.RUNTIMES, {"breeze": spec}), patch.object(runtime.socket, "socket") as socket_factory, patch.object(runtime.subprocess, "Popen", return_value=Mock(pid=process.pid)) as spawn:
            socket_factory.return_value.__enter__.return_value.connect_ex.return_value = 1
            runtime.ensure("breeze")
        recorded = json.loads(runtime._record_path("breeze").read_text(encoding="utf-8"))
        self.assertEqual(recorded["processes"], [runtime._identity(process)])
        command = [str(spec["exe"]), *spec["args"]]
        self.assertEqual(recorded["launch_cmdline"], command)
        self.assertEqual(spawn.call_args.args[0], command)

    def test_two_tts_switches_serialize_release_and_start(self):
        first_started, allow_first, second_attempted, second_released = (threading.Event() for _ in range(4))
        calls = []

        def release(name, cancel_check):
            calls.append(("release", name))
            if name == "drama3_local":
                second_released.set()

        def start(name, *args):
            calls.append(("start", name))
            if name == "breeze":
                first_started.set()
                allow_first.wait(3)

        def second():
            second_attempted.set()
            runtime.ensure("drama3_local")

        with patch.object(runtime, "_release_other_tts", side_effect=release), patch.object(runtime, "_ensure_runtime", side_effect=start):
            one = threading.Thread(target=runtime.ensure, args=("breeze",))
            two = threading.Thread(target=second)
            one.start()
            try:
                self.assertTrue(first_started.wait(2))
                two.start()
                self.assertTrue(second_attempted.wait(2))
                self.assertFalse(second_released.wait(.05))
            finally:
                allow_first.set()
                one.join(3)
                if two.ident is not None:
                    two.join(3)
            self.assertFalse(one.is_alive())
            self.assertFalse(two.is_alive())
        self.assertEqual(calls, [("release", "breeze"), ("start", "breeze"),
                                 ("release", "drama3_local"), ("start", "drama3_local")])


class RealProcessLifecycleTests(unittest.TestCase):
    def test_real_subprocess_tree_survives_app_restart_then_is_reclaimed(self):
        """Exercise Windows venv redirectors and persisted ownership without a GPU model."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            marker = folder / "child.pid"
            script = folder / "dummy_service.py"
            script.write_text(
                "import pathlib, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))\n"
                f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
                "time.sleep(60)\n", encoding="utf-8")
            spec = {**runtime.RUNTIMES["gptsovits_v5"], "exe": Path(sys.executable),
                    "args": [str(script)], "cwd": folder}
            child = subprocess.Popen([sys.executable, str(script)], cwd=folder,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            owned = []
            try:
                with patch.object(runtime, "LOG_DIR", folder), patch.object(runtime, "_processes", {}), patch.dict(runtime.RUNTIMES, {"gptsovits_v5": spec}):
                    record = runtime._register_process("gptsovits_v5", child)
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        record = runtime._refresh_record("gptsovits_v5", record)
                        if marker.exists() and len(record["processes"]) >= 2:
                            # Give the second venv redirector time to spawn its actual Python.
                            time.sleep(.2)
                            record = runtime._refresh_record("gptsovits_v5", record)
                            break
                        time.sleep(.02)
                    self.assertTrue(marker.exists())
                    self.assertGreaterEqual(len(record["processes"]), 2)
                    owned = [psutil.Process(item["pid"]) for item in record["processes"]]
                    runtime._processes.clear()
                    with patch.object(runtime, "healthy", return_value=True):
                        runtime.ensure("drama3_local")
                    self.assertTrue(all(not process.is_running() for process in owned))
                    self.assertFalse(runtime._record_path("gptsovits_v5").exists())
            finally:
                # Only handles created by this test are eligible for fallback cleanup.
                for process in reversed(owned):
                    if process.is_running():
                        process.terminate()
                        process.wait(timeout=5)
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
