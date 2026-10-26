#!/bin/bash
# Start script for SastaSMS OTP Bot

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "    Starting SastaSMS Professional Bot    "
echo "=========================================="

python3 bot.py
