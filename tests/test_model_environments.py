"""Import an existing environment and start/stop a real loopback test service."""
import copy
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from backend import local_runtime as runtime
from backend.model_environments import ModelEnvironment, inspect, load, save


class ModelEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='声织 env ')
        self.root=Path(self.folder.name)
        self.logs=patch.object(runtime,'LOG_DIR',self.root/'logs');self.logs.start()
        self.runtimes=runtime.RUNTIMES.copy();self.names=runtime.SERVICE_NAMES.copy();self.ids=runtime._environment_ids.copy()

    def tearDown(self):
        for name in runtime._environment_ids-self.ids:
            record=runtime._read_record(name)
            if record: runtime._stop_registered(name,record)
        runtime.RUNTIMES.clear();runtime.RUNTIMES.update(self.runtimes)
        runtime.SERVICE_NAMES.clear();runtime.SERVICE_NAMES.update(self.names)
        runtime._environment_ids.clear();runtime._environment_ids.update(self.ids)
        self.logs.stop();self.folder.cleanup()

    def profile(self,**changes):
        return ModelEnvironment.model_validate({'id':'local-test','name':'测试环境','adapter':'openai_llm',
            'executable':sys.executable,'working_directory':str(self.root),'base_url':'http://127.0.0.1:18999',**changes})

    def test_import_inspects_and_persists_without_execution(self):
        profile=self.profile(arguments=['--host','127.0.0.1'])
        with patch.object(runtime.subprocess,'Popen') as spawn:
            self.assertTrue(inspect(profile)['ready'])
            save(profile)
            self.assertEqual(load()[0].arguments,['--host','127.0.0.1'])
            spawn.assert_not_called()
        self.assertEqual(runtime.runtime_name('openai_llm','http://127.0.0.1:18999/v1'),'local-test')

    def test_missing_environment_and_public_bind_are_rejected(self):
        self.assertFalse(inspect(self.profile(executable=str(self.root/'missing.exe')))['ready'])
        for changes in [{'base_url':'http://0.0.0.0:18999'}, {'arguments':['--host','0.0.0.0']},
                        {'executable':'C:/Windows/System32/cmd.exe'}, {'script':str(self.root/'start.cmd')},
                        {'executable':'\\\\server\\share\\python.exe'}, {'id':'../wrong'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.profile(**changes)

    def test_real_launch_registry_and_stop_with_chinese_space_paths(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        script=self.root/'test service.py'
        script.write_text('from http.server import HTTPServer,BaseHTTPRequestHandler\nimport sys\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b"{}")\n def log_message(self,*args):pass\nHTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()\n',encoding='utf-8')
        profile=self.profile(script=str(script),arguments=[str(port)],base_url=f'http://127.0.0.1:{port}')
        save(profile)
        self.assertTrue(runtime.control_service(profile.id,'start')['running'])
        self.assertEqual(runtime._read_record(profile.id)['version'],2)
        self.assertFalse(runtime.control_service(profile.id,'stop')['running'])
        self.assertFalse(runtime._record_path(profile.id).exists())
        from backend.runtime_debug import snapshot
        status=snapshot(runtime.LOG_DIR)
        self.assertEqual([item['stage'] for item in reversed(status['events'])],['launching','waiting','ready','stopped'])


if __name__=='__main__':unittest.main()
