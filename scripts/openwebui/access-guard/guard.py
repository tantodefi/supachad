#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# access-guard — verifies the Cloudflare Access JWT in front of OpenWebUI and the
# browser-vm relay, so identity comes from a signature instead of a header anyone
# can send.
#
# Why: OpenWebUI trusted `Cf-Access-Authenticated-User-Email`. Over the internet only
# Cloudflare can set it, but every Docker Desktop container reaches the host's
# loopback ports via host.docker.internal (including the internet-facing WordPress
# site), so any of them could sign in as any user — admin included. The relay's
# `/vm-bridge/vm` browser leg likewise trusted Access alone (every approved user).
#
# Per request:
#   - strip every client-supplied identity header (the Cf-Access email, the secret
#     header below, the relay browser key);
#   - if `Cf-Access-Jwt-Assertion` (or the CF_Authorization cookie) verifies against
#     the team's certs with this app's AUD, set GUARD_EMAIL_HEADER (the only header
#     OpenWebUI trusts — a secret name, so even containers on the compose network
#     can't guess it) and Cf-Access-Authenticated-User-Email to the token's email;
#   - `/vm-bridge/*` goes to the relay only for a verified operator email
#     (CHAD_OPERATOR_ALLOWLIST), with RELAY_BROWSER_KEY attached; everyone else 403;
#   - everything else goes to OpenWebUI. Requests without a valid token still pass
#     (API-key and service-token callers), just with no trusted identity.
#
# Env: CF_ACCESS_TEAM_DOMAIN (https://<team>.cloudflareaccess.com), CF_ACCESS_AUD,
#      GUARD_EMAIL_HEADER, RELAY_BROWSER_KEY, CHAD_OPERATOR_ALLOWLIST,
#      GUARD_UPSTREAM (default http://open-webui:8080),
#      GUARD_RELAY_UPSTREAM (default http://browser-vm-relay:8787), GUARD_PORT (8080)

from __future__ import annotations

import asyncio
import logging
import os

import aiohttp
import jwt
from aiohttp import WSMsgType, web
from yarl import URL

log = logging.getLogger("access-guard")

TEAM = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").rstrip("/")
AUD = os.environ.get("CF_ACCESS_AUD", "")
EMAIL_HEADER = os.environ.get("GUARD_EMAIL_HEADER", "")
RELAY_KEY = os.environ.get("RELAY_BROWSER_KEY", "")
UPSTREAM = os.environ.get("GUARD_UPSTREAM", "http://open-webui:8080").rstrip("/")
RELAY = os.environ.get("GUARD_RELAY_UPSTREAM", "http://browser-vm-relay:8787").rstrip("/")
OPERATORS = {
    e.strip().lower()
    for e in os.environ.get("CHAD_OPERATOR_ALLOWLIST", "").replace("\n", ",").split(",")
    if e.strip()
}

CF_EMAIL = "cf-access-authenticated-user-email"
STRIP = {CF_EMAIL, EMAIL_HEADER.lower(), "x-relay-browser-key"}
HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "content-length",
}
WS_HEADERS = {"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol"}

_jwks: jwt.PyJWKClient | None = None


def _jwks_client() -> jwt.PyJWKClient:
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(
            f"{TEAM}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600,
            headers={"User-Agent": "chad-access-guard/1"},
        )
    return _jwks


def verify_email(token: str) -> str:
    """Return the verified email in a Cloudflare Access JWT, or '' (service tokens)."""
    key = _jwks_client().get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, key, algorithms=["RS256"], audience=AUD, issuer=TEAM, leeway=30)
    return str(claims.get("email") or "").strip().lower()


async def identify(request: web.Request) -> str:
    token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
    if not token or not TEAM or not AUD:
        return ""
    try:
        return await asyncio.get_running_loop().run_in_executor(None, verify_email, token)
    except Exception as exc:  # bad signature, expired, wrong aud, certs unreachable
        log.warning("jwt rejected: %s", type(exc).__name__)
        return ""


def forward_headers(request: web.Request, email: str, relay: bool) -> dict:
    out = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in STRIP or lk in HOP:
            continue
        out[k] = v
    if email and EMAIL_HEADER:
        out[EMAIL_HEADER] = email
        out["Cf-Access-Authenticated-User-Email"] = email
    if relay and RELAY_KEY:
        out["X-Relay-Browser-Key"] = RELAY_KEY
    return out


async def _pump_ws(src, dst) -> None:
    async for msg in src:
        if msg.type == WSMsgType.TEXT:
            await dst.send_str(msg.data)
        elif msg.type == WSMsgType.BINARY:
            await dst.send_bytes(msg.data)
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
            break
    await dst.close()


async def proxy_ws(request: web.Request, url: URL, headers: dict) -> web.StreamResponse:
    protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
    up_headers = {k: v for k, v in headers.items() if k.lower() not in WS_HEADERS and k.lower() != "host"}
    session: aiohttp.ClientSession = request.app["session"]
    try:
        upstream = await session.ws_connect(
            url, headers=up_headers, protocols=protocols, autoping=False, max_msg_size=0
        )
    except aiohttp.WSServerHandshakeError as exc:
        return web.Response(status=exc.status or 502, text="upstream refused websocket")
    except aiohttp.ClientError:
        return web.Response(status=502, text="upstream unavailable")
    client = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else (), autoping=False, max_msg_size=0)
    await client.prepare(request)
    await asyncio.gather(_pump_ws(client, upstream), _pump_ws(upstream, client), return_exceptions=True)
    return client


async def handle(request: web.Request) -> web.StreamResponse:
    if request.path == "/__guard/healthz":
        return web.Response(text="ok")
    email = await identify(request)
    relay = request.path.startswith("/vm-bridge/")
    if relay and (not email or email not in OPERATORS):
        return web.Response(status=403, text="forbidden")
    base = RELAY if relay else UPSTREAM
    url = URL(base + request.raw_path, encoded=True)
    headers = forward_headers(request, email, relay)

    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await proxy_ws(request, url.with_scheme("ws" if url.scheme == "http" else "wss"), headers)

    session: aiohttp.ClientSession = request.app["session"]
    try:
        upstream = await session.request(
            request.method, url, headers=headers,
            data=request.content if request.body_exists else None, allow_redirects=False,
        )
    except aiohttp.ClientError:
        return web.Response(status=502, text="upstream unavailable")
    async with upstream:
        resp = web.StreamResponse(status=upstream.status, reason=upstream.reason)
        for k, v in upstream.headers.items():
            if k.lower() not in HOP:
                resp.headers.add(k, v)
        await resp.prepare(request)
        async for chunk in upstream.content.iter_any():
            await resp.write(chunk)
        await resp.write_eof()
        return resp


async def _session(app: web.Application):
    app["session"] = aiohttp.ClientSession(
        auto_decompress=False,
        cookie_jar=aiohttp.DummyCookieJar(),  # never share cookies between users
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
        skip_auto_headers=("User-Agent",),
    )
    yield
    await app["session"].close()


def make_app() -> web.Application:
    app = web.Application(client_max_size=0)
    app.cleanup_ctx.append(_session)
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    missing = [n for n, v in (("CF_ACCESS_TEAM_DOMAIN", TEAM), ("CF_ACCESS_AUD", AUD), ("GUARD_EMAIL_HEADER", EMAIL_HEADER)) if not v]
    if missing:
        raise SystemExit(f"access-guard: missing {', '.join(missing)}")
    web.run_app(make_app(), port=int(os.environ.get("GUARD_PORT", "8080")), access_log=None)
