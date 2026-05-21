@echo off
setlocal enabledelayedexpansion

echo [1/3] 일정 에이전트(Schedule Agent) 이미지 빌드 중...
docker build -t agentmonorepo-schedule_agent -f agents/schedule_agent/Dockerfile.alpine .

echo [2/3] 기존 일정 에이전트 컨테이너 정리 중...
docker rm -f schedule_agent >nul 2>&1

echo [3/3] 일정 에이전트 실행 중...
:: REDIS_URL과 CASSIOPEIA_URL을 도커 네트워크 내부 주소로 덮어씁니다.
:: .env의 CLIENT_API_KEY를 CASSIOPEIA_API_KEY로 전달합니다.
docker run -d ^
  --name schedule_agent ^
  --network cassiopeia_default ^
  --env-file .env ^
  -e REDIS_URL=redis://cassiopeia:fc1e856eb57e6a6f4ff28b78dd185db1@redis:6379 ^
  -e CASSIOPEIA_URL=http://cassiopeia-cassiopeia_agent-1:49152 ^
  agentmonorepo-schedule_agent

echo.
echo =======================================================
echo 일정 관리 에이전트가 실행되었습니다!
echo 로그 확인 명령어: docker logs -f schedule_agent
echo =======================================================
pause
