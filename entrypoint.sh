#!/bin/sh
# Start cron and write the scan schedule — unless ACTIVATE_CRON is explicitly disabled
ACTIVATE_CRON="${ACTIVATE_CRON:-true}"
if [ "$ACTIVATE_CRON" = "true" ] || [ "$ACTIVATE_CRON" = "1" ] || [ "$ACTIVATE_CRON" = "yes" ]; then
    cron -L 15
    echo "${CRON_SCHEDULE:-"0 * * * *"} cd /app && /usr/local/bin/python -m app.main --scan-only > /proc/1/fd/1 2>/proc/1/fd/2" | crontab -
fi

# Execute the main container command (e.g., uvicorn)
exec "$@"
