#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Wire OpenWebUI image generation to the local ComfyUI (Z-Image Turbo) — for the
# OPERATORS ONLY. The model is unfiltered, so it must never reach public signups:
#
#   1. default user permission features.image_generation = False
#   2. group "operators" (CHAD_OPERATOR_ALLOWLIST) with image_generation = True
#   3. only then: engine = comfyui, workflow = z-image-turbo.api.json, enabled
#
# Refuses to enable the engine if step 1 or 2 isn't in place. Idempotent.
# ComfyUI = the ComfyUI Desktop app's server (127.0.0.1:8000, reached from the
# container as host.docker.internal:8000); it has to be running.
#
# Env: OWUI_URL (default http://127.0.0.1:3000), COMFYUI_URL (as the container
#      sees it, default http://host.docker.internal:8000),
#      CHAD_HOST_CREDS (default ~/.nemoclaw/credentials.json; needs
#      OPENWEBUI_API_KEY_TANTODEFI + CHAD_OPERATOR_ALLOWLIST)

import json
import os
import sys
import urllib.request
from pathlib import Path

OWUI = os.environ.get("OWUI_URL", "http://127.0.0.1:3000") + "/api/v1"
COMFY = os.environ.get("COMFYUI_URL", "http://host.docker.internal:8000")
CREDS = json.loads(Path(os.environ.get("CHAD_HOST_CREDS", Path.home() / ".nemoclaw/credentials.json")).read_text())
# --gguf: the ~7.5 GB quantized pair (needs the ComfyUI-GGUF custom node). The
# default bf16 pair (~20 GB) does not fit next to Docker on a 24 GB Mac.
GGUF = "--gguf" in sys.argv
WORKFLOW = Path(__file__).with_name("z-image-turbo-gguf.api.json" if GGUF else "z-image-turbo.api.json").read_text()
UNET = "z-image-turbo-Q4_K_M.gguf" if GGUF else "z_image_turbo_bf16.safetensors"
NODES = [
    {"type": "prompt", "key": "text", "node_ids": ["67"]},
    {"type": "negative_prompt", "key": "text", "node_ids": ["71"]},
    {"type": "model", "key": "unet_name", "node_ids": ["66"]},
    {"type": "width", "key": "width", "node_ids": ["68"]},
    {"type": "height", "key": "height", "node_ids": ["68"]},
    {"type": "n", "key": "batch_size", "node_ids": ["68"]},
    {"type": "steps", "key": "steps", "node_ids": ["70"]},
    {"type": "seed", "key": "seed", "node_ids": ["70"]},
]


def call(method, path, body=None):
    req = urllib.request.Request(
        OWUI + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + CREDS["OPENWEBUI_API_KEY_TANTODEFI"], "content-type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    raw = CREDS.get("CHAD_OPERATOR_ALLOWLIST", [])
    operators = {e.strip().lower() for e in (raw if isinstance(raw, list) else str(raw).split(",")) if "@" in e}

    perms = call("GET", "/users/default/permissions")
    if perms["features"].get("image_generation"):
        perms["features"]["image_generation"] = False
        call("POST", "/users/default/permissions", perms)
        print("default users: image_generation -> off")

    group = next((g for g in call("GET", "/groups/") if g["name"] == "operators"), None)
    if not group:
        group = call("POST", "/groups/create", {"name": "operators", "description": "Chad operators: image generation",
                                                 "permissions": {"features": {"image_generation": True}}})
    members = {u["email"].lower() for u in call("POST", f"/groups/id/{group['id']}/users", {})}
    users = call("GET", "/users/all").get("users", [])
    missing = [u["id"] for u in users if u["email"].lower() in operators and u["email"].lower() not in members]
    if missing:
        call("POST", f"/groups/id/{group['id']}/users/add", {"user_ids": missing})
    extra = members - operators
    if extra:
        print(f"refusing: operators group has non-operator members {sorted(extra)}", file=sys.stderr)
        return 2
    if call("GET", "/users/default/permissions")["features"].get("image_generation"):
        print("refusing: default users still have image_generation", file=sys.stderr)
        return 2

    cfg = call("GET", "/images/config")
    cfg.update({
        "ENABLE_IMAGE_GENERATION": True,
        "IMAGE_GENERATION_ENGINE": "comfyui",
        "IMAGE_GENERATION_MODEL": UNET,
        "IMAGE_SIZE": "1024x1024",
        "IMAGE_STEPS": 8,
        "COMFYUI_BASE_URL": COMFY,
        "COMFYUI_WORKFLOW": WORKFLOW,
        "COMFYUI_WORKFLOW_NODES": NODES,
    })
    out = call("POST", "/images/config/update", cfg)
    print(f"image generation: enabled={out['ENABLE_IMAGE_GENERATION']} engine={out['IMAGE_GENERATION_ENGINE']} "
          f"url={out['COMFYUI_BASE_URL']} size={out['IMAGE_SIZE']} steps={out['IMAGE_STEPS']}")
    print(f"operators group: {sorted(operators)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
