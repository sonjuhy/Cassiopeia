FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/agents/archive_agent/

ENV PYTHONPATH=/app
ENV MODE=server
ENV HOST=0.0.0.0
ENV PORT=8002

# 환경변수 (런타임 주입 필요)
# NOTION_TOKEN=...
# NOTION_DATABASE_ID=...
# REDIS_URL=redis://redis:6379
# CASSIOPEIA_URL=http://cassiopeia-agent:8001
# TASK_ANALYZER_BACKEND=claude   (claude | gemini | claude_cli | gemini_cli)
# ANTHROPIC_API_KEY=...
# GEMINI_API_KEY=...

EXPOSE 8002

CMD ["python", "-m", "agents.archive_agent.main"]
