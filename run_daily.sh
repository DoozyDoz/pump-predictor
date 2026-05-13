#!/bin/bash
# Daily pump alert pipeline — runs at 08:00 UTC via cron
set -e

cd "/home/muhammad/Documents/01-09 Apps/01 SaaS/01.02 Alpha"
LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"

LOG="$LOG_DIR/daily_$(date +%Y%m%d_%H%M).log"

echo "=== Pump Alert Pipeline ===" | tee "$LOG"
echo "Started: $(date -u)" | tee -a "$LOG"

/usr/bin/python3 -m src.main daily 2>&1 | tee -a "$LOG"

echo "Finished: $(date -u)" | tee -a "$LOG"

# Keep only last 30 days of logs
find "$LOG_DIR" -name "daily_*.log" -mtime +30 -delete
