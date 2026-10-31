"""Frozen/local ASGI entry point; all writable data is outside the distribution."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--models", choices=['status', 'start', 'stop'])
    parser.add_argument("model", nargs='?')
    args = parser.parse_args()
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    from backend.desktop_settings import resolve
    settings = resolve(args.data, args.port)
    os.environ.update(VOICE_LOOM_DATA=str(settings['data']), VOICE_LOOM_PORT=str(settings['port']))
    if settings['reports']:
        os.environ['VOICE_LOOM_REPORTS'] = str(settings['reports'])
    if args.mcp:
        from backend.mcp_server import run
        run(settings)
        return 0
    if args.models:
        import json
        from backend.local_runtime import service_status, control_service
        try:
            value = service_status() if args.models == 'status' else control_service(args.model, args.models)
            print(json.dumps(value, ensure_ascii=False), flush=True)
            return 0
        except (ValueError, RuntimeError) as error:
            print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr, flush=True)
            return 1
    from backend.app import app
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=settings['port'], access_log=False,
                            proxy_headers=False, timeout_graceful_shutdown=12,
                            limit_concurrency=64, timeout_keep_alive=5, log_level="warning",
                            loop="asyncio", http="h11", ws="none", lifespan="on")
    server = uvicorn.Server(config)
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
