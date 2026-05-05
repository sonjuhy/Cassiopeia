# Cassiopeia

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/img/cassiopeia_white.png">
    <source media="(prefers-color-scheme: light)" srcset="assets/img/cassiopeia_black.png">
    <img alt="Cassiopeia Logo" src="assets/img/cassiopeia_black.png" width="300">
  </picture>
</p>

This repository contains multiple AI agents designed to work together to perform complex tasks.

## Directory Structure

The project is structured as a monorepo, with different agents and shared libraries organized into distinct directories.

```
├───.env.example
├───.gitignore
├───.gitmessage.txt
├───docker-compose.yml
├───front_end_require.md
├───GUIDE.md
├───LICENSE
├───NOTICE
├───pytest.ini
├───README.md
├───.git\...
├───.github
│   └───workflows
│       ├───deploy_planning_agent.yml
│       └───deploy_slack_agent.yml
├───agents
│   ├───__init__.py
│   ├───archive_agent
│   │   ├───__init__.py
│   │   ├───Dockerfile
│   │   ├───Dockerfile.alpine
│   │   ├───fastapi_app.py
│   │   ├───main.py
│   │   ├───models.py
│   │   ├───protocols.py
│   │   ├───redis_listener.py
│   │   ├───requirements.txt
│   │   ├───test_agent.py
│   │   ├───test_unified_agent.py
│   │   ├───unified_agent.py
│   │   ├───notion
│   │   ├───obsidian
│   │   └───tests
│   ├───communication_agent
│   │   ├───__init__.py
│   │   ├───Dockerfile.alpine
│   │   ├───Dockerfile.listener
│   │   ├───listener_main.py
│   │   ├───main.py
│   │   ├───models.py
│   │   ├───protocols.py
│   │   ├───requirements.txt
│   │   ├───discord
│   │   ├───slack
│   │   ├───telegram
│   │   └───tests
│   ├───file_agent
│   │   ├───__init__.py
│   │   ├───agent.py
│   │   ├───config.py
│   │   ├───interfaces.py
│   │   ├───main.py
│   │   ├───requirements.txt
│   │   ├───validator.py
│   │   └───tests
│   ├───cassiopeia_agent\  # Renamed to cassiopeia_agent
│   │   ├───__init__.py
│   │   ├───admin_router.py
│   │   ├───agent_builder_handler.py
│   │   ├───agent.py
│   │   ├───app_context.py
│   │   ├───auth.py
│   │   ├───Dockerfile
│   │   ├───error_messages.py
│   │   ├───health_monitor.py
│   │   ├───intent_analyzer.py
│   │   ├───interfaces.py
│   │   ├───main.py
│   │   ├───manager.py
│   │   ├───marketplace_handler.py
│   │   ├───models.py
│   │   ├───nlu_engine.py
│   │   ├───NO_CODE_GUIDE.md
│   │   ├───OVERVIEW.md
│   │   ├───protocols.py
│   │   ├───rate_limiter.py
│   │   ├───registry.py
│   │   ├───requirements.txt
│   │   ├───sandbox_tool.py
│   │   ├───scheduler.py
│   │   ├───state_manager.py
│   │   └───tests
│   ├───research_agent
│   │   ├───__init__.py
│   │   ├───agent.py
│   │   ├───config.py
│   │   ├───interfaces.py
│   │   ├───main.py
│   │   ├───pipeline.py
│   │   ├───providers.py
│   │   ├───requirements.txt
│   │   └───tests
│   ├───sandbox_agent
│   │   ├───__init__.py
│   │   ├───Dockerfile
│   │   ├───main.py
│   │   ├───requirements.txt
│   │   ├───sandbox
│   │   └───tests
│   └───schedule_agent
│       ├───__init__.py
│       ├───agent.py
│       ├───config.py
│       ├───interfaces.py
│       ├───main.py
│       ├───providers.py
│       ├───requirements.txt
│       └───tests
├───aseets
│   └───img
│       ├───cassiopeia_black.png
│       └───cassiopeia_white.png
├───redis
│   ├───acl.conf
│   ├───acl.conf.tpl
│   └───entrypoint.sh
├───shared_core
│   ├───__init__.py
│   ├───agent_logger.py
│   ├───dispatch_auth.py
│   ├───calendar
│   │   ├───interfaces.py
│   ├───llm
│   │   ├───__init__.py
│   │   ├───factory.py
│   │   ├───gemma_inference.py
│   │   ├───interfaces.py
│   │   ├───llm_config.py
│   │   ├───ollama_manager.py
│   │   ├───providers
│   │   └───tests
│   ├───messaging
│   │   ├───__init__.py
│   │   ├───broker.py
│   │   ├───schema.py
│   ├───sandbox
│   │   ├───__init__.py
│   │   ├───client.py
│   │   ├───mixin.py
│   │   ├───models.py
│   ├───search
│   │   ├───interfaces.py
│   ├───storage
│   │   ├───__init__.py
│   │   ├───interfaces.py
│   │   ├───sqlite_manager.py
│   └───tests
│       ├───test_cassiopeia_broker.py
│       ├───test_dispatch_auth.py
│       ├───test_logging_security.py
├───tools
│   ├───__init__.py
│   ├───setup_wizard.py
│   ├───test_setup_wizard.py
│   └───agent_builder
│       ├───__init__.py
│       ├───__main__.py
│       ├───builder.py
│       ├───cli.py
│       ├───permissions.py
│       ├───templates.py
│       ├───validator.py
│       └───...

## Quick Start

```bash
git clone https://github.com/sonjuhy/Cassiopeia.git
cd Cassiopeia

# Linux / Mac
./start.sh

# Windows
start.bat
```

The script will:
1. Create and activate a virtual environment
2. Install dependencies
3. Run the setup wizard to generate `.env` (first time only)
4. Ask whether to run via **Python** (development) or **Docker** (production)

The agent is available at `http://localhost:49152`.

## Getting Started

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/sonjuhy/Cassiopeia.git
    cd Cassiopeia
    ```
2.  **Run the start script:** `./start.sh` (Linux/Mac) or `start.bat` (Windows)

## Running the Agents

### Cassiopeia Agent (`agents/cassiopeia_agent/`)

The cassiopeia agent serves as the core of the system.

#### Option 1 — Run directly (Python)

> **Note:** Always run commands from the **project root** (`Cassiopeia/`).
> `shared_core` is a root-level package and must be on the Python path.

> **Prerequisite:** Redis must be running before starting the agent.
> ```bash
> docker-compose up -d redis   # or: redis-server
> ```

**Development Mode (local LLM):**
```bash
python -m agents.cassiopeia_agent.main --llm local
```

**Production Mode (external LLMs):**
```bash
LLM_BACKEND=claude python -m agents.cassiopeia_agent.main
LLM_BACKEND=gemini python -m agents.cassiopeia_agent.main
```

---

#### Option 2 — Run via Docker (recommended for production)

Set the required environment variables in `.env` before running. Redis and the agent start together.

```bash
# Start Redis + Cassiopeia agent
docker-compose up -d redis cassiopeia_agent

# With local LLM (Ollama on Linux GPU)
docker-compose --profile local-llm up -d

# View logs
docker-compose logs -f cassiopeia_agent
```

The agent is available at `http://localhost:49152` by default (configurable via `CASSIOPEIA_PORT` in `.env`).

| `LLM_BACKEND` value | Description |
|---|---|
| `gemini` (default) | Google Gemini API |
| `claude` | Anthropic Claude API |
| `local` | Local Ollama instance |

### Other Agents

Each agent can be run independently. Consult their respective documentation for specific instructions. For example, to run the research agent:

```bash
python agents/research_agent/main.py
```

## Setup Wizard

The `tools/setup_wizard.py` script can assist in setting up the project environment.
```bash
python tools/setup_wizard.py
```

## Development Workflow

*   **Code Structure:** Agents are in `agents/`, shared libraries in `shared_core/`.
*   **Dependency Management:** Each agent has its own `requirements.txt`. Run `pip install -r agents/<agent-name>/requirements.txt` within a virtual environment.
*   **Testing:** Tests are in `tests/` subdirectories. Use `pytest`. Example: `pytest agents/cassiopeia_agent/tests/`
*   **Code Style:** Adhere to PEP 8. Linters and formatters are configured.

## Adding a New Agent

To add a new agent to Cassiopeia, use the **CassiopeiaSDK**.
Agents built with the SDK are **automatically registered** to Cassiopeia simply by running them via Docker.

> SDK Repository: [https://github.com/sonjuhy/CassiopeiaSDK](https://github.com/sonjuhy/CassiopeiaSDK)

**Python (pip):**
```bash
pip install cassiopeia-sdk
```

**Node.js (npm):**
```bash
npm install cassiopeia-sdk
```

**Run and auto-register via Docker:**
```bash
docker build -t my-agent .
docker run --network cassiopeia-net my-agent
```

For full usage details, refer to the [CassiopeiaSDK repository](https://github.com/sonjuhy/CassiopeiaSDK).

## Contributing

Please refer to `CONTRIBUTING.md` for contribution guidelines.

## License

This project is licensed under the Apache 2.0 License.

## Notes

*   Redis is required for message brokering.
*   Environment variables are used for configuration.

---
## **Previous Modifications**
*   Renamed `agents/cassiopeia_agent` to `agents/cassiopeia_agent`.
*   Updated Dockerfiles and `main.py` within `agents/cassiopeia_agent/` to reflect the new directory name and module paths.
*   Updated logger names and internal references accordingly.
*   Updated the FastAPI app title and descriptions in `agents/cassiopeia_agent/main.py`.
*   Updated `agents/cassiopeia_agent/OVERVIEW.md` to reflect the new agent name and path.
*   Updated example commands in `agents/cassiopeia_agent/Dockerfile` and `agents/cassiopeia_agent/Dockerfile.alpine` to use the new module path.
*   Updated the `state_manager.py` role check for "cassiopeia" to "cassiopeia".
*   Updated `GUIDE.md` with new agent name, paths, and updated commands.

## **Next Steps**
*   Rename the CI/CD workflow file.
*   Update paths within the CI/CD workflow file.

---
<br>

# Cassiopeia (한국어)

이 레포지토리는 복잡한 작업을 수행하기 위해 함께 작동하도록 설계된 여러 AI 에이전트를 포함하고 있습니다.

## 디렉토리 구조

이 프로젝트는 모노리포 형태로 구성되어 있으며, 각각의 에이전트와 공통 라이브러리가 구분된 디렉토리에 정리되어 있습니다. (트리 구조는 위의 영문 섹션을 참조하세요.)

## 간편 가이드 (Quick Start)

```bash
git clone https://github.com/sonjuhy/Cassiopeia.git
cd Cassiopeia

# Linux / Mac
./start.sh

# Windows
start.bat
```

스크립트가 다음을 자동으로 처리합니다:
1. 가상환경 생성 및 활성화
2. 의존성 설치
3. 설정 마법사로 `.env` 생성 (최초 1회)
4. **Python** (개발) 또는 **Docker** (운영) 실행 방식 선택

에이전트는 `http://localhost:49152`에서 접근할 수 있습니다.

## 시작하기

1.  **저장소 클론:**
    ```bash
    git clone https://github.com/sonjuhy/Cassiopeia.git
    cd Cassiopeia
    ```
2.  **시작 스크립트 실행:** `./start.sh` (Linux/Mac) 또는 `start.bat` (Windows)

## 에이전트 실행

### 카시오페아 에이전트 (`agents/cassiopeia_agent/`)

카시오페아(Cassiopeia) 에이전트는 시스템의 핵심 역할을 담당합니다.

#### 방법 1 — 직접 실행 (Python)

> **주의:** 반드시 **프로젝트 루트**(`Cassiopeia/`)에서 실행하세요.
> `shared_core`는 루트 레벨 패키지로, Python path에 포함되어야 합니다.

> **사전 조건:** 에이전트 시작 전 Redis가 실행 중이어야 합니다.
> ```bash
> docker-compose up -d redis   # 또는: redis-server
> ```

**개발 모드 (로컬 LLM):**
```bash
python -m agents.cassiopeia_agent.main --llm local
```

**운영 모드 (외부 LLMs):**
```bash
LLM_BACKEND=claude python -m agents.cassiopeia_agent.main
LLM_BACKEND=gemini python -m agents.cassiopeia_agent.main
```

---

#### 방법 2 — Docker로 실행 (운영 환경 권장)

실행 전 `.env` 파일에 필수 환경 변수를 설정하세요. Redis와 에이전트가 함께 시작됩니다.

```bash
# Redis + 카시오페아 에이전트 함께 시작
docker-compose up -d redis cassiopeia_agent

# 로컬 LLM 사용 시 (Linux GPU 환경, Ollama 포함)
docker-compose --profile local-llm up -d

# 로그 확인
docker-compose logs -f cassiopeia_agent
```

기본 포트는 `http://localhost:49152`이며, `.env`의 `CASSIOPEIA_PORT`로 변경할 수 있습니다.

| `LLM_BACKEND` 값 | 설명 |
|---|---|
| `gemini` (기본값) | Google Gemini API |
| `claude` | Anthropic Claude API |
| `local` | 로컬 Ollama 인스턴스 |

### 다른 에이전트들

각 에이전트는 독립적으로 실행될 수 있습니다. 구체적인 실행 방법은 각 에이전트의 문서를 참조하세요. 예를 들어, 리서치 에이전트를 실행하려면:

```bash
python agents/research_agent/main.py
```

## 설정 마법사 (Setup Wizard)

프로젝트 환경 설정을 돕기 위해 `tools/setup_wizard.py` 스크립트를 사용할 수 있습니다.
```bash
python tools/setup_wizard.py
```

## 개발 워크플로우

*   **코드 구조:** 에이전트들은 `agents/`에, 공통 라이브러리는 `shared_core/`에 위치합니다.
*   **의존성 관리:** 각 에이전트는 자체 `requirements.txt`를 가집니다. 가상환경에서 `pip install -r agents/<에이전트명>/requirements.txt`를 실행하세요.
*   **테스트:** 테스트는 각 `tests/` 하위 디렉토리에 있습니다. `pytest`를 사용하세요. 예: `pytest agents/cassiopeia_agent/tests/`
*   **코드 스타일:** PEP 8을 준수합니다. Linter와 Formatter가 구성되어 있습니다.

## 새 에이전트 추가

Cassiopeia에 새 에이전트를 추가하려면 **CassiopeiaSDK**를 사용하세요.
SDK로 빌드한 에이전트는 Docker로 실행하는 것만으로 Cassiopeia에 **자동 등록**됩니다.

> SDK 레포지토리: [https://github.com/sonjuhy/CassiopeiaSDK](https://github.com/sonjuhy/CassiopeiaSDK)

**Python (pip):**
```bash
pip install cassiopeia-sdk
```

**Node.js (npm):**
```bash
npm install cassiopeia-sdk
```

**Docker로 실행 및 자동 등록:**
```bash
docker build -t my-agent .
docker run --network cassiopeia-net my-agent
```

자세한 사용법은 [CassiopeiaSDK 레포지토리](https://github.com/sonjuhy/CassiopeiaSDK)를 참조하세요.

## 기여하기

기여 가이드라인은 `CONTRIBUTING.md`를 참조하세요.

## 라이선스

이 프로젝트는 Apache 2.0 License 조건에 따라 배포됩니다.

## 참고 사항

*   메시지 브로커링을 위해 Redis가 필요합니다.
*   환경 변수를 사용하여 시스템을 구성합니다.

---
## **이전 수정 사항**
*   `agents/cassiopeia_agent` 디렉토리명을 현재의 이름으로 변경했습니다.
*   새로운 디렉토리 이름과 모듈 경로를 반영하여 `agents/cassiopeia_agent/` 내부의 Dockerfile과 `main.py`를 업데이트했습니다.
*   로거 이름 및 내부 참조 경로를 일치되게 업데이트했습니다.
*   `agents/cassiopeia_agent/main.py`의 FastAPI 앱 제목 및 설명을 수정했습니다.
*   에이전트 이름 및 경로 변경 사항을 반영하여 `agents/cassiopeia_agent/OVERVIEW.md`를 수정했습니다.
*   새로운 모듈 경로를 사용하도록 Dockerfile들의 예시 명령어를 업데이트했습니다.
*   `state_manager.py`의 역할 확인 조건("cassiopeia")을 업데이트했습니다.
*   새로운 에이전트 이름과 경로, 업데이트된 명령어를 `GUIDE.md`에 반영했습니다.

## **다음 단계**
*   CI/CD 워크플로우 파일 이름 변경.
*   CI/CD 워크플로우 내 경로 업데이트.
