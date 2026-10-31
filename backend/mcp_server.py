"""Official MCP SDK stdio adapter for the authenticated local workstation."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from typing import Any

import requests
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .access import LocalAccess
from .desktop_settings import command


class Workstation:
    def __init__(self, settings):
        self.settings = settings
        self.base = 'http://127.0.0.1:'+str(settings['port'])
        self.start_lock = threading.Lock()

    def probe(self):
        path = self.settings['data']/'.local-access.json'
        if not path.is_file():
            return None
        access = LocalAccess(self.settings['data'])
        nonce = secrets.token_hex(32)
        try:
            with requests.Session() as client:
                client.trust_env = False
                response = client.get(self.base+'/api/health', params={'challenge': nonce}, timeout=(1, 2), allow_redirects=False)
                if response.status_code == 200 and access.same(access.health_proof(nonce), response.json().get('proof')):
                    return access
        except (requests.RequestException, ValueError):
            return None
        return None

    def access(self):
        with self.start_lock:
            access = self.probe()
            if access:
                return access
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(('127.0.0.1', self.settings['port'])) == 0:
                    raise ToolError('当前端口不是已授权的声织后台，请关闭旧后台或在 MCP 参数中选择其他端口')
            data = self.settings['data']
            data.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment.update(VOICE_LOOM_DATA=str(data), VOICE_LOOM_PORT=str(self.settings['port']),
                               VOICE_LOOM_APP_DIR=str(self.settings['app']), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            if self.settings['reports']:
                environment['VOICE_LOOM_REPORTS'] = str(self.settings['reports'])
            with (data/'mcp-backend.log').open('ab') as log:
                child = subprocess.Popen(command(['--port', str(self.settings['port']), '--data', str(data)]),
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for _ in range(100):
                access = self.probe()
                if access:
                    return access
                if child.poll() is not None:
                    raise ToolError('本机后台未能启动，请查看数据目录中的 mcp-backend.log')
                time.sleep(.3)
            child.terminate()
            raise ToolError('本机后台启动超时，请查看 mcp-backend.log')

    def call(self, method, path, **kwargs):
        access = self.access()
        with requests.Session() as client:
            client.trust_env = False
            client.headers['Authorization'] = 'Bearer '+access.browser_session
            try:
                response = client.request(method, self.base+'/api'+path, timeout=(3, 180), allow_redirects=False, **kwargs)
                value = response.json()
            except (requests.RequestException, ValueError):
                raise ToolError('声织请求未完成，请查询工作台状态后重试') from None
        if not response.ok:
            raise ToolError(value.get('detail', '声织请求失败'))
        return value

    @staticmethod
    def audio_path(value):
        original = Path(value).expanduser()
        path = original.resolve()
        if not original.is_absolute() or str(path).startswith('\\\\') or ':' in path.name:
            raise ToolError('请选择本机磁盘上的音频文件')
        if path.suffix.lower() not in {'.wav', '.flac', '.mp3', '.ogg'} or not path.is_file():
            raise ToolError('请选择已有的 WAV、FLAC、MP3 或 OGG 音频')
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ToolError('音频文件最多 25 MB')
        return path


def create_server(settings, workstation=None):
    bridge = workstation or Workstation(settings)
    server = MCPServer('voice-loom', title='声织', version='1.2.0',
        instructions='先查询状态和配置，再执行用户要求的操作。合成默认只保存文件，播放和开启麦克风使用独立工具。'
                     '需要确认的表演稿先展示给用户；调用确认合成工具须有用户确认。用户数据保存在本机数据目录。', log_level='WARNING')
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    external = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=read)
    def get_status(record_limit: int = 10) -> dict[str, Any]:
        """查询监听、播放、队列、最近记录和错误；必要时启动本机工作台后台。"""
        if not 0 <= record_limit <= 100:
            raise ToolError('record_limit 须为 0～100')
        value = bridge.call('GET', '/state')
        value['records'] = value['records'][:record_limit]
        value['data_directory'] = str(settings['data'])
        return value

    @server.tool(annotations=read)
    def get_configuration() -> dict[str, Any]:
        """读取模型、人设、目标语言与设备配置，API Key 返回已配置标记。"""
        return bridge.call('GET', '/config')

    @server.tool(annotations=write)
    def update_settings(changes: dict) -> dict[str, Any]:
        """修改工作台设置。changes 支持 active、default_asr、target_language、rewrite_enabled、persona、prompt、context_limit、audio、subtitles；对象字段按键合并。"""
        allowed = {'active', 'default_asr', 'target_language', 'rewrite_enabled', 'persona', 'prompt', 'context_limit', 'audio', 'subtitles'}
        if not changes or set(changes)-allowed:
            raise ToolError('请选择工具说明中列出的设置字段')
        current = bridge.call('GET', '/config')
        for key, value in changes.items():
            current[key] = {**current[key], **value} if isinstance(current.get(key), dict) and isinstance(value, dict) else value
        return bridge.call('PUT', '/config', json=current)

    @server.tool(annotations=read)
    def list_voices() -> dict[str, Any]:
        """列出本机参考音色和录音文字。"""
        return {'voices': bridge.call('GET', '/references')}

    @server.tool(annotations=write)
    def import_voice(file_path: str, name: str, transcript: str = '', language: str = 'zh') -> dict[str, Any]:
        """将用户指定的本机音频导入参考音色库；最长60秒、最大25MB。"""
        path = bridge.audio_path(file_path)
        with path.open('rb') as audio:
            return bridge.call('POST', '/references', files={'file': (path.name, audio)}, data={'name': name, 'transcript': transcript, 'language': language})

    @server.tool(annotations=external)
    def transcribe_voice(reference_id: str) -> dict[str, Any]:
        """用当前选择的 ASR 转写参考音色的真实台词并保存。"""
        return bridge.call('POST', '/references/'+safe_id(reference_id)+'/transcribe')

    @server.tool(annotations=external)
    def submit_text(text: str, preview_only: bool = True, play_audio: bool = False) -> dict[str, Any]:
        """提交文字，默认只预览编排；preview_only=false 时合成。默认保存音频文件，play_audio=true 才自动播放。返回任务ID。"""
        return bridge.call('POST', '/process/text', json={'text': text, 'preview_only': preview_only, 'play_audio': play_audio})

    @server.tool(annotations=external)
    def process_audio(file_path: str, preview_only: bool = True, play_audio: bool = False) -> dict[str, Any]:
        """识别用户指定的本机录音并提交编排，默认预览；返回任务ID。"""
        path = bridge.audio_path(file_path)
        with path.open('rb') as audio:
            return bridge.call('POST', '/process/audio', files={'file': (path.name, audio)},
                               data={'preview_only': str(preview_only).lower(), 'play_audio': str(play_audio).lower()})

    @server.tool(annotations=read)
    def get_record(record_id: str) -> dict[str, Any]:
        """查询任务进度、原文、译文、审核结果及生成音频的本机路径。"""
        records = bridge.call('GET', '/state')['records']
        record = next((item for item in records if item['id'] == record_id), None)
        if record is None:
            raise ToolError('记录不存在')
        if record.get('audio_url'):
            record['audio_file'] = str(settings['data']/'runs'/Path(record['audio_url']).name)
        return record

    @server.tool(annotations=external)
    def confirm_and_synthesize(record_id: str, plan: dict, play_audio: bool = False) -> dict[str, Any]:
        """用户确认台词和表演稿后提交新版本合成。保留旧记录；默认只生成文件。"""
        return bridge.call('POST', '/records/'+safe_id(record_id)+'/synthesize', json={'plan': plan, 'play_audio': play_audio})

    @server.tool(annotations=write)
    def play_record(record_id: str) -> dict[str, Any]:
        """用户要求试听时，通过所选输出设备播放已有合成音频。"""
        return bridge.call('POST', '/records/'+safe_id(record_id)+'/play')

    @server.tool(annotations=write)
    def listening(action: str) -> dict[str, Any]:
        """控制麦克风：start 开始监听，flush 提交当前片段，stop 停止监听、播放及排队任务。开启监听需用户明确要求。"""
        if action not in {'start', 'stop', 'flush'}:
            raise ToolError('action 请选择 start、stop 或 flush')
        return bridge.call('POST', '/listen/'+action)

    @server.tool(annotations=read)
    def list_model_services() -> dict[str, Any]:
        """查询已部署的本机模型、HTTP地址、运行状态和进程管理情况。"""
        return bridge.call('GET', '/runtimes')

    @server.tool(annotations=read)
    def get_debug_status(include_logs: bool = False) -> dict[str, Any]:
        """读取模型启动阶段、等待耗时、就绪/失败事件；可选择包含已脱敏的模型日志末尾。"""
        return bridge.call('GET', '/runtime/debug', params={'include_logs': str(include_logs).lower()})

    @server.tool(annotations=write)
    def model_service(name: str, action: str) -> dict[str, Any]:
        """独立启动或停止已部署模型，窗口关闭后服务继续运行。name: qwen_local、breeze、drama3_local、drama3_full、gptsovits_v5；action: start/stop。切换TTS会释放声织登记的上一套TTS。"""
        from .local_runtime import SERVICE_NAMES, load_environments
        load_environments()
        if name not in SERVICE_NAMES or action not in {'start', 'stop'}:
            raise ToolError('请选择工具说明中列出的模型及 start/stop')
        state = bridge.call('GET', '/state')
        if state['listening'] or state['stage'] != 'idle' or state['queue_size']:
            raise ToolError('请先停止监听并等待当前任务结束，再管理模型')
        environment = os.environ.copy()
        environment.update(VOICE_LOOM_DATA=str(settings['data']), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        result = subprocess.run(command(['--models', action, name, '--data', str(settings['data'])]),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, env=environment,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
        if result.returncode:
            raise ToolError('模型操作未完成，请查看数据目录 logs 中的模型日志')
        return json.loads(result.stdout)

    @server.tool(annotations=write)
    def export_session() -> dict[str, Any]:
        """把当前会话导出到本机数据目录，返回 JSON 文件路径。"""
        from .store import atomic_json
        value = bridge.call('GET', '/export/session')
        destination = settings['data']/'exports'/('session-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)+'.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(destination, value)
        return {'file': str(destination), 'records': len(value['records'])}

    @server.tool(annotations=read)
    def subtitle_outputs() -> dict[str, Any]:
        """返回本机 OBS 字幕源、只读字幕接口及文本文件路径。"""
        return {**bridge.call('GET', '/access/outputs'), 'text_file': str(settings['data']/'exports/subtitles.txt')}

    return server


def safe_id(value):
    import re
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value):
        raise ToolError('记录或音色 ID 无效')
    return value


def run(settings):
    create_server(settings).run(transport='stdio')
