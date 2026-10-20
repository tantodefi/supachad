#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Premium-grant bridge: pull paid/invited premium emails from the supachad-landing
# Worker and merge them into CHAD_OPERATOR_ALLOWLIST in the HOST credentials.json,
# then run `chad-ops gate-sync` (which pushes host -> pod and restarts the shim).
#
# The Worker records KV `premium:<email>` for every active monthly subscription and
# every `premium` invite redemption (lifetime); `GET /api/premium/list`
# (X-Admin-Secret) returns the ones currently active. The shim gate reads
# CHAD_OPERATOR_ALLOWLIST, so this is the one bridge that turns a paid signup into
# premium `chad` model access — and, when a subscription lapses, takes it away.
#
# Removal only touches emails this script added (tracked in $CHAD_PREMIUM_MANAGED).
# Operators added by hand (tantodefi, tjcooke) are never in that set, so they are
# never removed. A drop of more than half the managed set (min 3) at once is
# refused unless CHAD_PREMIUM_ALLOW_MASS_REMOVE=1 — a Worker bug returning [] must
# not revoke every subscriber. Idempotent; launchd runs it every 15 min.
#
# Env / config (from the host creds file unless overridden):
#   PREMIUM_LIST_URL   e.g. https://supachad.com/api/premium/list   (required)
#   PREMIUM_ADMIN_SECRET  the Worker's ADMIN_SECRET                 (required)
#   CHAD_HOST_CREDS    path to host credentials.json (default ~/.nemoclaw/credentials.json)
#   CHAD_PREMIUM_MANAGED  emails this script added (default ~/.nemoclaw/state/premium-managed.json)

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_CREDS="${CHAD_HOST_CREDS:-${HOME}/.nemoclaw/credentials.json}"
MANAGED="${CHAD_PREMIUM_MANAGED:-${HOME}/.nemoclaw/state/premium-managed.json}"
CHAD_OPS="${CHAD_OPS_BIN:-${here}/../chad-ops.sh}"

[ -f "$HOST_CREDS" ] || {
  echo "error: $HOST_CREDS not found" >&2
  exit 1
}

# Pull URL + secret from env, else from the creds file.
# The Worker's ADMIN_SECRET is stored as SUPACHAD_ADMIN_SECRET (shared with the
# OWUI credits filter), so fall back to it and to the production URL.
URL="${PREMIUM_LIST_URL:-$(python3 -c "import json;print(json.load(open('$HOST_CREDS')).get('PREMIUM_LIST_URL','') or 'https://supachad.com/api/premium/list')" 2>/dev/null || true)}"
SECRET="${PREMIUM_ADMIN_SECRET:-$(python3 -c "import json;c=json.load(open('$HOST_CREDS'));print(c.get('PREMIUM_ADMIN_SECRET','') or c.get('SUPACHAD_ADMIN_SECRET',''))" 2>/dev/null || true)}"
[ -n "$URL" ] && [ -n "$SECRET" ] || {
  echo "error: set PREMIUM_LIST_URL + PREMIUM_ADMIN_SECRET (env or $HOST_CREDS)" >&2
  exit 1
}

echo "[premium-sync] fetching $URL"
resp="$(curl -fsS -H "X-Admin-Secret: $SECRET" "$URL" 2>/dev/null || true)"
[ -n "$resp" ] || {
  echo "error: empty/failed response from Worker" >&2
  exit 1
}

# Reconcile CHAD_OPERATOR_ALLOWLIST with the Worker's active premium emails: add new
# ones, remove ones this script added that are no longer active. Writes atomically
# and reports what changed. RESP is passed via env.
changed="$(
  RESP="$resp" python3 - "$HOST_CREDS" "$MANAGED" <<'PY'
import json, os, sys, tempfile
creds_path, managed_path = sys.argv[1], sys.argv[2]
resp = json.loads(os.environ["RESP"])
if not resp.get("ok"):
    print("error: Worker returned not-ok: %s" % resp.get("error"), file=sys.stderr); sys.exit(2)
new = {e.strip().lower() for e in resp.get("emails", []) if e and "@" in e}
creds = json.load(open(creds_path))
cur = creds.get("CHAD_OPERATOR_ALLOWLIST", [])
cur = cur if isinstance(cur, list) else [x.strip() for x in str(cur).split(",") if x.strip()]
cur_set = {x.strip().lower() for x in cur}
try:
    managed = {x.lower() for x in json.load(open(managed_path))}
except (OSError, ValueError):
    managed = set()

added = new - cur_set              # hand-added operators already in cur_set stay unmanaged
removed = (managed - new) & cur_set
if len(removed) >= 3 and len(removed) * 2 > len(managed) and os.environ.get("CHAD_PREMIUM_ALLOW_MASS_REMOVE") != "1":
    print("error: refusing to remove %d of %d managed premium users at once "
          "(set CHAD_PREMIUM_ALLOW_MASS_REMOVE=1 if intended)" % (len(removed), len(managed)), file=sys.stderr)
    sys.exit(3)


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


next_managed = sorted((managed & new) | added)
if next_managed != sorted(managed):
    write(managed_path, next_managed)
if not added and not removed:
    print("NOCHANGE"); sys.exit(0)
creds["CHAD_OPERATOR_ALLOWLIST"] = sorted((cur_set - removed) | new)
write(creds_path, creds)
print("CHANGED added=%d removed=%d total=%d" % (len(added), len(removed), len(creds["CHAD_OPERATOR_ALLOWLIST"])))
PY
)"

echo "[premium-sync] $changed"
case "$changed" in
  CHANGED*)
    echo "[premium-sync] allowlist changed — running gate-sync"
    "$CHAD_OPS" gate-sync
    ;;
  NOCHANGE) echo "[premium-sync] nothing to do" ;;
  *)
    echo "[premium-sync] merge step failed" >&2
    exit 1
    ;;
esac
