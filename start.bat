@echo off
chcp 65001 > nul
echo ==========================================
echo   Cassiopeia - Startup Script
echo   카시오페아 시작 스크립트
echo ==========================================

:: ── 1. venv ─────────────────────────────────
if not exist "venv\" (
    echo [1/4] Creating virtual environment... / 가상환경 생성 중...
    python -m venv venv
)
call venv\Scripts\activate.bat
echo [1/4] Virtual environment ready. / 가상환경 준비 완료.

:: ── 2. 의존성 설치 ────────────────────────────
echo [2/4] Installing dependencies... / 의존성 설치 중...
pip install -q --no-cache-dir -r agents\cassiopeia_agent\requirements.txt
echo [2/4] Dependencies installed. / 설치 완료.

:: ── 3. .env 확인 ──────────────────────────────
if not exist ".env" (
    echo.
    echo [3/4] .env not found. Running setup wizard...
    echo [3/4] .env 파일이 없습니다. 설정 마법사를 실행합니다...
    echo.
    python tools\setup_wizard.py
) else (
    echo [3/4] .env found. / .env 파일 확인됨.
)

:: ── 4. 실행 방식 선택 ─────────────────────────
echo.
echo [4/4] How would you like to run Cassiopeia?
echo       실행 방식을 선택하세요:
echo.
echo   1) Python  (개발 / development)
echo   2) Docker  (운영 권장 / recommended for production)
echo.
set /p RUN_MODE="Select / 선택 [1/2]: "

if "%RUN_MODE%"=="1" (
    echo.
    echo Starting with Python... / Python으로 시작합니다...
    python -m agents.cassiopeia_agent.main
) else if "%RUN_MODE%"=="2" (
    echo.
    echo Starting with Docker... / Docker로 시작합니다...
    docker-compose up
) else (
    echo Invalid selection. / 잘못된 입력입니다.
    exit /b 1
)
