"""
title: Chad Lite Rate Limit
author: chad
version: 0.3.1
required_open_webui_version: 0.5.0
description: Per-user metering for the free tier. Premium subscribers are unlimited; credit-metered users (who bought credits via Square) consume 1 credit/message and skip the daily cap while in balance; everyone else gets the free daily message cap. The operators' chad model and admins are exempt.
"""
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# OpenWebUI FILTER function. inlet() runs before every model call; it counts a
# user's messages per UTC day and raises (blocks) once the free-tier cap is hit,
# with an upgrade CTA. Premium models and admins are exempt. Counts persist in a
# small JSON file so a container restart doesn't reset quotas mid-day.
#
# Install: Workspace -> Functions -> + -> paste; enable globally. Or via the
# admin API: POST /api/v1/functions/create (done by the deploy step).

import json
import os
import time
from typing import Optional

from pydantic import BaseModel, Field

_STATE_PATH = os.environ.get("CHAD_RATELIMIT_STATE", "/app/backend/data/chad_ratelimit.json")


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load() -> dict:
    try:
        with open(_STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    try:
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, _STATE_PATH)
    except OSError:
        pass  # best-effort; a read-only FS just means in-memory-only for this call


# Credit metering: the supachad Worker is the source of truth for credit balances
# (topped up by Square checkouts). The filter consumes 1 credit/message via its
# admin API. Gated on CHAD_CREDITS_ADMIN_SECRET being set, so the filter still works
# (free-cap only) before the Worker is wired.
_CREDITS_API = os.environ.get("CHAD_CREDITS_API", "https://supachad.com/api/credits")
_CREDITS_SECRET = os.environ.get("CHAD_CREDITS_ADMIN_SECRET", "")


# Cloudflare blocks Python's default "Python-urllib" user agent (HTTP 403), which
# silently turned every credit/premium check into a fall-through to the free cap.
_UA = {"User-Agent": "chad-rate-limit/0.3 (+https://supachad.com)"}

_TIER_TTL_S = 600
_tier_cache: dict = {}


def _is_premium(email: str) -> bool:
    """True when the Worker reports an active premium subscription for `email`.
    Cached for 10 min per email so it's not a Worker call per message. Any error
    reads as not-premium (the user falls through to credits / the free cap)."""
    if not _CREDITS_SECRET or not email or "@" not in email:
        return False
    import time
    import urllib.parse
    import urllib.request

    hit = _tier_cache.get(email)
    if hit and time.time() - hit[1] < _TIER_TTL_S:
        return hit[0]
    url = _CREDITS_API.rstrip("/").rsplit("/", 1)[0] + "/tier/" + urllib.parse.quote(email)
    try:
        req = urllib.request.Request(url, headers={**_UA, "X-Admin-Secret": _CREDITS_SECRET})
        with urllib.request.urlopen(req, timeout=3) as resp:
            premium = json.loads(resp.read() or b"{}").get("tier") == "premium"
    except Exception:
        return False
    _tier_cache[email] = (premium, time.time())
    return premium


def _consume_credit(email: str, amount: int = 1):
    """Consume `amount` credits for `email` via the Worker. Returns a tuple
    (status, balance): status ∈ "ok" (consumed — allow, skip daily cap),
    "insufficient" (0 balance), "unconfigured" (no secret/email), "error"
    (network/other). Any non-"ok" makes the caller fall through to the free daily
    cap — so a Worker outage never hard-blocks, it just reverts to free limits."""
    if not _CREDITS_SECRET or not email or "@" not in email:
        return ("unconfigured", 0)
    import urllib.error
    import urllib.request

    body = json.dumps({"email": email, "amount": amount}).encode()
    req = urllib.request.Request(
        _CREDITS_API.rstrip("/") + "/consume",
        data=body,
        headers={**_UA, "content-type": "application/json", "X-Admin-Secret": _CREDITS_SECRET},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read() or b"{}")
            return ("ok", int(d.get("balance", 0))) if d.get("ok") else ("insufficient", int(d.get("balance", 0)))
    except urllib.error.HTTPError as e:
        if e.code == 402:
            try:
                return ("insufficient", int(json.loads(e.read() or b"{}").get("balance", 0)))
            except Exception:
                return ("insufficient", 0)
        return ("error", 0)
    except Exception:
        return ("error", 0)


class Filter:
    class Valves(BaseModel):
        free_daily_limit: int = Field(
            default=50, description="Max messages/day for free-tier (non-exempt) models."
        )
        exempt_models: str = Field(
            default="chad",
            description="Comma-separated model ids exempt from the cap (premium).",
        )
        exempt_admins: bool = Field(
            default=True, description="Admins are never rate-limited."
        )
        upgrade_message: str = Field(
            default=(
                "You've reached today's free Chad Lite limit ({limit} messages). "
                "It resets at 00:00 UTC.\n\n"
                "**Two ways to keep going:**\n"
                "- **Buy credits** (pay-as-you-go) — each credit = 1 message, no daily cap while in balance.\n"
                "- **Go premium** — unlimited messages, $15/month.\n\n"
                "Get both at https://supachad.com."
            ),
            description="Shown when the free cap is hit. {limit} and {model} are substituted.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _exempt(self, model_id: str) -> bool:
        ids = {m.strip() for m in self.valves.exempt_models.split(",") if m.strip()}
        # Match the base id too (OWUI may prefix connection ids).
        return any(model_id == e or model_id.endswith("." + e) or e in model_id.split("/") for e in ids)

    async def inlet(self, body: dict, __event_emitter__=None, __user__: Optional[dict] = None) -> dict:
        model_id = str(body.get("model", "") or "")
        if self._exempt(model_id):
            return body
        user = __user__ or {}
        if self.valves.exempt_admins and user.get("role") == "admin":
            return body
        email = user.get("email") or ""
        uid = user.get("id") or email or "anon"

        # Premium subscribers: unlimited, and they never burn credits.
        if _is_premium(email):
            return body

        # Credit-metered users (bought credits via Square) pay per message and
        # skip the free daily cap while they have a balance. Any non-"ok" result
        # (no credits, or Worker unreachable) falls through to the free cap below.
        if _CREDITS_SECRET and email:
            status, balance = _consume_credit(email)
            if status == "ok":
                if __event_emitter__:  # balance display: a small OWUI status line
                    try:
                        await __event_emitter__({
                            "type": "status",
                            "data": {"description": f"💳 {balance} credits remaining", "done": True},
                        })
                    except Exception:
                        pass
                return body

        state = _load()
        day = _today()
        if state.get("day") != day:
            state = {"day": day, "counts": {}}
        counts = state.setdefault("counts", {})
        used = int(counts.get(uid, 0))

        if used >= self.valves.free_daily_limit:
            raise Exception(
                self.valves.upgrade_message.format(
                    limit=self.valves.free_daily_limit, model=model_id
                )
            )

        counts[uid] = used + 1
        _save(state)
        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body
