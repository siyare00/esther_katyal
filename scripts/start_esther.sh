#!/bin/bash
# Start Esther trading bot — called by cron at market open
# Logs to logs/esther_cron.log

cd /Users/shawnkatyal/esther-trading

# Don't start if already running
if pgrep -f "run_live.py" > /dev/null; then
    echo "$(date): Esther already running, skipping." >> logs/esther_cron.log
    exit 0
fi

echo "$(date): Starting Esther..." >> logs/esther_cron.log

# Activate venv and run in sandbox mode with Alpaca (paper trading)
/Users/shawnkatyal/esther-trading/.venv/bin/python \
    /Users/shawnkatyal/esther-trading/scripts/run_live.py \
    --sandbox \
    --broker tradier \
    --config config-tradier.yaml \
    --log-file logs/esther-tradier.log \
    >> logs/esther_cron.log 2>&1 &

echo "$(date): Esther started (PID $!)" >> logs/esther_cron.log
