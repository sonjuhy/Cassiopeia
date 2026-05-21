# 🛠️ AIAgentProject 미구현 기능 API 명세서 (Backend Guide)

이 문서는 프론트엔드 최적화 과정에서 제거되었거나 Mock 데이터로만 작동하던 기능들을 실제 백엔드에서 구현하기 위한 API 상세 명세입니다.

---

## 1. 🖥️ System & Control (시스템 제어 및 복구)

### 1.1 시스템 상태 제어
시스템의 물리적/논리적 상태를 변경하는 API입니다.

*   **Endpoint:** `POST /system/control`
*   **Auth:** `X-API-Key` (Admin)
*   **Request Body:**
    ```json
    {
      "action": "terminate" | "restart" | "optimize",
      "target": "all" | "core_engine" | "network_mesh"
    }
    ```
*   **Response (200 OK):**
    ```json
    {
      "status": "success",
      "message": "System restart initiated.",
      "timestamp": "2026-05-14T15:43:00Z"
    }
    ```

### 1.2 시스템 복구 실행 (Repair)
특정 모듈의 장애를 해결하기 위한 복구 프로토콜을 실행합니다.

*   **Endpoint:** `POST /system/recovery/repair`
*   **Auth:** `X-API-Key` (Admin)
*   **Request Body:**
    ```json
    {
      "module_id": "core_engine" | "network_mesh",
      "repair_type": "hotfix" | "full_reinstall"
    }
    ```
*   **Response (200 OK):**
    ```json
    {
      "repair_id": "rep-99201",
      "status": "in_progress",
      "estimated_time": "45s"
    }
    ```

---

## 2. 👤 User & Account (사용자 및 보안)

### 2.1 보안 설정 변경 (비밀번호 및 MFA)
*   **Endpoint:** `PUT /user/security`
*   **Auth:** `X-API-Key` (Client)
*   **Request Body:**
    ```json
    {
      "current_password": "...",
      "new_password": "...",
      "mfa_enabled": true,
      "mfa_type": "totp"
    }
    ```
*   **Response (200 OK):** `{ "status": "updated" }`

### 2.2 결제 및 크레딧 관리
*   **Endpoint:** `POST /user/credits/topup`
*   **Auth:** `X-API-Key` (Client)
*   **Request Body:**
    ```json
    {
      "amount": 50.00,
      "currency": "USD",
      "payment_method_id": "pm_123..."
    }
    ```
*   **Response (200 OK):**
    ```json
    {
      "transaction_id": "TX-9921",
      "new_balance": 15208
    }
    ```

---

## 3. 🌐 Endpoint & Infrastructure (인프라 관리)

### 3.1 방화벽 규칙 관리
*   **Endpoint:** `POST /endpoints/firewall/rules`
*   **Auth:** `X-API-Key` (Admin)
*   **Request Body:**
    ```json
    {
      "rule_name": "Block Unauthenticated Egress",
      "protocol": "TCP",
      "port": 443,
      "action": "allow" | "deny"
    }
    ```
*   **Response (201 Created):** `{ "id": "rule-001", "status": "active" }`

### 3.2 신규 엔드포인트 등록
*   **Endpoint:** `POST /endpoints`
*   **Auth:** `X-API-Key` (Admin)
*   **Request Body:**
    ```json
    {
      "path": "/v1/custom/logic",
      "method": "POST",
      "target_service": "logic-container:8080"
    }
    ```
*   **Response (201 Created):** `{ "endpoint_id": "end-551" }`

---

## 4. 🛒 Marketplace & Registration (에이전트 유통)

### 4.1 에이전트 상세 메타데이터 조회
마켓플레이스 사이드바에 표시될 상세 정보를 제공합니다.

*   **Endpoint:** `GET /marketplace/agents/{id}/details`
*   **Auth:** `X-API-Key` (None/Client)
*   **Response (200 OK):**
    ```json
    {
      "id": "mkt-1",
      "permissions_required": ["network_egress", "filesystem_read"],
      "release_history": [
        { "version": "v2.4.1", "date": "2026-04-01", "notes": "Bug fixes" }
      ],
      "confidence_score": 98.2,
      "documentation_url": "https://docs.ai-agent.com/mkt-1"
    }
    ```

### 4.2 에이전트 등록 (고급 설정 포함)
*   **Endpoint:** `POST /agents/register/advanced`
*   **Auth:** `X-API-Key` (Client)
*   **Request Body (Multipart Form-Data):**
    - `icon`: File (Image)
    - `metadata`: JSON String
      ```json
      {
        "name": "...",
        "description": "...",
        "economics": { "fee": 5.0, "billing_cycle": "monthly" },
        "permissions": { "gpu": true, "network": true }
      }
      ```
*   **Response (201 Created):** `{ "agent_id": "new-agent-001" }`

---

## 💡 백엔드 구현 가이드라인

1.  **Auth Strategy:** 모든 상태 변경 API(`POST`, `PUT`, `DELETE`)는 `X-API-Key` 헤더를 필수적으로 검증해야 합니다.
2.  **Idempotency:** 작업 제출 및 결제 관련 API는 `X-Idempotency-Key`를 지원하여 중복 처리를 방지할 것을 권장합니다.
3.  **Validation:** Input Body에 대한 스키마 검증(Zod, Pydantic 등)을 철저히 수행하고, 실패 시 상세한 에러 메시지(400 Bad Request)를 반환해야 합니다.
