# GUIDE

This project implements a multi-agent system where agents collaborate to perform complex tasks.

## Quick Start

#### Option 1 — Run directly (Python, for development)

```bash
# 1. Clone and navigate to the repository
git clone https://github.com/sonjuhy/Cassiopeia.git
cd Cassiopeia

# 2. Copy and configure environment variables (required before first run)
cp .env.example .env
# Open .env and fill in ADMIN_API_KEY, CLIENT_API_KEY, and other required values

# 3. Install dependencies
pip install -r agents/cassiopeia_agent/requirements.txt

# 4. Run Cassiopeia agent in development mode (local LLM)
# shared_core is resolved from the project root — run from there
python -m agents.cassiopeia_agent.main --llm local
```

---

#### Option 2 — Run via Docker (recommended for production)

```bash
# 1. Clone and navigate to the repository
git clone https://github.com/sonjuhy/Cassiopeia.git
cd Cassiopeia

# 2. Copy and configure environment variables
cp .env.example .env

# 3. Start Redis + Cassiopeia agent
docker-compose up -d redis cassiopeia_agent
```

The agent is available at `http://localhost:49152`.

## Agents

The project consists of several agents, each with a specific role:

*   **Cassiopeia Agent (`agents/cassiopeia_agent/`):** The central cassiopeiator that manages task distribution, planning, and communication between other agents. It acts as the main entry point for user requests.
*   **Research Agent (`agents/research_agent/`):** Responsible for conducting research and gathering information.
*   **File Agent (`agents/file_agent/`):** Handles file operations, such as reading, writing, and managing files.
*   **Communication Agent (`agents/communication_agent/`):** Manages communication with external platforms like Slack, Discord, and Telegram.
*   **Sandbox Agent (`agents/sandbox_agent/`):** Provides a sandboxed environment for executing code safely.
*   **Schedule Agent (`agents/schedule_agent/`):** Manages scheduling and task prioritization.

## Core Libraries

*   **Shared Core (`shared_core/`):** Contains common utilities and libraries used across all agents, including logging, LLM interfaces, messaging, storage, and authentication.

## Running the Agents

### Cassiopeia Agent

The cassiopeia agent can be run as a FastAPI application.

#### Option 1 — Run directly (Python)

> **Note:** Always run commands from the **project root** (`Cassiopeia/`).
> `shared_core` is a root-level package and must be on the Python path.

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

Redis and the Cassiopeia agent start together. Set the required environment variables in `.env` before running.

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

Refer to the specific agent's README or documentation for instructions on how to run it. For example, to run the research agent:

```bash
python agents/research_agent/main.py
```

## Setup Wizard

The setup wizard can be run to help configure the environment:

```bash
python tools/setup_wizard.py
```

## Development Workflow

1.  **Code Structure:** Agents are located in the `agents/` directory, with core libraries in `shared_core/`.
2.  **Dependency Management:** Each agent has its own `requirements.txt`. Run `pip install -r agents/<agent-name>/requirements.txt` within a virtual environment.
3.  **Testing:** Tests are located in the `tests/` subdirectory of each agent. Use `pytest` to run tests. For example, to run tests for the cassiopeia agent:
    ```bash
    pytest agents/cassiopeia_agent/tests/
    ```
4.  **Code Style:** Adhere to standard Python style guides (PEP 8). Linters and formatters are configured in the project.

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

Please refer to `CONTRIBUTING.md` for more details on how to contribute to this project.

## License

This project is licensed under the Apache 2.0 License.

## Notes

*   Ensure Redis is running for message brokering.
*   Environment variables can be used for configuration.
*   This project is designed as a monorepo for easier management and development of multiple agents.

## Troubleshooting

If you encounter issues, check the agent logs, ensure dependencies are installed correctly, and verify that required services (like Redis) are running.

---
## **Previous Modifications**
*   Renamed `agents/cassiopeia_agent` to `agents/cassiopeia_agent`.
*   Updated Dockerfiles and `main.py` within `agents/cassiopeia_agent/` to reflect the new directory name and module paths.
*   Updated logger names and internal references accordingly.
*   Updated the FastAPI app title and descriptions in `agents/cassiopeia_agent/main.py`.
*   Updated `agents/cassiopeia_agent/OVERVIEW.md` to reflect the new agent name and path.
*   Updated example commands in `agents/cassiopeia_agent/Dockerfile` and `agents/cassiopeia_agent/Dockerfile.alpine` to use the new module path.
*   Updated the `state_manager.py` role check for "cassiopeia" to "cassiopeia".
*   Updated `README.md` with the new agent name.

## **Next Steps**
*   Rename the CI/CD workflow file.
*   Update paths within the CI/CD workflow file.

---
<br>

# 가이드 (Korean)

이 프로젝트는 여러 에이전트가 협력하여 복잡한 작업을 수행하는 멀티 에이전트 시스템을 구현합니다.

## 간편 가이드 (Quick Start)

#### 방법 1 — 직접 실행 (Python, 개발 환경)

```bash
# 1. 저장소 클론 및 이동
git clone https://github.com/sonjuhy/Cassiopeia.git
cd Cassiopeia

# 2. 환경 변수 파일 복사 및 설정 (최초 실행 전 필수)
cp .env.example .env
# .env 파일을 열어 ADMIN_API_KEY, CLIENT_API_KEY 등 필수 값을 입력하세요

# 3. 필수 패키지 설치
pip install -r agents/cassiopeia_agent/requirements.txt

# 4. 개발 모드(로컬 LLM)로 카시오페아 에이전트 바로 실행
# shared_core는 프로젝트 루트 기준으로 탐색되므로 루트에서 실행하세요
python -m agents.cassiopeia_agent.main --llm local
```

---

#### 방법 2 — Docker로 실행 (운영 환경 권장)

```bash
# 1. 저장소 클론 및 이동
git clone https://github.com/sonjuhy/Cassiopeia.git
cd Cassiopeia

# 2. 환경 변수 파일 복사 및 설정
cp .env.example .env

# 3. Redis + 카시오페아 에이전트 시작
docker-compose up -d redis cassiopeia_agent
```

에이전트는 `http://localhost:49152`에서 접근할 수 있습니다.

## 에이전트 목록

프로젝트는 각기 특정 역할을 담당하는 여러 에이전트로 구성됩니다:

*   **Cassiopeia Agent (`agents/cassiopeia_agent/`):** 다른 에이전트들 간의 태스크 분배, 계획 수립, 커뮤니케이션을 관리하는 중앙 오케스트레이터입니다. 사용자 요청의 기본 진입점 역할을 합니다.
*   **Research Agent (`agents/research_agent/`):** 리서치 수행 및 정보 수집을 담당합니다.
*   **File Agent (`agents/file_agent/`):** 파일 읽기, 쓰기 및 관리 등 파일 관련 작업을 처리합니다.
*   **Communication Agent (`agents/communication_agent/`):** Slack, Discord, Telegram 등 외부 플랫폼과의 통신을 관리합니다.
*   **Sandbox Agent (`agents/sandbox_agent/`):** 코드를 안전하게 실행할 수 있는 격리된 샌드박스 환경을 제공합니다.
*   **Schedule Agent (`agents/schedule_agent/`):** 일정 관리 및 태스크 우선순위 지정을 담당합니다.

## 공통 라이브러리 (Core Libraries)

*   **Shared Core (`shared_core/`):** 로깅, LLM 인터페이스, 메시징, 저장소, 인증 등 모든 에이전트가 공통으로 사용하는 유틸리티와 라이브러리를 포함합니다.

## 에이전트 실행

### 카시오페아 에이전트 (Cassiopeia Agent)

카시오페아 에이전트는 FastAPI 애플리케이션으로 실행할 수 있습니다.

#### 방법 1 — 직접 실행 (Python)

> **주의:** 반드시 **프로젝트 루트**(`Cassiopeia/`)에서 실행하세요.
> `shared_core`는 루트 레벨 패키지로, Python path에 포함되어야 합니다.

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

개별 에이전트를 실행하는 방법은 각 에이전트의 README나 문서를 참조하세요. 예시 (리서치 에이전트 실행):

```bash
python agents/research_agent/main.py
```

## 설정 마법사 (Setup Wizard)

환경 구성을 돕기 위해 설정 마법사를 실행할 수 있습니다:

```bash
python tools/setup_wizard.py
```

## 개발 워크플로우

1.  **코드 구조:** 에이전트들은 `agents/` 디렉토리에, 공통 라이브러리는 `shared_core/`에 위치합니다.
2.  **의존성 관리:** 각 에이전트는 자체 `requirements.txt`를 가집니다. 가상환경에서 `pip install -r agents/<에이전트명>/requirements.txt`를 실행하세요.
3.  **테스트:** 테스트 코드는 각 에이전트의 `tests/` 하위 디렉토리에 있습니다. `pytest`로 테스트를 실행하세요. 예시 (카시오페아 에이전트 테스트):
    ```bash
    pytest agents/cassiopeia_agent/tests/
    ```
4.  **코드 스타일:** 파이썬 표준 스타일 가이드(PEP 8)를 준수합니다. 프로젝트 내에 Linter와 Formatter가 구성되어 있습니다.

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

프로젝트 기여에 대한 자세한 내용은 `CONTRIBUTING.md`를 참조하세요.

## 라이선스

이 프로젝트는 Apache 2.0 라이선스가 적용됩니다.

## 참고 사항

*   메시지 브로커링을 위해 Redis 서버가 실행 중이어야 합니다.
*   환경 변수를 사용하여 시스템 설정을 관리할 수 있습니다.
*   여러 에이전트를 쉽게 관리하고 개발하기 위해 모노리포(monorepo) 구조로 설계되었습니다.

## 문제 해결 (Troubleshooting)

문제가 발생하면 에이전트 로그를 확인하고, 의존성 라이브러리가 올바르게 설치되었는지, Redis와 같은 필수 서비스가 정상적으로 실행 중인지 확인하세요.

---
## **이전 수정 사항**
*   `agents/cassiopeia_agent` 디렉토리명을 현재의 이름으로 변경했습니다.
*   새로운 디렉토리 이름과 모듈 경로를 반영하여 `agents/cassiopeia_agent/` 내부의 Dockerfile과 `main.py`를 업데이트했습니다.
*   로거 이름 및 내부 참조 경로를 일치되게 업데이트했습니다.
*   `agents/cassiopeia_agent/main.py`의 FastAPI 앱 제목 및 설명을 수정했습니다.
*   에이전트 이름 및 경로 변경 사항을 반영하여 `agents/cassiopeia_agent/OVERVIEW.md`를 수정했습니다.
*   새로운 모듈 경로를 사용하도록 Dockerfile들의 예시 명령어를 업데이트했습니다.
*   `state_manager.py`의 역할 확인 조건("cassiopeia")을 업데이트했습니다.
*   새로운 에이전트 이름으로 `README.md`를 업데이트했습니다.

## **다음 단계**
*   CI/CD 워크플로우 파일 이름 변경.
*   CI/CD 워크플로우 내 경로 업데이트.
