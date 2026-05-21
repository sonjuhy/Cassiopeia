@echo off
setlocal enabledelayedexpansion

echo [1/3] 커뮤니케이션 에이전트 이미지 빌드 중...
docker build -t agentmonorepo-communication_agent -f agents/communication_agent/Dockerfile.listener .

echo [2/3] 기존 커뮤니케이션 에이전트 컨테이너 정리 중...
docker rm -f communication_agent >nul 2>&1

echo [3/3] 커뮤니케이션 에이전트 실행 중...
:: REDIS_URL을 도커 네트워크 내부의 redis 컨테이너 주소로 덮어씁니다.
docker run -d ^
  --name communication_agent ^
  --network cassiopeia_default ^
  --env-file .env ^
  -e REDIS_URL=redis://cassiopeia:fc1e856eb57e6a6f4ff28b78dd185db1@redis:6379 ^
  agentmonorepo-communication_agent

echo.
echo =======================================================
echo 실행이 완료되었습니다!
echo 로그 확인 명령어: docker logs -f communication_agent
echo =======================================================
pause
