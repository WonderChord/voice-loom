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
    server = MCPServer('voice-loom', title='声织', version='1.2.1',
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
        """修改工作台设置。changes 支持 active、default_asr、target_language、rewrite_enabled、audit_enabled、allow_events、emotion_intensity、persona、prompt、speech_prompt、style_strength、active_style_preset、style_presets、characters、pronunciations、context_limit、audio、subtitles、appearance；对象字段按键合并。切换角色卡请用 use_character。模型的地址与密钥请在模型连接页修改。"""
        allowed = {
            'active', 'default_asr', 'target_language', 'rewrite_enabled', 'audit_enabled',
            'allow_events', 'emotion_intensity', 'persona', 'prompt', 'speech_prompt',
            'style_strength', 'active_style_preset', 'style_presets', 'context_limit',
            'audio', 'subtitles', 'appearance', 'characters', 'pronunciations',
        }
        if not changes or set(changes)-allowed:
            raise ToolError('请选择工具说明中列出的设置字段')
        current = bridge.call('GET', '/config')
        for key, value in changes.items():
            current[key] = {**current[key], **value} if isinstance(current.get(key), dict) and isinstance(value, dict) else value
        return bridge.call('PUT', '/config', json=current)

    @server.tool(annotations=read)
    def list_characters() -> dict[str, Any]:
        """列出角色卡（音色、TTS、人设、目标语言、专属读音条数）与当前角色卡。"""
        config = bridge.call('GET', '/config')
        names = {p['id']: p['name'] for p in config.get('providers') or []}
        voices = {v['id']: v['name'] for v in bridge.call('GET', '/references')}
        return {
            'active_character': config.get('active_character', ''),
            'characters': [{
                'id': c['id'], 'name': c['name'], 'note': c.get('note', ''),
                'reference': voices.get(c.get('reference', ''), c.get('reference', '')),
                'tts': names.get(c.get('tts', ''), c.get('tts', '')),
                'target_language': c.get('target_language'), 'rewrite_enabled': c.get('rewrite_enabled'),
                'pronunciations': len(c.get('pronunciations') or []),
            } for c in config.get('characters') or []],
            'global_pronunciations': len(config.get('pronunciations') or []),
        }

    @server.tool(annotations=write)
    def use_character(character: str) -> dict[str, Any]:
        """切换到角色卡（按 ID 或名称匹配）：一并切换参考音色、TTS、人设风格、目标语言、情绪强度与角色专属读音。监听中可切换，下一句生效。"""
        q = character.strip().lower()
        cards = bridge.call('GET', '/config').get('characters') or []
        exact = [c for c in cards if c['id'].lower() == q or c['name'].lower() == q]
        found = exact or [c for c in cards if q and (q in c['id'].lower() or q in c['name'].lower())]
        if not found:
            raise ToolError(f'未找到角色卡：{character}')
        if len(found) > 1:
            raise ToolError('角色卡有多个匹配：' + '、'.join(f"{c['id']}（{c['name']}）" for c in found) + '；请改用完整 ID')
        result = bridge.call('POST', f"/characters/{found[0]['id']}/apply")
        active = result['config']['active']
        return {'character': found[0]['name'], 'tts': active['tts'], 'reference': active['reference'],
                'warnings': result.get('warnings', [])}

    @server.tool(annotations=read)
    def preview_pronunciation(text: str, tts: str = '') -> dict[str, Any]:
        """预览读音词典替换后实际送给 TTS 的文本（全局词条 + 当前角色卡词条）；tts 为空时用当前 TTS。"""
        return bridge.call('POST', '/pronunciations/preview', json={'text': text, 'provider': tts})

    @server.tool(annotations=read)
    def list_models(kind: str = '') -> dict[str, Any]:
        """列出已配置的本地与云端模型连接（ASR / LLM优化 / LLM自检 / TTS）及当前默认激活状态。kind 可传 asr、llm、tts 或留空。"""
        if kind and kind not in {'asr', 'llm', 'tts'}:
            raise ToolError('kind 请选择 asr、llm、tts 或留空')
        config = bridge.call('GET', '/config')
        active = config.get('active') or {}
        default_asr = config.get('default_asr', '')
        items = []
        for p in config.get('providers') or []:
            if kind and p.get('kind') != kind:
                continue
            base = str(p.get('base_url') or '')
            is_local = p.get('type') in {'qwen_local', 'sensevoice', 'breeze', 'gptsovits', 'drama3_local'} or any(h in base for h in ('127.0.0.1', 'localhost', '[::1]'))
            items.append({
                'id': p['id'],
                'kind': p['kind'],
                'type': p['type'],
                'name': p['name'],
                'base_url': base,
                'model': p.get('model', ''),
                'is_local': is_local,
                'is_active': active.get(p['kind']) == p['id'],
                'is_audit_llm': p['kind'] == 'llm' and (active.get('audit_llm') or active.get('llm')) == p['id'],
                'is_default_asr': p['kind'] == 'asr' and default_asr == p['id'],
            })
        return {
            'active': active,
            'default_asr': default_asr,
            'rewrite_enabled': config.get('rewrite_enabled', False),
            'audit_enabled': config.get('audit_enabled', True),
            'models': items,
        }

    @server.tool(annotations=read)
    def list_devices() -> dict[str, Any]:
        """列出本机可用的音频输入设备（麦克风）与输出设备（耳机/虚拟声卡）及当前默认配置。"""
        devices = bridge.call('GET', '/devices')
        config = bridge.call('GET', '/config')
        audio = config.get('audio') or {}
        return {
            **devices,
            'configured_input_device': audio.get('input_device'),
            'configured_output_device': audio.get('output_device'),
        }

    @server.tool(annotations=read)
    def get_tts_tag_specs(provider: str = '') -> dict[str, Any]:
        """拉取每个 TTS 引擎的标签规范（如 Drama3 的 <whisper>…</whisper>、[sighs]、[laughs]、情绪标签映射等）。provider 可传 ID、名称或类型（如 drama3、drama3-local、elevenlabs），留空返回当前默认 TTS 及全部 TTS 的标签规范。"""
        config = bridge.call('GET', '/config')
        caps = bridge.call('GET', '/capabilities')
        tts_providers = [p for p in config.get('providers') or [] if p.get('kind') == 'tts']
        active_tts = (config.get('active') or {}).get('tts', '')
        key = provider.strip().lower()
        if key:
            matched = [
                p for p in tts_providers
                if p['id'].lower() == key or p['name'].lower() == key or p['type'].lower() == key
            ] or [
                p for p in tts_providers
                if key in p['id'].lower() or key in p['name'].lower() or key in p['type'].lower() or key in str(p.get('model', '')).lower()
            ]
            if not matched:
                raise ToolError(f'未找到匹配的 TTS 模型：{provider}')
            results = {
                p['id']: {
                    'id': p['id'],
                    'name': p['name'],
                    'type': p['type'],
                    'model': p.get('model', ''),
                    'is_active': p['id'] == active_tts,
                    **(caps.get(p['id']) or {}),
                }
                for p in matched
            }
            return {'matched_count': len(results), 'primary': results[matched[0]['id']], 'providers': results}
        all_specs = {
            p['id']: {
                'id': p['id'],
                'name': p['name'],
                'type': p['type'],
                'model': p.get('model', ''),
                'is_active': p['id'] == active_tts,
                **(caps.get(p['id']) or {}),
            }
            for p in tts_providers
        }
        return {
            'active_tts': active_tts,
            'active_spec': all_specs.get(active_tts),
            'providers': all_specs,
        }

    @server.tool(annotations=write)
    def set_defaults(
        reference: str = '',
        asr: str = '',
        llm: str = '',
        audit_llm: str | None = None,
        tts: str = '',
        input_device: int | str | None = None,
        output_device: int | str | None = None,
        rewrite_enabled: bool | None = None,
        audit_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """设置默认参考语音、默认 ASR/LLM优化/LLM自检/TTS 模型、默认音频输入/输出设备及流程开关。均支持按 ID、序号或名称匹配；设备传 'default' 或 '系统默认' 可恢复系统默认设备。"""
        config = bridge.call('GET', '/config')
        updated: dict[str, Any] = {}

        def match_provider(kind_name: str, query: str) -> dict[str, Any]:
            q = query.strip().lower()
            candidates = [p for p in config.get('providers') or [] if p.get('kind') == kind_name]
            exact = [p for p in candidates if p['id'].lower() == q or p['name'].lower() == q]
            if exact:
                return exact[0]
            partial = [p for p in candidates if q in p['id'].lower() or q in p['name'].lower() or q in p['type'].lower() or q in str(p.get('model', '')).lower()]
            if len(partial) == 1:
                return partial[0]
            if partial:
                names = '、'.join(f"{p['id']}（{p['name']}）" for p in partial)
                raise ToolError(f'{kind_name.upper()} 有多个匹配：{names}；请改用完整 ID')
            raise ToolError(f'未找到匹配的 {kind_name.upper()} 模型：{query}')

        if reference.strip():
            q = reference.strip().lower()
            voices = bridge.call('GET', '/references')
            exact = [v for v in voices if v['id'].lower() == q or v['name'].lower() == q]
            partial = exact or [v for v in voices if q in v['name'].lower() or q in v['id'].lower()]
            if not partial:
                raise ToolError(f'未找到匹配的参考音色：{reference}')
            if len(partial) > 1:
                names = '、'.join(f"{v['id']}（{v['name']}）" for v in partial)
                raise ToolError(f'参考音色有多个匹配：{names}；请改用完整 ID')
            chosen = partial[0]
            config['active']['reference'] = chosen['id']
            updated['reference'] = {'id': chosen['id'], 'name': chosen['name']}

        if asr.strip():
            chosen = match_provider('asr', asr)
            config['active']['asr'] = chosen['id']
            config['default_asr'] = chosen['id']
            updated['asr'] = {'id': chosen['id'], 'name': chosen['name']}

        if llm.strip():
            chosen = match_provider('llm', llm)
            config['active']['llm'] = chosen['id']
            updated['llm'] = {'id': chosen['id'], 'name': chosen['name']}

        if audit_llm is not None:
            val = audit_llm.strip()
            if not val or val.lower() in {'follow', 'same', 'default', '跟随', '跟随优化', '同上'}:
                config['active']['audit_llm'] = ''
                updated['audit_llm'] = {'id': '', 'name': '跟随 LLM 优化模型'}
            else:
                chosen = match_provider('llm', val)
                config['active']['audit_llm'] = chosen['id']
                updated['audit_llm'] = {'id': chosen['id'], 'name': chosen['name']}

        if tts.strip():
            chosen = match_provider('tts', tts)
            config['active']['tts'] = chosen['id']
            updated['tts'] = {'id': chosen['id'], 'name': chosen['name']}

        if input_device is not None or output_device is not None:
            devices = bridge.call('GET', '/devices')
            for field_name, list_key, raw_val in (
                ('input_device', 'inputs', input_device),
                ('output_device', 'outputs', output_device),
            ):
                if raw_val is None:
                    continue
                if isinstance(raw_val, str) and raw_val.strip().lower() in {'', 'default', 'none', 'null', '系统默认', '默认', '-1'}:
                    config['audio'][field_name] = None
                    updated[field_name] = {'id': None, 'name': '系统默认'}
                elif isinstance(raw_val, int) and raw_val < 0:
                    config['audio'][field_name] = None
                    updated[field_name] = {'id': None, 'name': '系统默认'}
                elif isinstance(raw_val, int) or (isinstance(raw_val, str) and raw_val.strip().isdigit()):
                    dev_id = int(raw_val)
                    avail = devices.get(list_key) or []
                    matched = next((d for d in avail if d.get('id') == dev_id), None)
                    if avail and matched is None:
                        raise ToolError(f'设备 ID {dev_id} 不在可用 {list_key} 列表中')
                    config['audio'][field_name] = dev_id
                    updated[field_name] = {'id': dev_id, 'name': matched['name'] if matched else str(dev_id)}
                else:
                    q = str(raw_val).strip().lower()
                    avail = devices.get(list_key) or []
                    exact = [d for d in avail if str(d.get('name', '')).lower() == q]
                    partial = exact or [d for d in avail if q in f"{d.get('name', '')} {d.get('hostapi', '')}".lower()]
                    if not partial:
                        raise ToolError(f'未找到匹配的音频设备：{raw_val}')
                    chosen_dev = partial[0]
                    config['audio'][field_name] = chosen_dev['id']
                    updated[field_name] = {'id': chosen_dev['id'], 'name': chosen_dev['name']}

        if rewrite_enabled is not None:
            config['rewrite_enabled'] = bool(rewrite_enabled)
            updated['rewrite_enabled'] = bool(rewrite_enabled)

        if audit_enabled is not None:
            config['audit_enabled'] = bool(audit_enabled)
            updated['audit_enabled'] = bool(audit_enabled)

        if not updated:
            raise ToolError('请至少指定一项要设置的默认项（reference、asr、llm、audit_llm、tts、input_device、output_device、rewrite_enabled、audit_enabled）')

        saved = bridge.call('PUT', '/config', json=config)
        return {'updated': updated, 'active': saved.get('active'), 'default_asr': saved.get('default_asr'), 'audio': saved.get('audio'), 'rewrite_enabled': saved.get('rewrite_enabled'), 'audit_enabled': saved.get('audit_enabled')}

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
