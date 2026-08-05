FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY demo_agent ./demo_agent
RUN pip install --no-cache-dir .

ENV DEMO_AGENT_HOST=0.0.0.0
EXPOSE 8101
CMD ["campus-helper-demo"]
