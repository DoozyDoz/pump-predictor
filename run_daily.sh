#!/bin/bash
# Daily pump alert pipeline — runs at 08:07 UTC via cron
set -e
export PATH="$HOME/.local/bin:$PATH"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily_$(date +%Y%m%d_%H%M).log"

echo "=== Pump Alert Pipeline ===" | tee "$LOG"
echo "Started: $(date -u)" | tee -a "$LOG"

python3 -m src.main daily 2>&1 | tee -a "$LOG"

echo "Finished: $(date -u)" | tee -a "$LOG"
find "$LOG_DIR" -name "daily_*.log" -mtime +30 -delete
