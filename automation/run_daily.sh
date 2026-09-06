#!/bin/bash
# Nightly wrapper. Skips non-trading days and shouts if the run fails --
# silence must never be mistaken for "no signals today".
set -uo pipefail
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT" || exit 1
mkdir -p logs

DOW=$(date +%u)                 # 6=Sat, 7=Sun
if [ "$DOW" -ge 6 ]; then
  echo "$(date '+%F %T') weekend, skipping"
  exit 0
fi

[ -f .env ] && set -a && . ./.env && set +a

echo "=== $(date '+%F %T') daily run ==="
OUTPUT=$(PYTHONPATH="$PROJECT" "$PROJECT/.venv/bin/python" run_daily.py \
           --portfolio "${PORTFOLIO_VALUE:-1000000}" 2>&1)
STATUS=$?
echo "$OUTPUT"

if [ $STATUS -ne 0 ]; then
  MSG="Stock signals FAILED at $(date '+%F %T'):
$(echo "$OUTPUT" | tail -20)"
else
  MSG="$OUTPUT"
fi

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  PYTHONPATH="$PROJECT" "$PROJECT/.venv/bin/python" - <<PY
from delivery.telegram_bot import send
send("""$MSG""")
PY
fi
exit $STATUS
