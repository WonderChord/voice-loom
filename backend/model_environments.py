"""Import declarative launch settings for existing local model environments."""
from __future__ import annotations
import json
import os
from pathlib import Path
import threading
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, field_validator, model_validator
from .models import Model
from .store import atomic_json

_lock = threading.RLock()


class ModelEnvironment(Model):
    id: str = Field(pattern=r'^local-[a-z0-9][a-z0-9_-]{0,55}$')
    name: str = Field(min_length=1, max_length=100)
    adapter: Literal['openai_llm', 'openai_asr', 'openai_tts', 'qwen_local', 'breeze', 'drama3_local', 'gptsovits']
    executable: str = Field(min_length=1, max_length=2048)
    script: str = Field(default='', max_length=2048)
    working_directory: str = Field(min_length=1, max_length=2048)
    arguments: list[str] = Field(default_factory=list, max_length=100)
    model_files: list[str] = Field(default_factory=list, max_length=30)
    base_url: str = Field(max_length=2048)
    health_path: str = Field(default='/health', max_length=250)
    startup_seconds: int = Field(default=180, ge=10, le=600)

    @field_validator('executable', 'script', 'working_directory')
    @classmethod
    def local_path(cls, value):
        if not value:
            return value
        path = Path(value).expanduser()
        if not path.is_absolute() or str(path).startswith('\\\\') or ':' in path.name:
            raise ValueError('启动路径请选择本机磁盘上的绝对路径')
        return str(path.resolve())

    @field_validator('arguments', 'model_files')
    @classmethod
    def bounded_values(cls, values):
        if any(not isinstance(value, str) or len(value) > 4096 or '\x00' in value for value in values):
            raise ValueError('启动参数或模型路径过长')
        return values

    @model_validator(mode='after')
    def validate_launch(self):
        exe = Path(self.executable)
        if exe.suffix.lower() != '.exe' or exe.stem.lower() in {'cmd', 'powershell', 'pwsh', 'wscript', 'cscript', 'mshta', 'rundll32', 'regsvr32', 'explorer'}:
            raise ValueError('请选择模型服务程序或 Python 可执行文件')
        if self.script and (Path(self.script).suffix.lower() != '.py' or exe.stem.lower() not in {'python', 'pythonw', 'python3', 'python3.12', 'python3.11', 'python3.10'}):
            raise ValueError('Python 启动脚本请搭配 Python 解释器使用')
        url = urlsplit(self.base_url)
        if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost'} or
                not url.port or not 1024 <= url.port <= 65535 or url.username or url.password or
                url.query or url.fragment or url.path not in {'', '/'}):
            raise ValueError('服务地址请填写 http://127.0.0.1:端口，端口为1024～65535')
        object.__setattr__(self, 'base_url', self.base_url.rstrip('/'))
        if not self.health_path.startswith('/') or self.health_path.startswith('//') or '?' in self.health_path or '#' in self.health_path or '..' in self.health_path:
            raise ValueError('健康检查路径请填写 /health 或 /v1/models 这样的本机路径')
        # Reject wildcard/public bindings in imported profiles.
        if any(value in {'0.0.0.0', '::', '[::]'} or '0.0.0.0' in value or value.startswith(('--host=', '--listen=', '--bind=')) and value.split('=', 1)[1] not in {'127.0.0.1', 'localhost', '::1'} for value in self.arguments):  # nosec B104
            raise ValueError('模型服务的监听参数请使用 127.0.0.1 或 localhost')
        for index, value in enumerate(self.arguments[:-1]):
            if value in {'--host', '--listen', '--bind', '--server-name', '--listen-address'} and self.arguments[index+1] not in {'127.0.0.1', 'localhost', '::1'}:
                raise ValueError('模型服务的监听参数请使用 127.0.0.1 或 localhost')
        object.__setattr__(self, 'model_files', [self.local_path(value) for value in self.model_files])
        return self


def location():
    from . import local_runtime
    return local_runtime.LOG_DIR.parent/'model-environments.json'


def load():
    path = location()
    if not path.exists():
        return []
    if path.stat().st_size > 256 * 1024:
        raise ValueError('本机模型环境配置过大，请检查 model-environments.json')
    content = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(content, list) or len(content) > 30:
        raise ValueError('本机模型环境最多30项')
    return [ModelEnvironment.model_validate(value) for value in content]


def inspect(profile: ModelEnvironment):
    checks = [{'name': '启动程序', 'path': profile.executable, 'ok': Path(profile.executable).is_file()},
              {'name': '工作目录', 'path': profile.working_directory, 'ok': Path(profile.working_directory).is_dir()}]
    if profile.script:
        checks.append({'name': 'Python脚本', 'path': profile.script, 'ok': Path(profile.script).is_file()})
    checks += [{'name': '模型文件', 'path': value, 'ok': Path(value).is_file()} for value in profile.model_files]
    return {'profile': profile.model_dump(), 'checks': checks, 'ready': all(item['ok'] for item in checks),
            'command': [profile.executable, *([profile.script] if profile.script else []), *profile.arguments]}


def save(profile: ModelEnvironment):
    inspection = inspect(profile)
    if not inspection['ready']:
        raise ValueError('请先补齐启动程序、工作目录、脚本和模型文件')
    from . import local_runtime
    with _lock:
        values = load()
        if any(item.id == profile.id for item in values) and local_runtime.healthy(profile.base_url, profile.id):
            raise ValueError('请先停止该环境的模型服务，再修改启动设置')
        if any(item.id != profile.id and item.base_url == profile.base_url for item in values):
            raise ValueError('该端口已分配给另一项导入环境，请选择其他端口')
        if len(values) >= 30 and not any(item.id == profile.id for item in values):
            raise ValueError('本机模型环境最多30项')
        values = [item for item in values if item.id != profile.id] + [profile]
        path = location()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, [item.model_dump() for item in values])
    local_runtime.load_environments()
    return inspection
