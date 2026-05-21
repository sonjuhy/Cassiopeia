@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

echo ==========================================
echo   Cassiopeia - Startup Script
echo   카시오페아 시작 스크립트
echo ==========================================
echo.

:: ── 1. 언어 선택 ──────────────────────────────────────────────────────────────
echo Select language / 언어를 선택하세요
echo   [1] English
echo   [2] 한국어
set /p _LANG="  → "
if "!_LANG!"=="" set _LANG=1

if "!_LANG!"=="2" (
  set L_ENV_OK=.env 파일 확인됨.
  set L_ENV_SETUP=.env 파일이 없습니다. 설정을 시작합니다.
  set L_LLM=LLM 백엔드 선택 [gemini/claude/local, 기본값: gemini]:
  set L_GEMINI=GEMINI_API_KEY 입력:
  set L_CLAUDE=ANTHROPIC_API_KEY 입력:
  set L_LOCAL_URL=LOCAL_LLM_BASE_URL [기본값: http://localhost:11434/v1]:
  set L_LOCAL_MODEL=LOCAL_LLM_MODEL [기본값: llama3.2]:
  set L_USE_SLACK=Slack 연동 설정? (y/N):
  set L_SLACK_BOT=  SLACK_BOT_TOKEN (xoxb-...):
  set L_SLACK_APP=  SLACK_APP_TOKEN (xapp-...):
  set L_SLACK_CH=  SLACK_CHANNEL (C0...):
  set L_USE_NOTION=Notion 연동 설정? (y/N):
  set L_NOTION_TOKEN=  NOTION_TOKEN:
  set L_NOTION_DB=  NOTION_DATABASE_ID:
  set L_SECRETS=보안 키 설정 (비워두면 자동 생성):
  set L_ADMIN=  ADMIN_API_KEY:
  set L_CLIENT=  CLIENT_API_KEY:
  set L_HMAC=  DISPATCH_HMAC_SECRET:
  set L_ENC=  ENCRYPTION_KEY:
  set L_R_CASS=  REDIS_CASSIOPEIA_PASSWORD:
  set L_R_COMM=  REDIS_COMMUNITY_PASSWORD:
  set L_ENV_DONE=.env 파일이 생성되었습니다.
  set L_MISSING_KEYS=다음 필수 키가 .env에 없거나 비어 있습니다:
  set L_FILL_MISSING=값을 입력하세요 (자동 생성 항목은 Enter로 건너뜁니다):
  set L_MISSING_AUTO=Enter 입력 시 자동 생성
  set L_ENV_UPDATED=.env 파일이 업데이트되었습니다.
  set L_RUN=실행 방식을 선택하세요:
  set L_RUN1=  1) Python  (개발 환경)
  set L_RUN2=  2) Docker  (운영 권장)
  set L_RUN_SEL=선택 [1/2]:
  set L_VENV=가상환경 생성 및 의존성 설치 중...
  set L_VENV_OK=준비 완료.
  set L_PY=Python으로 시작합니다...
  set L_DOCKER=Docker로 시작합니다...
  set L_INVALID=잘못된 입력입니다.
) else (
  set L_ENV_OK=.env found.
  set L_ENV_SETUP=.env not found. Starting setup.
  set L_LLM=LLM backend [gemini/claude/local, default: gemini]:
  set L_GEMINI=GEMINI_API_KEY:
  set L_CLAUDE=ANTHROPIC_API_KEY:
  set L_LOCAL_URL=LOCAL_LLM_BASE_URL [default: http://localhost:11434/v1]:
  set L_LOCAL_MODEL=LOCAL_LLM_MODEL [default: llama3.2]:
  set L_USE_SLACK=Set up Slack integration? (y/N):
  set L_SLACK_BOT=  SLACK_BOT_TOKEN (xoxb-...):
  set L_SLACK_APP=  SLACK_APP_TOKEN (xapp-...):
  set L_SLACK_CH=  SLACK_CHANNEL (C0...):
  set L_USE_NOTION=Set up Notion integration? (y/N):
  set L_NOTION_TOKEN=  NOTION_TOKEN:
  set L_NOTION_DB=  NOTION_DATABASE_ID:
  set L_SECRETS=Configure security keys (leave blank to auto-generate):
  set L_ADMIN=  ADMIN_API_KEY:
  set L_CLIENT=  CLIENT_API_KEY:
  set L_HMAC=  DISPATCH_HMAC_SECRET:
  set L_ENC=  ENCRYPTION_KEY:
  set L_R_CASS=  REDIS_CASSIOPEIA_PASSWORD:
  set L_R_COMM=  REDIS_COMMUNITY_PASSWORD:
  set L_ENV_DONE=.env file created.
  set L_MISSING_KEYS=Missing required keys detected in .env:
  set L_FILL_MISSING=Enter values below (press Enter to auto-generate where applicable):
  set L_MISSING_AUTO=Enter to auto-generate
  set L_ENV_UPDATED=.env updated with missing keys.
  set L_RUN=How would you like to run Cassiopeia?
  set L_RUN1=  1) Python  (development)
  set L_RUN2=  2) Docker  (recommended for production)
  set L_RUN_SEL=Select [1/2]:
  set L_VENV=Setting up virtual environment and installing dependencies...
  set L_VENV_OK=Ready.
  set L_PY=Starting with Python...
  set L_DOCKER=Starting with Docker...
  set L_INVALID=Invalid selection.
)

:: ── 2. .env ───────────────────────────────────────────────────────────────────
echo.
if not exist ".env" goto :create_env

echo [1/3] !L_ENV_OK!

:: Load key=value pairs from .env into environment (skip # comment lines)
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    set "_tmp_key=%%A"
    if not "!_tmp_key:~0,1!"=="#" if not "!_tmp_key!"=="" (
        set "%%A=%%B"
    )
)
if "!LLM_BACKEND!"=="" set LLM_BACKEND=gemini

:: Detect missing or empty required keys
set _HAS_MISSING=0
if "!LLM_BACKEND!"=="gemini" if "!GEMINI_API_KEY!"==""    set _HAS_MISSING=1
if "!LLM_BACKEND!"=="claude" if "!ANTHROPIC_API_KEY!"=="" set _HAS_MISSING=1
if "!ADMIN_API_KEY!"==""             set _HAS_MISSING=1
if "!CLIENT_API_KEY!"==""            set _HAS_MISSING=1
if "!ENCRYPTION_KEY!"==""            set _HAS_MISSING=1
if "!REDIS_CASSIOPEIA_PASSWORD!"=="" set _HAS_MISSING=1
if "!REDIS_COMMUNITY_PASSWORD!"==""  set _HAS_MISSING=1
if "!DISPATCH_HMAC_SECRET!"==""      set _HAS_MISSING=1
if "!SANDBOX_API_KEY!"==""           set _HAS_MISSING=1

if "!_HAS_MISSING!"=="0" goto :ask_run

:: Show missing keys
echo.
echo !L_MISSING_KEYS!
if "!LLM_BACKEND!"=="gemini" if "!GEMINI_API_KEY!"==""    echo   - GEMINI_API_KEY
if "!LLM_BACKEND!"=="claude" if "!ANTHROPIC_API_KEY!"=="" echo   - ANTHROPIC_API_KEY
if "!ADMIN_API_KEY!"==""             echo   - ADMIN_API_KEY
if "!CLIENT_API_KEY!"==""            echo   - CLIENT_API_KEY
if "!ENCRYPTION_KEY!"==""            echo   - ENCRYPTION_KEY
if "!REDIS_CASSIOPEIA_PASSWORD!"=="" echo   - REDIS_CASSIOPEIA_PASSWORD
if "!REDIS_COMMUNITY_PASSWORD!"==""  echo   - REDIS_COMMUNITY_PASSWORD
if "!DISPATCH_HMAC_SECRET!"==""      echo   - DISPATCH_HMAC_SECRET
if "!SANDBOX_API_KEY!"==""           echo   - SANDBOX_API_KEY
echo.
echo !L_FILL_MISSING!
echo.

:: Prompt and fill each missing key
if "!LLM_BACKEND!"=="gemini" if "!GEMINI_API_KEY!"=="" (
    set /p GEMINI_API_KEY="  GEMINI_API_KEY: "
    call :upsert_env GEMINI_API_KEY "!GEMINI_API_KEY!"
)
if "!LLM_BACKEND!"=="claude" if "!ANTHROPIC_API_KEY!"=="" (
    set /p ANTHROPIC_API_KEY="  ANTHROPIC_API_KEY: "
    call :upsert_env ANTHROPIC_API_KEY "!ANTHROPIC_API_KEY!"
)
if "!ADMIN_API_KEY!"=="" (
    set /p ADMIN_API_KEY="  ADMIN_API_KEY [!L_MISSING_AUTO!]: "
    if "!ADMIN_API_KEY!"=="" for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set ADMIN_API_KEY=%%i
    call :upsert_env ADMIN_API_KEY "!ADMIN_API_KEY!"
)
if "!CLIENT_API_KEY!"=="" (
    set /p CLIENT_API_KEY="  CLIENT_API_KEY [!L_MISSING_AUTO!]: "
    if "!CLIENT_API_KEY!"=="" for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set CLIENT_API_KEY=%%i
    call :upsert_env CLIENT_API_KEY "!CLIENT_API_KEY!"
)
if "!ENCRYPTION_KEY!"=="" (
    set /p ENCRYPTION_KEY="  ENCRYPTION_KEY [!L_MISSING_AUTO!]: "
    if "!ENCRYPTION_KEY!"=="" for /f %%i in ('powershell -NoProfile -c "[Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).Replace('+','-').Replace('/','_')"') do set ENCRYPTION_KEY=%%i
    call :upsert_env ENCRYPTION_KEY "!ENCRYPTION_KEY!"
)
if "!REDIS_CASSIOPEIA_PASSWORD!"=="" (
    set /p REDIS_CASSIOPEIA_PASSWORD="  REDIS_CASSIOPEIA_PASSWORD [!L_MISSING_AUTO!]: "
    if "!REDIS_CASSIOPEIA_PASSWORD!"=="" for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(16)).ToLower()"') do set REDIS_CASSIOPEIA_PASSWORD=%%i
    call :upsert_env REDIS_CASSIOPEIA_PASSWORD "!REDIS_CASSIOPEIA_PASSWORD!"
)
if "!REDIS_COMMUNITY_PASSWORD!"=="" (
    set /p REDIS_COMMUNITY_PASSWORD="  REDIS_COMMUNITY_PASSWORD [!L_MISSING_AUTO!]: "
    if "!REDIS_COMMUNITY_PASSWORD!"=="" for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(16)).ToLower()"') do set REDIS_COMMUNITY_PASSWORD=%%i
    call :upsert_env REDIS_COMMUNITY_PASSWORD "!REDIS_COMMUNITY_PASSWORD!"
)
if "!DISPATCH_HMAC_SECRET!"=="" (
    set /p DISPATCH_HMAC_SECRET="  DISPATCH_HMAC_SECRET [!L_MISSING_AUTO!]: "
    if "!DISPATCH_HMAC_SECRET!"=="" for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set DISPATCH_HMAC_SECRET=%%i
    call :upsert_env DISPATCH_HMAC_SECRET "!DISPATCH_HMAC_SECRET!"
)
if "!SANDBOX_API_KEY!"=="" (
    for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set SANDBOX_API_KEY=%%i
    call :upsert_env SANDBOX_API_KEY "!SANDBOX_API_KEY!"
    echo   SANDBOX_API_KEY: ^(!L_MISSING_AUTO!^)
)

echo.
echo [1/3] !L_ENV_UPDATED!
goto :ask_run

:: ── .env 신규 생성 ────────────────────────────────────────────────────────────
:create_env
echo [1/3] !L_ENV_SETUP!
echo.

set /p LLM_BACKEND="!L_LLM! "
if "!LLM_BACKEND!"=="" set LLM_BACKEND=gemini

set GEMINI_API_KEY=& set ANTHROPIC_API_KEY=& set LOCAL_LLM_BASE_URL=& set LOCAL_LLM_MODEL=& set NLU_LLM_MODEL=gemini-2.5-flash

if "!LLM_BACKEND!"=="gemini" set /p GEMINI_API_KEY="!L_GEMINI! "
if "!LLM_BACKEND!"=="claude" set /p ANTHROPIC_API_KEY="!L_CLAUDE! "
if "!LLM_BACKEND!"=="local" (
  set /p LOCAL_LLM_BASE_URL="!L_LOCAL_URL! "
  if "!LOCAL_LLM_BASE_URL!"=="" set LOCAL_LLM_BASE_URL=http://localhost:11434/v1
  set /p LOCAL_LLM_MODEL="!L_LOCAL_MODEL! "
  if "!LOCAL_LLM_MODEL!"=="" set LOCAL_LLM_MODEL=llama3.2
  set NLU_LLM_MODEL=!LOCAL_LLM_MODEL!
)

set SLACK_BOT_TOKEN=& set SLACK_APP_TOKEN=& set SLACK_CHANNEL=
set /p _slack="!L_USE_SLACK! "
if /i "!_slack!"=="y" (
  set /p SLACK_BOT_TOKEN="!L_SLACK_BOT! "
  set /p SLACK_APP_TOKEN="!L_SLACK_APP! "
  set /p SLACK_CHANNEL="!L_SLACK_CH! "
)

set NOTION_TOKEN=& set NOTION_DATABASE_ID=
set /p _notion="!L_USE_NOTION! "
if /i "!_notion!"=="y" (
  set /p NOTION_TOKEN="!L_NOTION_TOKEN! "
  set /p NOTION_DATABASE_ID="!L_NOTION_DB! "
)

echo.
echo !L_SECRETS!
set /p ADMIN_API_KEY="!L_ADMIN! "
set /p CLIENT_API_KEY="!L_CLIENT! "
set /p DISPATCH_HMAC_SECRET="!L_HMAC! "
set /p ENCRYPTION_KEY="!L_ENC! "
set /p REDIS_CASSIOPEIA_PASSWORD="!L_R_CASS! "
set /p REDIS_COMMUNITY_PASSWORD="!L_R_COMM! "

if "!ADMIN_API_KEY!"==""             for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set ADMIN_API_KEY=%%i
if "!CLIENT_API_KEY!"==""            for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set CLIENT_API_KEY=%%i
if "!DISPATCH_HMAC_SECRET!"==""      for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set DISPATCH_HMAC_SECRET=%%i
if "!ENCRYPTION_KEY!"==""            for /f %%i in ('powershell -NoProfile -c "[Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).Replace('+','-').Replace('/','_')"') do set ENCRYPTION_KEY=%%i
if "!REDIS_CASSIOPEIA_PASSWORD!"=="" for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(16)).ToLower()"') do set REDIS_CASSIOPEIA_PASSWORD=%%i
if "!REDIS_COMMUNITY_PASSWORD!"==""  for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(16)).ToLower()"') do set REDIS_COMMUNITY_PASSWORD=%%i
for /f %%i in ('powershell -NoProfile -c "[System.Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower()"') do set SANDBOX_API_KEY=%%i

(
  echo # Generated by Cassiopeia start.bat
  echo PYTHONPATH=.
  echo LLM_BACKEND=!LLM_BACKEND!
  echo GEMINI_API_KEY=!GEMINI_API_KEY!
  echo ANTHROPIC_API_KEY=!ANTHROPIC_API_KEY!
  echo LOCAL_LLM_BASE_URL=!LOCAL_LLM_BASE_URL!
  echo LOCAL_LLM_MODEL=!LOCAL_LLM_MODEL!
  echo NLU_LLM_MODEL=!NLU_LLM_MODEL!
  echo NLU_LLM_TEMPERATURE=0.2
  echo NLU_CONFIDENCE_THRESHOLD=0.7
  echo SLACK_BOT_TOKEN=!SLACK_BOT_TOKEN!
  echo SLACK_APP_TOKEN=!SLACK_APP_TOKEN!
  echo SLACK_CHANNEL=!SLACK_CHANNEL!
  echo NOTION_TOKEN=!NOTION_TOKEN!
  echo NOTION_DATABASE_ID=!NOTION_DATABASE_ID!
  echo ADMIN_API_KEY=!ADMIN_API_KEY!
  echo CLIENT_API_KEY=!CLIENT_API_KEY!
  echo DISPATCH_HMAC_SECRET=!DISPATCH_HMAC_SECRET!
  echo ENCRYPTION_KEY=!ENCRYPTION_KEY!
  echo REDIS_CASSIOPEIA_PASSWORD=!REDIS_CASSIOPEIA_PASSWORD!
  echo REDIS_COMMUNITY_PASSWORD=!REDIS_COMMUNITY_PASSWORD!
  echo REDIS_URL=redis://cassiopeia:!REDIS_CASSIOPEIA_PASSWORD!@127.0.0.1:6379
  echo USER_TIMEZONE=Asia/Seoul
  echo CORS_ORIGINS=http://localhost:3000,http://localhost:5173
  echo RESPONSE_TIMEOUT_SEC=30.0
  echo CB_THRESHOLD=3
  echo CB_WINDOW_SEC=300
  echo HEARTBEAT_VALID_SEC=30
  echo RATE_LIMIT_PER_MIN=20
  echo RATE_LIMIT_WINDOW=60
  echo SANDBOX_RUNTIME=disabled
  echo SANDBOX_API_KEY=!SANDBOX_API_KEY!
) > .env

echo.
echo [1/3] !L_ENV_DONE!

:: ── 3. 실행 방식 선택 ─────────────────────────────────────────────────────────
:ask_run
echo.
echo [2/3] !L_RUN!
echo !L_RUN1!
echo !L_RUN2!
echo.
set /p RUN_MODE="!L_RUN_SEL! "

:: ── 4. 실행 ──────────────────────────────────────────────────────────────────
echo.
if "!RUN_MODE!"=="1" (
  echo [3/3] !L_VENV!
  if not exist "venv\" python -m venv venv
  call venv\Scripts\activate.bat
  pip install -q --no-cache-dir -r agents\cassiopeia_agent\requirements.txt
  echo [3/3] !L_VENV_OK!
  echo.
  echo !L_PY!
  python -m agents.cassiopeia_agent.main
) else if "!RUN_MODE!"=="2" (
  echo !L_DOCKER!
  docker-compose up
) else (
  echo !L_INVALID!
  exit /b 1
)
goto :eof

:: ── 서브루틴: .env 내 키 업데이트 또는 추가 ──────────────────────────────────
:upsert_env
setlocal
set "_k=%~1"
set "_v=%~2"
powershell -NoProfile -c "
$k = '%_k%'
$v = '%_v%'
$path = (Resolve-Path '.env').Path
$txt = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
$pat = [System.Text.RegularExpressions.Regex]::new('^' + [System.Text.RegularExpressions.Regex]::Escape($k) + '=.*', 'Multiline')
if ($pat.IsMatch($txt)) {
    $txt = $pat.Replace($txt, ($k + '=' + $v), 1)
} else {
    $txt = $txt.TrimEnd([char]10, [char]13) + [char]10 + $k + '=' + $v + [char]10
}
[System.IO.File]::WriteAllText($path, $txt, [System.Text.Encoding]::UTF8)
"
endlocal
goto :eof
