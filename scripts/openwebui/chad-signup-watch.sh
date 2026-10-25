#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Signup approvals on the operator's phone. Every pass (launchd, every 2 min):
#   1. POST /api/signups/claim on the supachad-landing Worker — it flips new
#      `pending` signups (younger than CHAD_SIGNUP_MAX_AGE_H) to `notified` and
#      returns them, so each signup is pushed exactly once.
#   2. For each one, a detached `moshi-hook ask --require-remote` yes/no card.
#      Plain yes/no only: --options cards don't round-trip without a Moshi license.
#   3. The answer goes back to POST /api/signups/decide:
#        0 yes        → approve (Worker mints the invite and emails it)
#        1 no         → deny
#        3 timed out  → left `notified`; the operator email's review link still works
#        4 unreachable→ release (back to `pending`, retried next pass)
#
# Usage: chad-signup-watch.sh            one pass (what launchd runs)
#        chad-signup-watch.sh --ask EMAIL [NOTE]   one card (spawned by a pass)
#
# Env / config (host creds file unless overridden):
#   SIGNUP_API_BASE        default https://supachad.com
#   PREMIUM_ADMIN_SECRET   the Worker's ADMIN_SECRET (falls back to SUPACHAD_ADMIN_SECRET)
#   CHAD_SIGNUP_MAX_AGE_H  only claim signups this new (default 24)
#   CHAD_SIGNUP_ASK_TIMEOUT  card lifetime (default 24h, Moshi's max)

set -euo pipefail

HOST_CREDS="${CHAD_HOST_CREDS:-${HOME}/.nemoclaw/credentials.json}"
LOG="${CHAD_SIGNUP_LOG:-${HOME}/.nemoclaw/openwebui/chad-signup-watch.log}"
MOSHI="${MOSHI_HOOK_BIN:-moshi-hook}"
BASE="${SIGNUP_API_BASE:-https://supachad.com}"

cred() { python3 -c "import json,sys;c=json.load(open(sys.argv[1]));print(next((c[k] for k in sys.argv[2:] if c.get(k)),''))" "$HOST_CREDS" "$@" 2>/dev/null || true; }
SECRET="${PREMIUM_ADMIN_SECRET:-$(cred PREMIUM_ADMIN_SECRET SUPACHAD_ADMIN_SECRET)}"
[ -n "$SECRET" ] || {
  echo "error: no Worker admin secret (PREMIUM_ADMIN_SECRET / SUPACHAD_ADMIN_SECRET in $HOST_CREDS)" >&2
  exit 1
}

api() { # api METHOD PATH JSON
  curl -fsS -m 30 -X "$1" -H "X-Admin-Secret: $SECRET" -H "content-type: application/json" \
    --data "$3" "$BASE$2"
}

decide() { # decide EMAIL ACTION
  local body
  body="$(python3 -c 'import json,sys;print(json.dumps({"email":sys.argv[1],"action":sys.argv[2],"by":"moshi"}))' "$1" "$2")"
  api POST /api/signups/decide "$body"
}

ts() { date '+%Y-%m-%dT%H:%M:%S'; }

if [ "${1:-}" = "--ask" ]; then
  email="${2:?email}"
  note="${3:-}"
  q="New Chad signup: ${email}${note:+ — \"${note}\"}. Approve free access? (Yes emails them an invite link.)"
  set +e
  "$MOSHI" ask --require-remote --timeout "${CHAD_SIGNUP_ASK_TIMEOUT:-24h}" \
    --source supachad-signup --label "Supachad signups" "$q" </dev/null
  rc=$?
  set -e
  case "$rc" in
    0) echo "$(ts) approve $email: $(decide "$email" approve || echo FAILED)" ;;
    1) echo "$(ts) deny $email: $(decide "$email" deny || echo FAILED)" ;;
    3) echo "$(ts) timeout $email: left notified (use the review link in the operator email)" ;;
    4) echo "$(ts) moshi unreachable for $email: $(decide "$email" release || echo FAILED)" ;;
    *) echo "$(ts) moshi-hook exit $rc for $email: $(decide "$email" release || echo FAILED)" ;;
  esac
  exit 0
fi

resp="$(api POST /api/signups/claim "{\"max_age_hours\": ${CHAD_SIGNUP_MAX_AGE_H:-24}}" 2>/dev/null)" || {
  echo "$(ts) error: claim failed against $BASE" >&2
  exit 1
}

# One tab-separated line per claimed signup: email \t note (tabs/newlines stripped).
RESP="$resp" python3 -c '
import json, os
d = json.loads(os.environ["RESP"])
if not d.get("ok"):
    raise SystemExit("worker not ok: %s" % d.get("error"))
for s in d.get("signups", []):
    note = " ".join(str(s.get("note", "")).split())[:200]
    print("%s\t%s" % (s["email"], note))
' | while IFS=$'\t' read -r email note; do
  [ -n "$email" ] || continue
  echo "$(ts) claimed $email — asking on phone"
  mkdir -p "$(dirname "$LOG")"
  nohup "$0" --ask "$email" "$note" >>"$LOG" 2>&1 </dev/null &
done
