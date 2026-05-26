#!/bin/bash
set -e

# Load environment variables
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

echo "=========================================="
# 1. Sandbox Agent
echo "Starting Sandbox Agent..."
docker compose --profile sandbox up -d sandbox_agent

# 2. Communication Agent
echo "Building and starting Communication Agent..."
docker build -t agentmonorepo-communication_agent -f agents/communication_agent/Dockerfile.listener .
docker rm -f communication_agent 2>/dev/null || true
docker run -d \
  --name communication_agent \
  --network cassiopeia_default \
  --env-file .env \
  -e REDIS_URL=redis://cassiopeia:${REDIS_CASSIOPEIA_PASSWORD}@redis:6379 \
  agentmonorepo-communication_agent

# 3. Archive Agent
echo "Building and starting Archive Agent..."
docker build -t agentmonorepo-archive_agent -f agents/archive_agent/Dockerfile .
docker rm -f archive_agent 2>/dev/null || true
docker run -d \
  --name archive_agent \
  --network cassiopeia_default \
  --env-file .env \
  -e REDIS_URL=redis://cassiopeia:${REDIS_CASSIOPEIA_PASSWORD}@redis:6379 \
  -e CASSIOPEIA_URL=http://cassiopeia-cassiopeia_agent-1:49152 \
  -e TASK_ANALYZER_BACKEND=gemini \
  -e NOTION_MODE=server \
  agentmonorepo-archive_agent

# 4. Research Agent
echo "Building and starting Research Agent..."
docker build -t agentmonorepo-research_agent -f agents/research_agent/Dockerfile.alpine .
docker rm -f research_agent 2>/dev/null || true
docker run -d \
  --name research_agent \
  --network cassiopeia_default \
  --env-file .env \
  -e REDIS_URL=redis://cassiopeia:${REDIS_CASSIOPEIA_PASSWORD}@redis:6379 \
  -e CASSIOPEIA_URL=http://cassiopeia-cassiopeia_agent-1:49152 \
  agentmonorepo-research_agent

# 5. Schedule Agent
echo "Building and starting Schedule Agent..."
docker build -t agentmonorepo-schedule_agent -f agents/schedule_agent/Dockerfile.alpine .
docker rm -f schedule_agent 2>/dev/null || true
docker run -d \
  --name schedule_agent \
  --network cassiopeia_default \
  --env-file .env \
  -e REDIS_URL=redis://cassiopeia:${REDIS_CASSIOPEIA_PASSWORD}@redis:6379 \
  -e CASSIOPEIA_URL=http://cassiopeia-cassiopeia_agent-1:49152 \
  agentmonorepo-schedule_agent

echo "=========================================="
echo "All other agents have been started successfully!"
docker ps --filter "network=cassiopeia_default"
