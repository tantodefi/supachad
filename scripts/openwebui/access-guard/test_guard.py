# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# access-guard tests: real RS256 tokens from a throwaway key, an echo upstream that
# reports the headers it received. Run: python3 -m unittest test_guard.py
# (needs aiohttp + PyJWT[crypto], see requirements.txt)

import os
import time
import unittest

from cryptography.hazmat.primitives.asymmetric import rsa

TEAM = "https://team.cloudflareaccess.com"
AUD = "aud-tag-123"
SECRET_HDR = "X-Chad-Auth-test1234"
os.environ.update({
    "CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD, "GUARD_EMAIL_HEADER": SECRET_HDR,
    "RELAY_BROWSER_KEY": "relay-browser-key", "CHAD_OPERATOR_ALLOWLIST": "op@x.com, Op2@X.com",
})

import jwt  # noqa: E402
from aiohttp import WSMsgType, web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import guard  # noqa: E402

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Signing:
    key = KEY.public_key()


class _FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return _Signing()


guard._jwks = _FakeJWKS()


def tok(email="user@x.com", aud=AUD, iss=TEAM, key=KEY, exp=3600, **extra):
    claims = {"aud": [aud], "iss": iss, "exp": int(time.time()) + exp, "iat": int(time.time()), **extra}
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, key, algorithm="RS256")


async def echo(request):
    if request.headers.get("Upgrade", "").lower() == "websocket":
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({k.lower(): v for k, v in request.headers.items()})
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await ws.send_str("echo:" + msg.data)
        return ws
    body = await request.read()
    resp = web.json_response({"path": request.raw_path, "method": request.method, "body": body.decode(),
                              "headers": {k.lower(): v for k, v in request.headers.items()}})
    resp.set_cookie("a", "1")
    resp.headers.add("Set-Cookie", "b=2")
    return resp


class GuardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        up = web.Application()
        up.router.add_route("*", "/{t:.*}", echo)
        self.up = TestServer(up)
        await self.up.start_server()
        base = str(self.up.make_url("")).rstrip("/")
        guard.UPSTREAM = base
        guard.RELAY = base + "/relay"
        self.c = TestClient(TestServer(guard.make_app()))
        await self.c.start_server()

    async def asyncTearDown(self):
        await self.c.close()
        await self.up.close()

    async def get(self, path="/api/x", **headers):
        r = await self.c.get(path, headers=headers)
        return r.status, (await r.json() if r.status == 200 else await r.text()), r

    async def test_forged_headers_stripped_without_token(self):
        st, body, _ = await self.get(**{"Cf-Access-Authenticated-User-Email": "op@x.com", SECRET_HDR: "op@x.com",
                                        "X-Relay-Browser-Key": "relay-browser-key"})
        self.assertEqual(st, 200)
        h = body["headers"]
        self.assertNotIn("cf-access-authenticated-user-email", h)
        self.assertNotIn(SECRET_HDR.lower(), h)
        self.assertNotIn("x-relay-browser-key", h)

    async def test_valid_token_sets_verified_email(self):
        st, body, _ = await self.get(**{"Cf-Access-Jwt-Assertion": tok("User@X.com"),
                                        "Cf-Access-Authenticated-User-Email": "op@x.com"})
        h = body["headers"]
        self.assertEqual(h[SECRET_HDR.lower()], "user@x.com")
        self.assertEqual(h["cf-access-authenticated-user-email"], "user@x.com")

    async def test_cookie_token_works(self):
        r = await self.c.get("/", cookies={"CF_Authorization": tok("c@x.com")})
        self.assertEqual((await r.json())["headers"][SECRET_HDR.lower()], "c@x.com")

    async def test_bad_tokens_give_no_identity(self):
        for bad in (tok(key=OTHER), tok(aud="other"), tok(iss="https://evil.cloudflareaccess.com"),
                    tok(exp=-120), "not.a.jwt"):
            st, body, _ = await self.get(**{"Cf-Access-Jwt-Assertion": bad})
            self.assertEqual(st, 200)
            self.assertNotIn(SECRET_HDR.lower(), body["headers"], bad[:20])

    async def test_service_token_passes_without_identity(self):
        st, body, _ = await self.get(**{"Cf-Access-Jwt-Assertion": tok(email=None, common_name="svc.access"),
                                        "Authorization": "Bearer sk-api"})
        self.assertEqual(body["headers"]["authorization"], "Bearer sk-api")
        self.assertNotIn(SECRET_HDR.lower(), body["headers"])

    async def test_vm_bridge_operators_only(self):
        st, _, _ = await self.get("/vm-bridge/healthz")
        self.assertEqual(st, 403)
        st, _, _ = await self.get("/vm-bridge/healthz", **{"Cf-Access-Jwt-Assertion": tok("user@x.com")})
        self.assertEqual(st, 403)
        st, _, _ = await self.get("/vm-bridge/healthz", **{"Cf-Access-Authenticated-User-Email": "op@x.com"})
        self.assertEqual(st, 403)
        st, body, _ = await self.get("/vm-bridge/healthz?a=1", **{"Cf-Access-Jwt-Assertion": tok("op2@x.com")})
        self.assertEqual(st, 200)
        self.assertEqual(body["path"], "/relay/vm-bridge/healthz?a=1")
        self.assertEqual(body["headers"]["x-relay-browser-key"], "relay-browser-key")

    async def test_body_query_and_multi_cookies_pass_through(self):
        r = await self.c.post("/api/files/a%2Fb?x=%20y", data=b'{"m":"hi"}', headers={"content-type": "application/json"})
        body = await r.json()
        self.assertEqual(body["path"], "/api/files/a%2Fb?x=%20y")
        self.assertEqual(body["body"], '{"m":"hi"}')
        self.assertEqual(sorted(c.split("=")[0] for c in r.headers.getall("Set-Cookie")), ["a", "b"])

    async def test_websocket_proxied_with_verified_identity(self):
        ws = await self.c.ws_connect("/ws/socket.io/?EIO=4", headers={"Cf-Access-Jwt-Assertion": tok("w@x.com"),
                                                                       SECRET_HDR: "op@x.com"})
        first = await ws.receive_json()
        self.assertEqual(first[SECRET_HDR.lower()], "w@x.com")
        await ws.send_str("ping")
        self.assertEqual((await ws.receive()).data, "echo:ping")
        await ws.close()

    async def test_vm_bridge_websocket_needs_operator(self):
        r = await self.c.get("/vm-bridge/vm?chat_id=c1", headers={"Upgrade": "websocket", "Connection": "Upgrade",
                                                                   "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",  # gitleaks:allow (RFC 6455 sample nonce)
                                                                   "Sec-WebSocket-Version": "13"})
        self.assertEqual(r.status, 403)
        ws = await self.c.ws_connect("/vm-bridge/vm?chat_id=c1", headers={"Cf-Access-Jwt-Assertion": tok("op@x.com")})
        first = await ws.receive_json()
        self.assertEqual(first["x-relay-browser-key"], "relay-browser-key")
        await ws.close()


if __name__ == "__main__":
    unittest.main()
