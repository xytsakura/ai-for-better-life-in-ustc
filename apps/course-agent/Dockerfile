FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COURSE_AGENT_RUNTIME_DIR=/app/var/course-agent

WORKDIR /app
COPY . /app/repository
RUN pip install --no-cache-dir -e /app/repository/apps/course-agent

WORKDIR /app/repository/apps/course-agent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["uvicorn", "course_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
