"""Exercise real MCP protocol calls and independent playback intent."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from backend.mcp_server import create_server, Workstation


class FakeWorkstation:
    def __init__(self): self.calls=[]
    def call(self, method, path, **kwargs):
        self.calls.append((method,path,kwargs))
        if path == '/state':
            return {'records':[{'id':str(index)} for index in range(15)],'listening':False,'playing':False,'stage':'idle','queue_size':0}
        if path == '/references': return []
        return {'id':'task'}


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


if __name__=='__main__': unittest.main()
