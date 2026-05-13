#!/bin/bash
# Telegram paper trading bot daemon
export PATH="$HOME/.local/bin:$PATH"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
exec python3 -m src.bot
