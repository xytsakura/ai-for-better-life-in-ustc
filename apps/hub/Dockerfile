FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HUB_HOST=0.0.0.0 \
    HUB_PORT=8100 \
    HUB_RUNTIME_DIR=/app/var/hub

WORKDIR /app
COPY . /app/repository
RUN pip install --no-cache-dir -e /app/repository/apps/hub

WORKDIR /app/repository/apps/hub
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=3)"
CMD ["campus-agent-hub"]
