"""Shared desktop/MCP paths; generated client configuration contains no secrets."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys


def app_directory():
    if os.environ.get('VOICE_LOOM_APP_DIR'):
        return Path(os.environ['VOICE_LOOM_APP_DIR']).resolve()
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parents[1]


def resolve(data=None, port=None):
    app = app_directory()
    portable = (app/'portable.mode').exists()
    home = app/'user-data' if portable else Path(os.environ.get('LOCALAPPDATA', str(app)))/'VoiceLoom'
    settings = home/'desktop.json'
    saved = json.loads(settings.read_text(encoding='utf-8-sig')) if settings.is_file() else {}
    raw_data = data or os.environ.get('VOICE_LOOM_DATA')
    if not raw_data:
        saved_data = saved.get('data_directory')
        if saved_data:
            candidate = Path(saved_data)
            if not candidate.is_absolute():
                candidate = home / candidate
            elif portable and not candidate.exists():
                candidate = home / 'data'
            raw_data = candidate
        else:
            raw_data = home / 'data'
    directory = Path(raw_data).resolve()
    service_port = int(port or os.environ.get('VOICE_LOOM_PORT') or saved.get('port') or 17861)
    if not 1024 <= service_port <= 65535:
        raise ValueError('本机服务端口须为 1024～65535')
    return {'app': app, 'home': home, 'data': directory, 'port': service_port,
            'reports': os.environ.get('VOICE_LOOM_REPORTS') or saved.get('reports_directory')}


def command(arguments):
    if getattr(sys, 'frozen', False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve().parents[1]/'desktop/service.py'), *arguments]


def integration_configs(data, port):
    launch = command(['--mcp', '--data', str(data), '--port', str(port)])
    server = {'command': launch[0], 'args': launch[1:]}
    toml = ('[mcp_servers.voice_loom]\ncommand = '+json.dumps(server['command'], ensure_ascii=False)+
            '\nargs = '+json.dumps(server['args'], ensure_ascii=False)+
            '\nstartup_timeout_sec = 30\ntool_timeout_sec = 300\n')
    return {'transport': 'stdio', 'command': server['command'], 'args': server['args'],
            'codex_toml': toml, 'mcp_json': json.dumps({'mcpServers': {'voice_loom': server}}, ensure_ascii=False, indent=2),
            'data_directory': str(data), 'port': port}
