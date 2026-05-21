@echo off
setlocal enabledelayedexpansion

echo [1/4] Building Communication Agent...
docker build --no-cache -t agentmonorepo-communication_agent -f agents/communication_agent/Dockerfile.listener .

echo [2/4] Building Archive Agent...
docker build --no-cache -t agentmonorepo-archive_agent -f agents/archive_agent/Dockerfile .

echo [3/4] Running Communication Agent...
docker rm -f communication_agent >nul 2>&1
docker run -d ^
  --name communication_agent ^
  --network cassiopeia_default ^
  --env-file .env ^
  -e REDIS_URL=redis://cassiopeia:fc1e856eb57e6a6f4ff28b78dd185db1@redis:6379 ^
  agentmonorepo-communication_agent

echo [4/4] Running Archive Agent...
docker rm -f archive_agent >nul 2>&1
docker run -d ^
  --name archive_agent ^
  --network cassiopeia_default ^
  --env-file .env ^
  -e REDIS_URL=redis://cassiopeia:fc1e856eb57e6a6f4ff28b78dd185db1@redis:6379 ^
  -e CASSIOPEIA_URL=http://cassiopeia-cassiopeia_agent-1:49152 ^
  -e TASK_ANALYZER_BACKEND=gemini ^
  -e NOTION_MODE=server ^
  agentmonorepo-archive_agent

echo Done!