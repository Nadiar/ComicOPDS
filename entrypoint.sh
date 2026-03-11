#!/bin/sh
# Start cron in the background
cron -L 15

# Write out the crontab for root
echo "${CRON_SCHEDULE:-"0 * * * *"} cd /app && /usr/local/bin/python main.py --scan-only > /proc/1/fd/1 2>/proc/1/fd/2" | crontab -

# Execute the main container command (e.g., uvicorn)
exec "$@"
