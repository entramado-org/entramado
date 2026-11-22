#!/usr/bin/env bash
# 063: run `qa-esc-step` back to back instead of one step per 10-min cron tick.
# Same machine, same lock and soft caps: RUNNING → poll, WAIT → sleep until the window reopens,
# HALT/DONE → stop (exit 0 on DONE, 3 on HALT). Usage: pipeline/scripts/qa_esc_loop.sh [AUDIT_DIR]
set -uo pipefail
cd "$(dirname "$0")/../.."
AUDIT=${1:-}
LOG=${QA_ESC_LOOP_LOG:-/tmp/qa_esc_loop.log}
POLL=${QA_ESC_POLL:-20}
fails=0
while true; do
  line=$(make -s qa-esc-step ${AUDIT:+AUDIT="$AUDIT"} 2>&1 | tail -1)
  echo "$(date -u +%FT%TZ) $line" >> "$LOG"
  action=$(printf '%s' "$line" | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["action"])
except Exception: print("ERROR")')
  case "$action" in
    DONE) exit 0 ;;
    HALT) exit 3 ;;
    ERROR)
      fails=$((fails + 1)); [ "$fails" -ge 5 ] && exit 4; sleep 60 ;;
    WAIT)
      fails=0
      until_ts=$(printf '%s' "$line" | python3 -c 'import json,sys,datetime as d
p=json.load(sys.stdin).get("pausedUntil")
print(int(d.datetime.fromisoformat(p).timestamp()) if isinstance(p,str) else int(p or 0))')
      wait=$(( until_ts - $(date +%s) )); [ "$wait" -lt 60 ] && wait=300
      sleep "$wait" ;;
    RUNNING) fails=0; sleep "$POLL" ;;
    *) fails=0; sleep 5 ;;
  esac
done
