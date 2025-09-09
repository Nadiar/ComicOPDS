FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .

# install system libs for Pillow (JPEG, PNG, WebP)
RUN apt-get update && apt-get install -y --no-install-recommends \
      libjpeg62-turbo zlib1g libpng16-16 libwebp7 wget \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app

ENV CONTENT_BASE_DIR=/library \
    PAGE_SIZE=50 \
    SERVER_BASE=http://localhost:8080 \
    URL_PREFIX= \
    OPDS_BASIC_USER= \
    OPDS_BASIC_PASS= \
    ENABLE_WATCH=true

EXPOSE 8080
VOLUME ["/data", "/library"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
