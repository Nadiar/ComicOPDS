FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .

# install system libs for Pillow and Cron
RUN apt-get update && apt-get install -y --no-install-recommends \
      libjpeg62-turbo zlib1g libpng16-16 libwebp7 wget cron \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV CONTENT_BASE_DIR=/library \
    PAGE_SIZE=50 \
    SERVER_BASE=http://localhost:8080 \
    URL_PREFIX= \
    OPDS_BASIC_USER= \
    OPDS_BASIC_PASS= \
    ACTIVATE_CRON=true \
    CRON_SCHEDULE="0 * * * *" \
    TRUSTED_PROXIES="10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8

EXPOSE 8080
VOLUME ["/data", "/library"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD wget -qO- http://localhost:8080/healthz | grep -q '"ok": true' || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
