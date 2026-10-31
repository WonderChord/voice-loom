"""Bounded model startup events shared by the UI, MCP, and standalone launcher."""
from __future__ import annotations
import json
from pathlib import Path
import re
import threading
import time
from .store import atomic_json

_lock = threading.Lock()


def note(directory: Path, name: str, stage: str, message: str, *, started=None, pid=None):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', name):
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory/(name+'-status.json')
        with _lock:
            previous = json.loads(path.read_text(encoding='utf-8')) if path.is_file() and path.stat().st_size < 65536 else {}
            timestamp = time.time()
            start = started or previous.get('started_at') or timestamp
            event = {'model': name, 'stage': stage, 'message': message, 'time': timestamp}
            events = previous.get('events', [])
            if previous.get('stage') != stage or previous.get('message') != message:
                events = [*events[-29:], event]
            atomic_json(path, {**event, 'started_at': start, 'elapsed_seconds': round(timestamp-start, 1),
                               'pid': pid or previous.get('pid'), 'events': events})
    except (OSError, ValueError, TypeError):
        # Diagnostics stay secondary to model work; unwritable data is surfaced by the job itself.
        return


def snapshot(directory: Path, include_logs=False):
    models, events = [], []
    for path in directory.glob('*-status.json'):
        try:
            if path.stat().st_size > 65536:
                continue
            value = json.loads(path.read_text(encoding='utf-8'))
            name = value.get('model', '')
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', name):
                continue
            events.extend(value.pop('events', []))
            if value.get('stage') in {'launching', 'waiting'}:
                value['elapsed_seconds'] = round(max(0, time.time()-value['started_at']), 1)
            if include_logs:
                tails=[]
                for filename in (name+'.log', name+'-error.log', 'sensevoice-worker.log' if name=='sensevoice' else ''):
                    target=directory/filename
                    if filename and target.is_file():
                        with target.open('rb') as source:
                            source.seek(max(0, target.stat().st_size-3072))
                            text=source.read(3072).decode('utf-8', errors='replace')
                        text=re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
                        tails.extend(text.splitlines()[-8:])
                value['log_tail']='\n'.join(tails)[-1600:]
            models.append(value)
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            continue
    return {'models': sorted(models,key=lambda item:item.get('time',0),reverse=True),
            'events': sorted(events,key=lambda item:item.get('time',0),reverse=True)[:100]}
