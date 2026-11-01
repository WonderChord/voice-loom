"""Exercise real MCP protocol calls and independent playback intent."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from backend.mcp_server import create_server, Workstation
from backend.models import Config
from backend import providers as p


class FakeWorkstation:
    def __init__(self):
        self.calls = []
        self.config = Config().model_dump()
        self.voices = [{'id': 'voice-duya', 'name': '渡鸦', 'transcript': '参考台词', 'language': 'zh', 'tags': [], 'pinned': True, 'favorite': True, 'duration': 5.0, 'audio_url': '/api/audio/references/voice-duya.wav', 'provider_voice_ids': {}}]
        self.devices = {
            'inputs': [{'id': 1, 'name': '阵列麦克风 (Realtek)', 'hostapi': 'WASAPI', 'sample_rate': 48000}],
            'outputs': [{'id': 3, 'name': 'CABLE Input (VB-Audio)', 'hostapi': 'WASAPI', 'sample_rate': 48000}],
            'default_input': 1,
            'default_output': 3,
        }

    def call(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == '/state':
            return {'records': [{'id': str(index)} for index in range(15)], 'listening': False, 'playing': False, 'stage': 'idle', 'queue_size': 0}
        if path == '/references':
            return self.voices
        if path == '/config' and method == 'GET':
            return json.loads(json.dumps(self.config))
        if path == '/config' and method == 'PUT':
            self.config = json.loads(json.dumps(kwargs['json']))
            return self.config
        if path == '/devices':
            return self.devices
        if path == '/capabilities':
            return {prov['id']: p.capabilities(prov) for prov in self.config['providers']}
        return {'id': 'task'}


class MCPTests(unittest.TestCase):
    def run_client(self, exercise):
        with tempfile.TemporaryDirectory() as folder:
            bridge=FakeWorkstation()
            server=create_server({'data':Path(folder),'port':17961},bridge)
            async def run():
                async with Client(server) as client: await exercise(client,bridge)
            asyncio.run(run())

    def test_protocol_tools_status_and_annotations(self):
        async def check(client,bridge):
            listed=await client.list_tools()
            tools={tool.name:tool for tool in listed.tools}
            self.assertGreaterEqual(len(tools),16)
            self.assertTrue(tools['get_status'].annotations.read_only_hint)
            self.assertFalse(tools['play_record'].annotations.read_only_hint)
            result=await client.call_tool('get_status',{'record_limit':2})
            self.assertFalse(result.is_error)
            self.assertEqual(len(result.structured_content['records']),2)
        self.run_client(check)

    def test_default_preview_and_file_only_synthesis_are_explicit(self):
        async def check(client,bridge):
            await client.call_tool('submit_text',{'text':'测试台词'})
            self.assertEqual(bridge.calls[-1][2]['json'],{'text':'测试台词','preview_only':True,'play_audio':False})
            await client.call_tool('submit_text',{'text':'测试台词','preview_only':False})
            self.assertFalse(bridge.calls[-1][2]['json']['play_audio'])
            self.assertFalse(any('/play' in call[1] for call in bridge.calls))
        self.run_client(check)

    def test_path_traversal_and_unknown_settings_never_reach_api(self):
        async def check(client,bridge):
            result=await client.call_tool('play_record',{'record_id':'../../secrets'})
            self.assertTrue(result.is_error)
            result=await client.call_tool('update_settings',{'changes':{'executable':'anything.exe'}})
            self.assertTrue(result.is_error)
            self.assertEqual(bridge.calls,[])
        self.run_client(check)

    def test_audio_path_rejects_unc_and_non_audio(self):
        for path in ['relative.wav','C:/Windows/System32/cmd.exe','\\\\server\\share\\audio.wav','C:/test.wav:stream']:
            with self.subTest(path=path),self.assertRaises(ToolError): Workstation.audio_path(path)

    def test_mcp_set_defaults_devices_models_and_tts_tag_specs(self):
        async def check(client, bridge):
            models_res = await client.call_tool('list_models', {'kind': 'llm'})
            self.assertFalse(models_res.is_error)
            llm_ids = {m['id']: m for m in models_res.structured_content['models']}
            self.assertIn('local-llm', llm_ids)
            self.assertTrue(llm_ids['local-llm']['is_local'])
            self.assertIn('cloud-llm', llm_ids)
            self.assertFalse(llm_ids['cloud-llm']['is_local'])

            devs_res = await client.call_tool('list_devices', {})
            self.assertFalse(devs_res.is_error)
            self.assertEqual(devs_res.structured_content['inputs'][0]['id'], 1)

            spec_res = await client.call_tool('get_tts_tag_specs', {'provider': 'drama3-local'})
            self.assertFalse(spec_res.is_error)
            primary = spec_res.structured_content['primary']
            self.assertEqual(primary['tag_spec']['whisper_tag'], '<whisper>{text}</whisper>')
            self.assertEqual(primary['tag_spec']['event_tags']['sigh'], '[sighs]')
            self.assertEqual(primary['tag_spec']['emotion_tags']['sad'], '[sad]')

            set_res = await client.call_tool('set_defaults', {
                'reference': '渡鸦',
                'asr': 'SenseVoice',
                'llm': '本机 LLM',
                'audit_llm': 'cloud-llm',
                'tts': 'Drama 3 · Q4',
                'input_device': '阵列麦克风',
                'output_device': 'CABLE Input',
                'rewrite_enabled': True,
                'audit_enabled': False,
            })
            self.assertFalse(set_res.is_error)
            out = set_res.structured_content
            self.assertEqual(out['active']['reference'], 'voice-duya')
            self.assertEqual(out['active']['asr'], 'sensevoice')
            self.assertEqual(out['default_asr'], 'sensevoice')
            self.assertEqual(out['active']['llm'], 'local-llm')
            self.assertEqual(out['active']['audit_llm'], 'cloud-llm')
            self.assertEqual(out['active']['tts'], 'drama3-local')
            self.assertEqual(out['audio']['input_device'], 1)
            self.assertEqual(out['audio']['output_device'], 3)
            self.assertTrue(out['rewrite_enabled'])
            self.assertFalse(out['audit_enabled'])
        self.run_client(check)

    def test_mcp_ambiguous_match_and_provider_edits_are_refused(self):
        async def check(client, bridge):
            # 「drama」同时命中云端与两个本机 Drama3，必须拒绝而不是随手选第一个。
            before = json.loads(json.dumps(bridge.config))
            ambiguous = await client.call_tool('set_defaults', {'tts': 'drama'})
            self.assertTrue(ambiguous.is_error)
            self.assertIn('多个匹配', ambiguous.content[0].text)
            self.assertEqual(bridge.config, before)
            exact = await client.call_tool('set_defaults', {'tts': 'drama3-local'})
            self.assertFalse(exact.is_error)
            self.assertEqual(exact.structured_content['active']['tts'], 'drama3-local')
            # 改服务地址会把已保存的密钥发往新地址，只能在模型连接页操作。
            moved = await client.call_tool('update_settings', {'changes': {'providers': []}})
            self.assertTrue(moved.is_error)
        self.run_client(check)

    def test_mcp_characters_can_be_listed_and_switched_by_name(self):
        async def check(client, bridge):
            bridge.config['characters'] = [
                {'id': 'noa', 'name': '乃爱', 'reference': 'voice-duya', 'tts': 'breeze', 'pronunciations': [{}]},
                {'id': 'noa-ja', 'name': '乃爱 日语', 'reference': '', 'tts': '', 'pronunciations': []}]
            listed = await client.call_tool('list_characters', {})
            self.assertEqual(listed.structured_content['characters'][0]['reference'], '渡鸦')
            self.assertEqual(listed.structured_content['characters'][0]['pronunciations'], 1)
            ambiguous = await client.call_tool('use_character', {'character': '乃'})
            self.assertTrue(ambiguous.is_error)
            self.assertIn('多个匹配', ambiguous.content[0].text)
            original_call = bridge.call
            def call(method, path, **kwargs):
                if path == '/characters/noa/apply':
                    bridge.calls.append((method, path, kwargs))
                    return {'config': {'active': {'tts': 'breeze', 'reference': 'voice-duya'}}, 'warnings': []}
                return original_call(method, path, **kwargs)
            bridge.call = call
            used = await client.call_tool('use_character', {'character': '乃爱'})
            self.assertFalse(used.is_error)
            self.assertEqual(used.structured_content['character'], '乃爱')
            self.assertIn(('POST', '/characters/noa/apply', {}), bridge.calls)
        self.run_client(check)


if __name__=='__main__': unittest.main()
