"""云端实例上的极简鉴权反代：校验 Bearer 密钥后转发到本机模型服务。

用法：python3 auth_proxy.py --listen 8790 --upstream http://127.0.0.1:7871 --key-file /content/vl_key.txt
前面再接 cloudflared / Caddy 提供 HTTPS。
"""
import argparse
import hmac
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "authorization"}


def build(upstream: str, key: str) -> web.Application:
    expected = ("Bearer " + key).encode()

    async def handle(request: web.Request) -> web.StreamResponse:
        if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), expected):
            return web.json_response({"error": "unauthorized"}, status=401)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        body = await request.read()
        session: ClientSession = request.app["session"]
        async with session.request(request.method, upstream + request.rel_url.path_qs, headers=headers,
                                   data=body, allow_redirects=False) as upstream_response:
            payload = await upstream_response.read()
            out = {k: v for k, v in upstream_response.headers.items() if k.lower() not in HOP}
            return web.Response(status=upstream_response.status, body=payload, headers=out)

    async def start(app):
        app["session"] = ClientSession(timeout=ClientTimeout(total=900))

    async def stop(app):
        await app["session"].close()

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--key-file", required=True)
    args = parser.parse_args()
    key = Path(args.key_file).read_text(encoding="utf-8").strip()
    web.run_app(build(args.upstream.rstrip("/"), key), host="127.0.0.1", port=args.listen, access_log=None)
