🚀 프론트엔드 연동을 위한 백엔드 API 구현 요청서
안녕하세요! 프론트엔드(React 기반) 화면 구성을 위해 필요한 API 목록을 정리했습니다. AIAgentProject는 AI 에이전트(Orchestra)와 통신하여 사용자의 태스크를 처리하고 시스템을 모니터링하는 대시보드입니다. 아래 기능들을 참고하여 API 명세 및 개발을 진행해 주시면 감사하겠습니다. 구현 중 궁금한 점이나 데이터 구조 변경이 필요하다면 언제든 논의해 주세요!

1. 태스크 지시 및 AI 처리 (대시보드 메인)
1.1 태스크 제출 API
기능 명: 태스크 제출 및 작업 지시
기능 목적: 사용자가 대시보드에서 자연어로 입력한 명령을 AI 에이전트에게 전달합니다.
요청 방식: POST /tasks
필요 데이터 (Request Body):
content (String): 작업 명령어 (예: "오늘 일기 저장해줘")
user_id (String): 요청자 ID
channel_id (String): 요청 채널 (기본값: "web_ui")
기대 응답 형식: 작업이 접수되었다는 상태와 고유 task_id 반환 (예: { "status": "accepted", "task_id": "task-123" })
1.2 태스크 상태 확인 API (Polling 용도)
기능 명: 태스크 진행 상태 추적
기능 목적: 프론트엔드에서 주기적(Polling)으로 호출하여 작업이 대기중/진행중/완료/에러 인지 파악하고, 화면에 진행 상황(로그)을 실시간으로 업데이트합니다.
요청 방식: GET /tasks/{taskId}?include_logs=true
필요 데이터 (Path/Query): taskId (경로 변수), include_logs (중간 로그 포함 여부 boolean)
기대 응답 형식:
status: 현재 상태 ("SUBMITTED", "PROCESSING", "COMPLETED", "ERROR")
recent_logs: 에이전트가 처리 중인 중간 과정 메시지 배열
result: 완료 시 최종 요약 및 결과 내용
1.3 세션 대화 이력 API
기능 명: 세션별 상세 대화 이력 조회
기능 목적: 완료된 작업에 대해 AI가 어떤 사고 과정을 거쳐 결과를 도출했는지 사용자에게 채팅 형식으로 보여줍니다.
요청 방식: GET /sessions/{sessionId}/history
필요 데이터 (Path): sessionId (태스크 완료 결과에 포함된 세션 ID)
기대 응답 형식: [ { "role": "user" | "assistant", "content": "내용", "timestamp": "시간" } ] 형태의 배열
1.4 프롬프트 추천 API
기능 명: 초기 프롬프트(명령어) 추천
기능 목적: 사용자가 처음 빈 입력창을 보았을 때 쉽게 클릭해서 명령할 수 있도록 추천 텍스트를 제공합니다.
요청 방식: GET /prompts/suggestions
필요 데이터: (요청 파라미터 없음)
기대 응답 형식: { "suggestions": ["추천어 1", "추천어 2"] }
2. 모니터링 및 상태 확인 (시스템 오버뷰)
2.1 에이전트 목록 및 연결 상태 API
기능 명: 전체 에이전트 목록 및 연결 상태 조회
기능 목적: 현재 접속 가능한 AI 에이전트(예: 정보 검색 비서 등)가 정상적으로 켜져 있는지 확인합니다.
요청 방식: GET /agents
필요 데이터: (요청 파라미터 없음)
기대 응답 형식:
available: 접속 가능한 에이전트 ID 배열
all: 각 에이전트의 구체적인 상태 (능력치, 에러 여부, 하트비트 정상 여부 등)
2.2 시스템 리소스 사용량 API
기능 명: 전체 시스템 사용량 확인
기능 목적: 대시보드의 차트 및 통계 위젯에 표시할 CPU, RAM, API 호출 횟수를 가져옵니다.
요청 방식: GET /system/usage
필요 데이터: (요청 파라미터 없음)
기대 응답 형식: { "cpuUsage": { "value": "42.8%", "percentage": 42.8 }, "ramAllocated": { ... }, ... }
2.3 에이전트 대기열(Queue) API
기능 명: 에이전트별 대기열(Queue) 상태 조회
기능 목적: 현재 작업이 밀려 있는(병목) 에이전트를 파악합니다.
요청 방식: GET /queue/status
필요 데이터: (요청 파라미터 없음)
기대 응답 형식: { "search_agent": 5, "coding_agent": 0 } 처럼 에이전트 키 값과 대기 개수 반환
3. 에이전트 상세 관리 (에이전트 관리 페이지)
3.1 에이전트 상세 정보 API
기능 명: 개별 에이전트 상세 스펙 및 성능 조회
기능 목적: 특정 에이전트의 버전, 로드율(Load), 성능 지표(지연 시간 등)를 표출합니다.
요청 방식: GET /agents/detail (전체 상세 리스트) 또는 GET /agents/{agentId}
기대 응답 형식: 이름, 버전, 상태, metrics(성능 수치) 정보를 포함한 객체 배열
3.2 에이전트 권한 설정 API
기능 명: 개별 에이전트 권한 제어
기능 목적: 보안을 위해 에이전트의 파일 접근 권한, 네트워크 접속 권한 등을 화면에서 껐다 켤 수 있게 합니다.
요청 방식: PUT /agents/{agentId}/permissions
필요 데이터 (Request Body): { "network": true, "fileSystem": false, "gpu": true }
기대 응답 형식: 200 OK 처리 완료 응답
4. 마켓플레이스 연동 (마켓플레이스 페이지)
4.1 스토어 에이전트 목록 API
기능 명: 신규 템플릿(외부 에이전트) 목록 조회
기능 목적: 사용자가 새로 설치할 수 있는 AI 에이전트들의 정보(이름, 카테고리, 비용 등)를 표시합니다.
요청 방식: GET /marketplace/agents
기대 응답 형식: 카테고리, 아이콘, 설치 상태(installStatus), 과금 방식(pricingType)이 포함된 배열
4.2 에이전트 설치 API
기능 명: 마켓플레이스 에이전트 설치 승인
기능 목적: 화면에서 "설치하기" 버튼을 눌렀을 때 시스템에 해당 에이전트를 배포/설치합니다.
요청 방식: POST /marketplace/install
필요 데이터 (Request Body): 설치할 템플릿/에이전트의 고유 ID 및 설정값
기대 응답 형식: { "status": "installed", "agent_name": "..." }
5. 계정 및 설정 관리 (설정 페이지)
5.1 사용자 프로필 API
기능 명: 로그인된 사용자의 정보 및 과금 조회
기능 목적: 현재 잔여 크레딧(비용)과 사용자 권한 등급을 표시하고, 과거의 토큰/비용 소모 내역을 불러옵니다.
요청 방식: GET /user/profile, GET /user/transactions
기대 응답 형식: 프로필 정보(nickname, computeCredits 등) 및 과금 내역(activityType, cost, status 등)
5.2 샌드박스 API 키 생성/조회 API
기능 명: 외부 연동용 관리자 인증 키(API Key) 관리
기능 목적: 외부 플랫폼에서 우리 시스템에 접근하기 위해 발급받는 인증 Key를 생성/폐기합니다.
요청 방식: POST /admin/sandbox/keys (생성), GET /admin/sandbox/keys (조회), DELETE /admin/sandbox/keys/{prefix} (삭제)
필요 데이터 (POST Body): { "label": "개발용 연동 키" }
기대 응답 형식: 생성 시 암호화된 key 텍스트 원본 반환
💡 프론트엔드 참고 사항 (개발 시 고려해 주시면 좋은 점):

태스크 처리(1.1 ~ 1.2)는 완료까지 시간이 걸리는 비동기 작업이므로, status 값이 PROCESSING에서 COMPLETED 혹은 ERROR로 확실히 떨어질 수 있도록 응답을 구성해 주시면 좋습니다.
에러 발생 시 프론트엔드에서 알림(Toast/Alert)으로 표시할 수 있도록 직관적인 에러 메시지를 포함해 주시면 도움이 됩니다!