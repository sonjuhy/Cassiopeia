# Contributing to Cassiopeia

Cassiopeia 프로젝트에 기여해 주셔서 감사합니다.

---

## 목차

- [시작하기 전에](#시작하기-전에)
- [개발 환경 세팅](#개발-환경-세팅)
- [브랜치 전략](#브랜치-전략)
- [커밋 메시지 컨벤션](#커밋-메시지-컨벤션)
- [Pull Request 절차](#pull-request-절차)
- [코드 스타일](#코드-스타일)
- [테스트 작성 규칙](#테스트-작성-규칙)
- [새 에이전트 추가](#새-에이전트-추가)

---

## 시작하기 전에

- 버그를 발견하거나 기능을 제안하려면 **Issue를 먼저 생성**하세요.
- 큰 변경사항(새 에이전트, 아키텍처 변경 등)은 Issue에서 논의 후 진행하세요.
- `.env` 파일이나 API 키, 비밀값은 절대 커밋하지 마세요.

---

## 개발 환경 세팅

```bash
# 1. 저장소 클론
git clone https://github.com/sonjuhy/Cassiopeia.git
cd Cassiopeia

# 2. 환경 변수 설정
cp .env.example .env
# .env 파일을 열어 필요한 값을 채워 넣으세요

# 3. 가상환경 생성 및 의존성 설치
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r agents/<에이전트명>/requirements.txt

# 4. Redis 실행 (메시지 브로커)
docker-compose up -d redis

# 5. 설정 마법사 실행 (선택)
python tools/setup_wizard.py
```

---

## 브랜치 전략

| 브랜치 | 용도 |
|--------|------|
| `main` | 프로덕션 배포 브랜치 |
| `feature/<이름>` | 새 기능 개발 |
| `fix/<이름>` | 버그 수정 |
| `docs/<이름>` | 문서 작업 |
| `refactor/<이름>` | 코드 리팩토링 |

```bash
# 작업 시작 예시
git checkout main
git pull origin main
git checkout -b feature/my-new-feature
```

---

## 커밋 메시지 컨벤션

이 프로젝트는 `.gitmessage.txt`에 정의된 Conventional Commits 규칙을 따릅니다.

```
<타입>(<스코프>): <제목>

<본문> (선택)

<꼬리말> (선택)
```

### 타입

| 타입 | 이모지 | 설명 |
|------|--------|------|
| `feat` | ✨ | 새로운 기능 추가 |
| `fix` | 🐛 | 버그 수정 |
| `docs` | 📝 | 문서 수정 |
| `refactor` | ♻️ | 기능 변경 없는 코드 개선 |
| `style` | 💄 | 포매팅, 세미콜론 등 코드 변경 없음 |
| `test` | 🧪 | 테스트 코드 추가/수정 |
| `chore` | 🔧 | 빌드, 패키지 매니저 수정 |
| `perf` | 🚀 | 성능 개선 |
| `revert` | ⏪ | 이전 커밋으로 되돌림 |
| `build` | 🔨 | 빌드 관련 파일 수정 |
| `ci` | 👷 | CI 설정 수정 |

### 예시

```
feat(research_agent): 웹 검색 캐싱 기능 추가

검색 결과를 Redis에 TTL 10분으로 캐싱하여 중복 요청 최소화

Resolves: #42
```

- 제목은 **50자 이내**, 명령문/현재형으로 작성
- 본문은 **무엇을, 왜** 변경했는지 설명 (어떻게 X)
- 한 커밋에는 **하나의 논리적 변경**만 담기

---

## Pull Request 절차

1. `main` 브랜치를 최신 상태로 유지한 뒤 기능 브랜치에서 작업
2. 변경 사항에 대한 테스트 작성 및 통과 확인
3. PR 생성 시 아래 항목을 포함

```markdown
## 변경 내용
- ...

## 관련 Issue
Closes #이슈번호

## 테스트 확인
- [ ] 단위 테스트 통과
- [ ] 기존 테스트 회귀 없음
```

4. 최소 1명의 리뷰어 승인 후 머지
5. PR 브랜치는 머지 후 삭제

---

## 코드 스타일

- **PEP 8** 준수
- 타입 힌트 사용 (`def fn(x: int) -> str:`)
- 함수/클래스 단위 책임 분리 원칙 유지
- 새 에이전트는 `shared_core`의 공통 인터페이스(`interfaces.py`)를 상속

```bash
# 포매터 실행 (black)
black agents/ shared_core/

# 린터 실행 (flake8)
flake8 agents/ shared_core/
```

---

## 테스트 작성 규칙

- 테스트 파일 위치: `agents/<에이전트명>/tests/` 또는 `shared_core/tests/`
- 파일명: `test_*.py`
- 클래스명: `Test*`
- 함수명: `test_*`

```bash
# 전체 테스트 실행
pytest

# 특정 에이전트 테스트
pytest agents/cassiopeia_agent/tests/

# 특정 모듈 테스트
pytest shared_core/tests/test_cassiopeia_broker.py
```

- 외부 서비스(Redis, LLM API 등)는 **mock** 처리
- 비동기 테스트는 `pytest-asyncio` 사용 (`asyncio_mode = auto` 적용됨)
- 환경 변수가 필요한 테스트는 `pytest.ini`의 `env` 섹션 활용

---

## 새 에이전트 추가

Cassiopeia에 새 에이전트를 추가하려면 **CassiopeiaSDK**를 사용하세요.
SDK를 이용해 빌드한 에이전트는 Docker로 실행하는 것만으로 Cassiopeia에 **자동 등록**됩니다.

> SDK 레포지토리: [https://github.com/sonjuhy/CassiopeiaSDK](https://github.com/sonjuhy/CassiopeiaSDK)

### Python (pip)

```bash
pip install cassiopeia-sdk
```

### Node.js (npm)

```bash
npm install cassiopeia-sdk
```

### 에이전트 실행 및 자동 등록

빌드한 에이전트를 Docker로 실행하면 Cassiopeia에 자동으로 등록됩니다.

```bash
docker build -t my-agent .
docker run --network cassiopeia-net my-agent
```

자세한 사용법은 [CassiopeiaSDK 레포지토리](https://github.com/sonjuhy/CassiopeiaSDK)를 참조하세요.

---

## 라이선스

기여하신 코드는 이 프로젝트의 [Apache 2.0 License](LICENSE)에 따라 배포됩니다.

---

*질문이 있으시면 Issue를 통해 문의해 주세요.*
