"""
세션 상태 및 대화 이력 관리 (State Manager)
- SQLite: 대화 이력, 사용자 프로필, 에이전트 로그 영구 저장
- Redis: 실시간 태스크 상태 및 빠른 메시지 캐싱
"""

from __future__ import annotations

import aiosqlite
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from cryptography.fernet import Fernet

logger = logging.getLogger("cassiopeia_agent.state_manager")

_SESSION_TTL = 7200
_TASK_TTL = 86400
_MAX_MESSAGES = 20          # Redis 캐시 유지 수
_HISTORY_LIMIT = 10         # LLM 컨텍스트 포함 DB 이력 수
_SUMMARIZE_THRESHOLD = 20

# UPDATE users 시 허용되는 컬럼명 화이트리스트 — SQL 인젝션 방어
_ALLOWED_USER_COLUMNS: frozenset[str] = frozenset({"name", "style_pref", "llm_keys"})


class StateManager:
    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        if redis_client is not None:
            self._redis = redis_client
        else:
            redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
            self._redis = aioredis.from_url(
                redis_url, decode_responses=True, socket_timeout=60.0
            )

        self._db_path = os.environ.get("DATABASE_PATH", "sqlite_db.db")
        self._db_conn: aiosqlite.Connection | None = None
        
        # 암호화 키 초기화
        enc_key = os.environ.get("ENCRYPTION_KEY")
        if not enc_key:
            raise RuntimeError("ENCRYPTION_KEY 환경변수가 설정되지 않았습니다. 서비스 시작 전에 반드시 설정하세요.")
        try:
            self._cipher_suite = Fernet(enc_key.encode('utf-8'))
        except ValueError as exc:
            raise RuntimeError(f"ENCRYPTION_KEY가 올바르지 않습니다: {exc}") from exc

    async def ensure_db(self) -> aiosqlite.Connection:
        """SQLite 연결 보장 및 테이블 초기화"""
        if self._db_conn is None:
            self._db_conn = await aiosqlite.connect(self._db_path)
            self._db_conn.row_factory = aiosqlite.Row
            await self._db_conn.execute("PRAGMA journal_mode=WAL")
            await self._create_tables()
        return self._db_conn

    async def _create_tables(self) -> None:
        queries = [
            "CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, name TEXT, style_pref TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, session_id TEXT, thread_id TEXT, role TEXT, content TEXT, provider TEXT, tokens_in INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, user_id TEXT, channel_id TEXT, last_summary TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_name TEXT, task_id TEXT, session_id TEXT, action TEXT, message TEXT, payload TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS task_history (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT UNIQUE, user_id TEXT, content TEXT, status TEXT DEFAULT 'PENDING', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        ]
        db = await self.ensure_db()
        for query in queries:
            await db.execute(query)

        # 기존 테이블 마이그레이션 (llm_keys 컬럼 추가)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN llm_keys TEXT")
        except Exception:
            pass # 이미 존재하는 경우 무시

        await db.commit()

    def _encrypt(self, data: str) -> str:
        """문자열을 암호화하여 반환합니다."""
        return self._cipher_suite.encrypt(data.encode('utf-8')).decode('utf-8')

    def _decrypt(self, data: str) -> str:
        """암호화된 문자열을 복호화하여 반환합니다. 실패 시 빈 JSON 반환."""
        if not data:
            return "{}"
        try:
            return self._cipher_suite.decrypt(data.encode('utf-8')).decode('utf-8')
        except Exception as e:
            logger.error("데이터 복호화 실패: %s", e)
            return "{}"

    # ── 사용자 관리 ─────────────────────────────────────────────────────────

    async def get_user_profile(self, user_id: str) -> dict[str, Any]:
        db = await self.ensure_db()
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        
        if row:
            profile = dict(row)
            profile["style_pref"] = json.loads(profile.get("style_pref", "{}"))
            
            # llm_keys 복호화
            encrypted_keys = profile.get("llm_keys")
            if encrypted_keys and encrypted_keys.startswith("gAAAAA"):
                decrypted_keys_str = self._decrypt(encrypted_keys)
            else:
                # 마이그레이션 호환성: 평문이거나 빈 값일 경우
                decrypted_keys_str = encrypted_keys or "{}"
                
            try:
                profile["llm_keys"] = json.loads(decrypted_keys_str)
            except json.JSONDecodeError:
                profile["llm_keys"] = {}
                
            return profile

        default_style = {"tone": "친절하고 전문적임", "language": "한국어", "detail_level": "상세함"}
        empty_keys_encrypted = self._encrypt("{}")
        await db.execute(
            "INSERT INTO users (user_id, name, style_pref, llm_keys) VALUES (?, ?, ?, ?)",
            (user_id, "User", json.dumps(default_style, ensure_ascii=False), empty_keys_encrypted)
        )
        await db.commit()
        return {"user_id": user_id, "name": "User", "style_pref": default_style, "llm_keys": {}}

    async def update_user_profile(self, user_id: str, updates: dict[str, Any]) -> None:
        unknown = set(updates.keys()) - _ALLOWED_USER_COLUMNS
        if unknown:
            raise ValueError(f"허용되지 않은 프로필 필드: {unknown}")

        db = await self.ensure_db()
        if "style_pref" in updates:
            updates["style_pref"] = json.dumps(updates["style_pref"], ensure_ascii=False)
        if "llm_keys" in updates:
            # llm_keys 암호화
            keys_json_str = json.dumps(updates["llm_keys"], ensure_ascii=False)
            updates["llm_keys"] = self._encrypt(keys_json_str)

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        params = list(updates.values()) + [user_id]
        await db.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", params)
        await db.commit()

    # ── 세션 및 메시지 ───────────────────────────────────────────────────────

    async def init_session(self, session_id: str, user_id: str, channel_id: str) -> None:
        await self.get_user_profile(user_id)
        db = await self.ensure_db()
        
        await db.execute(
            "INSERT OR IGNORE INTO sessions (session_id, user_id, channel_id) VALUES (?, ?, ?)",
            (session_id, user_id, channel_id)
        )
        await db.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?", (session_id,))
        await db.commit()

        key = f"session:{session_id}:state"
        await self._redis.hset(key, mapping={"user_id": user_id, "channel_id": channel_id})
        await self._redis.expire(key, _SESSION_TTL)

    async def add_message(self, session_id: str, user_id: str, role: str, content: str, provider: str = "system", tokens: int = 0, thread_id: str | None = None) -> None:
        db = await self.ensure_db()
        await db.execute(
            "INSERT INTO chat_history (user_id, session_id, thread_id, role, content, provider, tokens_in) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, session_id, thread_id, role, content, provider, tokens)
        )
        await db.commit()

        # Redis 캐싱
        key = f"session:{session_id}:messages"
        msg = {"role": role, "content": content, "timestamp": datetime.now(timezone.utc).isoformat()}
        await self._redis.rpush(key, json.dumps(msg, ensure_ascii=False))
        await self._redis.ltrim(key, -_MAX_MESSAGES, -1)

    async def build_context_for_llm(self, session_id: str, user_id: str, provider: str = "gemini") -> list[dict[str, Any]]:
        db = await self.ensure_db()
        async with db.execute(
            "SELECT role, content FROM chat_history WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, _HISTORY_LIMIT)
        ) as cursor:
            rows = await cursor.fetchall()
            history = [dict(r) for r in rows]
            history.reverse()

        profile = await self.get_user_profile(user_id)
        style = profile.get("style_pref", {})
        
        context = []
        persona = f"[사용자 프로필] ID: {user_id}, 스타일: {style.get('tone')}, 언어: {style.get('language')}"
        
        if provider == "gemini":
            context.append({"role": "user", "content": persona})
            context.append({"role": "model", "content": "확인했습니다. 이전 맥락을 고려하여 답변하겠습니다."})
        else:
            context.append({"role": "system", "content": persona})

        for msg in history:
            role = msg["role"]
            if provider == "gemini":
                role = "model" if role in ["assistant", "cassiopeia", "cassiopeia_agent"] else "user"
            context.append({"role": role, "content": msg["content"]})
        return context

    # ── 에이전트 로그 ────────────────────────────────────────────────────────

    async def add_agent_log(self, agent_name: str, action: str, message: str, task_id: str | None = None, session_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
        db = await self.ensure_db()
        await db.execute(
            "INSERT INTO agent_logs (agent_name, task_id, session_id, action, message, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (agent_name, task_id, session_id, action, message, json.dumps(payload or {}, ensure_ascii=False))
        )
        await db.commit()

    # ── 시크릿(API 키) 관리 ──────────────────────────────────────────────────

    async def save_agent_secrets(self, agent_name: str, secrets: dict[str, str]) -> None:
        """에이전트별 시크릿(API 키 등)을 암호화하여 Redis에 저장합니다."""
        raw_json = json.dumps(secrets, ensure_ascii=False)
        encrypted = self._cipher_suite.encrypt(raw_json.encode("utf-8")).decode("utf-8")
        
        key = f"agent:{agent_name}:secrets"
        await self._redis.hset(key, mapping={"encrypted_secrets": encrypted})
        # 시크릿은 별도의 만료 시간을 두지 않거나 매우 길게 설정 (영구 저장성)
        logger.info("[StateManager] 에이전트 시크릿 저장 완료: %s", agent_name)

    async def get_agent_secrets(self, agent_name: str) -> dict[str, str]:
        """Redis에서 암호화된 에이전트 시크릿을 가져와 복호화합니다."""
        key = f"agent:{agent_name}:secrets"
        data = await self._redis.hgetall(key)
        if not data or "encrypted_secrets" not in data:
            return {}
        
        try:
            encrypted = data["encrypted_secrets"]
            decrypted = self._cipher_suite.decrypt(encrypted.encode("utf-8")).decode("utf-8")
            return json.loads(decrypted)
        except Exception as e:
            logger.error("[StateManager] 시크릿 복호화 실패 (%s): %s", agent_name, e)
            return {}

    async def delete_agent_secrets(self, agent_name: str) -> None:
        """에이전트 시크릿을 삭제합니다."""
        key = f"agent:{agent_name}:secrets"
        await self._redis.delete(key)
        logger.info("[StateManager] 에이전트 시크릿 삭제 완료: %s", agent_name)

    # ── 유틸리티 및 상태 관리 ──────────────────────────────────────────────────

    async def update_task_state(self, task_id: str, fields: dict[str, Any]) -> None:
        key = f"task:{task_id}:state"
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        serialized = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)) for k, v in fields.items()}
        await self._redis.hset(key, mapping=serialized)
        await self._redis.expire(key, _TASK_TTL)

    async def get_task_state(self, task_id: str) -> dict[str, Any]:
        return await self._redis.hgetall(f"task:{task_id}:state")

    async def get_session_context_summary(self, session_id: str) -> dict[str, Any]:
        state = await self._redis.hgetall(f"session:{session_id}:state")
        user_id = state.get("user_id", "")
        style = {}
        if user_id:
            profile = await self.get_user_profile(user_id)
            style = profile.get("style_pref", {})
        
        async with (await self.ensure_db()).execute("SELECT last_summary FROM sessions WHERE session_id = ?", (session_id,)) as cursor:
            row = await cursor.fetchone()
            last_summary = row["last_summary"] if row else ""

        return {"style": style, "last_summary": last_summary}

    async def maybe_summarize(self, session_id: str) -> None:
        # 요약 로직은 향후 LLM 연동 시 구체화 (현재는 placeholder)
        pass

    # ── Admin API 지원 조회 메서드 ──────────────────────────────────────────────

    async def get_agent_logs(
        self,
        agent_name: str | None = None,
        action: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """에이전트 활동 로그를 필터링·페이지네이션하여 반환합니다."""
        db = await self.ensure_db()
        conditions: list[str] = []
        params: list[Any] = []
        if agent_name:
            conditions.append("agent_name = ?")
            params.append(agent_name)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM agent_logs {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def count_agent_logs(
        self,
        agent_name: str | None = None,
        action: str | None = None,
        task_id: str | None = None,
    ) -> int:
        """로그 총 건수 반환 (페이지네이션 total 계산용)."""
        db = await self.ensure_db()
        conditions: list[str] = []
        params: list[Any] = []
        if agent_name:
            conditions.append("agent_name = ?")
            params.append(agent_name)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with db.execute(f"SELECT COUNT(*) FROM agent_logs {where}", params) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def list_sessions(
        self, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """세션 목록과 전체 건수를 함께 반환합니다."""
        db = await self.ensure_db()
        async with db.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()

        async with db.execute("SELECT COUNT(*) FROM sessions") as cursor:
            total_row = await cursor.fetchone()
        total = total_row[0] if total_row else 0

        return [dict(r) for r in rows], total

    async def delete_session(self, session_id: str) -> None:
        """세션과 관련된 대화 이력을 SQLite와 Redis에서 모두 삭제합니다."""
        db = await self.ensure_db()
        await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
        await db.commit()
        await self._redis.delete(f"session:{session_id}:state")
        await self._redis.delete(f"session:{session_id}:messages")

    async def get_session_history(
        self, session_id: str, limit: int = 30, offset: int = 0
    ) -> list[dict[str, Any]]:
        """특정 세션의 대화 이력을 최신순으로 반환합니다."""
        db = await self.ensure_db()
        async with db.execute(
            "SELECT role, content, provider, created_at FROM chat_history "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        # 최신순 조회 후 시간순으로 뒤집어 반환
        return list(reversed([dict(r) for r in rows]))

    async def list_users(
        self, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """사용자 목록과 전체 건수를 함께 반환합니다."""
        db = await self.ensure_db()
        async with db.execute(
            "SELECT user_id, name, created_at FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()

        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_row = await cursor.fetchone()
        total = total_row[0] if total_row else 0

        return [dict(r) for r in rows], total

    # ── 작업 히스토리 ──────────────────────────────────────────────────────────

    async def save_task_history(
        self, task_id: str, user_id: str, content: str, status: str = "PENDING"
    ) -> None:
        """사용자 작업 히스토리에 새 태스크를 저장합니다."""
        db = await self.ensure_db()
        await db.execute(
            "INSERT OR IGNORE INTO task_history (task_id, user_id, content, status) VALUES (?, ?, ?, ?)",
            (task_id, user_id, content, status),
        )
        await db.commit()

    async def update_task_history_status(self, task_id: str, status: str) -> None:
        """태스크 상태를 업데이트합니다."""
        db = await self.ensure_db()
        await db.execute(
            "UPDATE task_history SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (status, task_id),
        )
        await db.commit()

    async def get_user_task_history(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """사용자의 작업 히스토리를 최신순으로 반환합니다."""
        db = await self.ensure_db()
        conditions = ["user_id = ?"]
        params: list[Any] = [user_id]

        if status_filter:
            conditions.append("status = ?")
            params.append(status_filter)

        where = "WHERE " + " AND ".join(conditions)
        async with db.execute(
            f"SELECT task_id, user_id, content, status, created_at, updated_at FROM task_history {where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ) as cursor:
            rows = await cursor.fetchall()

        async with db.execute(
            f"SELECT COUNT(*) FROM task_history {where}", params
        ) as cursor:
            total_row = await cursor.fetchone()
        total = total_row[0] if total_row else 0

        return [dict(r) for r in rows], total

    # ── Idempotency ────────────────────────────────────────────────────────────

    async def get_idempotency_result(self, key: str) -> dict[str, Any] | None:
        """Idempotency 키에 캐시된 응답을 반환합니다. 없으면 None."""
        raw = await self._redis.get(f"idempotency:{key}")
        if raw:
            return json.loads(raw)
        return None

    async def save_idempotency_result(
        self, key: str, result: dict[str, Any], ttl: int = 86400
    ) -> None:
        """Idempotency 결과를 Redis에 저장합니다 (기본 TTL: 24시간)."""
        await self._redis.setex(
            f"idempotency:{key}", ttl,
            json.dumps(result, ensure_ascii=False),
        )

    async def scan_task_ids(self, limit: int = 50) -> list[str]:
        """
        Redis에서 task:*:state 키를 스캔하여 task_id 목록을 반환합니다.
        최대 limit 개까지 반환하며, 생성 시각 역순 정렬은 보장되지 않습니다.
        """
        keys: list[str] = []
        async for key in self._redis.scan_iter("task:*:state", count=100):
            keys.append(key)
            if len(keys) >= limit:
                break
        # "task:{id}:state" → "{id}"
        return [k.split(":")[1] for k in keys]

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
        if self._db_conn:
            await self._db_conn.close()
