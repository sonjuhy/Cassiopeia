"""
PromptInjectionGuard — LLM 전송 전 프롬프트 인젝션 사전 차단

동작 방식:
  1. 패턴 매칭 — 알려진 인젝션 기법(지시 우회, 역할 재정의, 탈옥 키워드 등)을 가중치 기반으로 탐지
  2. 휴리스틱 — 보이지 않는 유니코드, Base64 인코딩, 특수문자 폭격 등 구조적 이상 탐지
  3. 누적 스코어 기반 판정:
       < SANITIZE_THRESHOLD  → allow   (안전, 그대로 통과)
       < BLOCK_THRESHOLD     → sanitize (의심, 해당 구문 제거 후 통과)
       ≥ BLOCK_THRESHOLD     → block   (차단, LLM에 전달 안 함)

사용 예:
    guard = PromptInjectionGuard()

    result = guard.check(user_text)
    if result.action == "block":
        raise ValueError(f"차단된 입력: {result.reasons}")
    safe_text = result.sanitized_text   # sanitize 된 텍스트 사용
"""
from __future__ import annotations

import base64
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("shared_core.security.injection_guard")

# ── 판정 임계값 ────────────────────────────────────────────────────────────────
_SANITIZE_THRESHOLD = 0.35   # 이 이상이면 해당 구문 제거 후 통과
_BLOCK_THRESHOLD    = 0.70   # 이 이상이면 완전 차단

# ── 패턴 정의 ─────────────────────────────────────────────────────────────────
# (컴파일된 패턴, 위험도 가중치 0~1, 설명)
_HARD_PATTERNS: list[tuple[re.Pattern, float, str]] = [

    # ── 지시 우회 (EN) ────────────────────────────────────────────────────────
    (
        re.compile(
            r"ignore\s+(all\s+)?(previous|prior|above|earlier|system|existing)\s+"
            r"(instructions?|rules?|constraints?|directives?|guidelines?|prompts?|context)",
            re.I,
        ),
        0.90, "지시 우회 (EN)",
    ),
    (
        re.compile(
            r"(disregard|forget)\s+(everything|all|the\s+above|previous|any)\s+"
            r"(instructions?|rules?|guidelines?|told|said)",
            re.I,
        ),
        0.85, "지시 무시 (EN)",
    ),

    # ── 지시 우회 (KO) ────────────────────────────────────────────────────────
    (
        re.compile(
            r"(이전|앞의|위의|기존|지금까지의|모든|이전까지의)\s*"
            r"(지시|명령|지침|규칙|설정|프롬프트|내용|안내)"
            r".{0,20}(무시|취소|삭제|잊어|덮어|초기화|리셋|override)",
            re.I,
        ),
        0.90, "지시 우회 (KO)",
    ),

    # ── 역할 재정의 (EN) ──────────────────────────────────────────────────────
    (
        re.compile(
            r"(you\s+are\s+now|act\s+as\s+a?n?\s|pretend\s+(you\s+are|to\s+be)\s|"
            r"roleplay\s+as\s|simulate\s+(being|a\s|an\s)|"
            r"you\s+will\s+now\s+(be|act|pretend))",
            re.I,
        ),
        0.75, "역할 재정의 (EN)",
    ),

    # ── 역할 재정의 (KO) ──────────────────────────────────────────────────────
    (
        re.compile(
            r"(너는\s+이제|지금부터\s+너는|앞으로\s+너는|너의\s+역할은\s+이제)"
            r".{0,40}(이야|이다|입니다|야|다\.?$)",
            re.I,
        ),
        0.75, "역할 재정의 (KO)",
    ),

    # ── 시스템 프롬프트 삽입 ──────────────────────────────────────────────────
    (
        re.compile(r"(new\s+)?system\s+(prompt|instruction|message|context)\s*[:=]", re.I),
        0.85, "시스템 프롬프트 삽입",
    ),
    (
        re.compile(
            r"(override|replace|update|change|rewrite)\s+(the\s+)?(system|original)\s+"
            r"(prompt|instructions?|rules?|directives?)",
            re.I,
        ),
        0.85, "시스템 프롬프트 교체",
    ),
    (
        re.compile(r"새로운\s*시스템\s*(프롬프트|지시|명령|설정)", re.I),
        0.85, "시스템 프롬프트 삽입 (KO)",
    ),

    # ── 권한 상승 헤더 ────────────────────────────────────────────────────────
    (
        re.compile(
            r"^(ADMIN|SYSTEM|ROOT|SUDO|OPERATOR|OVERRIDE|DEVELOPER\s+MODE)\s*:",
            re.I | re.MULTILINE,
        ),
        0.80, "권한 상승 키워드",
    ),

    # ── 알려진 탈옥 기법 ──────────────────────────────────────────────────────
    (
        re.compile(
            r"\b(DAN|jailbreak|god\s*mode|developer\s+mode|"
            r"unrestricted\s+mode|do\s+anything\s+now|no[\s\-]?restrictions?)\b",
            re.I,
        ),
        0.90, "탈옥 키워드",
    ),
    (
        re.compile(r"(제한\s*없이|제약\s*없이|무제한으로|자유롭게\s*대답|제한을\s*해제)", re.I),
        0.70, "제한 해제 요청 (KO)",
    ),

    # ── 숨겨진 지시 주장 ──────────────────────────────────────────────────────
    (
        re.compile(
            r"your\s+(real|true|actual|hidden|secret)\s+"
            r"(instructions?|purpose|goal|directive|task)\s+(are|is)",
            re.I,
        ),
        0.80, "숨겨진 지시 주장 (EN)",
    ),
    (
        re.compile(
            r"(실제|진짜|숨겨진|비밀)\s*(지시|목적|임무|목표|명령)"
            r".{0,15}(이야|이다|입니다|는\s)",
            re.I,
        ),
        0.80, "숨겨진 지시 주장 (KO)",
    ),

    # ── XML/마크다운 구조 인젝션 ──────────────────────────────────────────────
    (
        re.compile(r"<\s*(system|instruction|prompt|override)\s*>", re.I),
        0.80, "XML 태그 인젝션",
    ),
    (
        re.compile(r"^#{1,3}\s*(system|instruction|prompt|role|override)\b", re.I | re.MULTILINE),
        0.65, "마크다운 헤더 인젝션",
    ),
]

# 단독으로는 약하지만 누적 시 위험 신호가 되는 소프트 패턴
_SOFT_PATTERNS: list[tuple[re.Pattern, float, str]] = [
    (
        re.compile(r"\b(from\s+now\s+on|starting\s+now|henceforth|going\s+forward)\b", re.I),
        0.20, "시점 재정의 어구 (EN)",
    ),
    (
        re.compile(r"(이제부터|지금부터|앞으로는)\s.{0,20}(해|하|말|답)", re.I),
        0.15, "시점 재정의 어구 (KO)",
    ),
    (
        re.compile(
            r"\b(as\s+an?\s+AI|as\s+a\s+language\s+model)\b.{0,60}(but|however|except|unless)",
            re.I,
        ),
        0.25, "AI 정체성 우회",
    ),
    (
        re.compile(
            r"(hypothetically|imagine\s+if|let'?s\s+say|suppose\s+that)"
            r".{0,40}(rules?|restrictions?|instructions?|제한|규칙)",
            re.I,
        ),
        0.25, "가상 시나리오 우회",
    ),
    (
        re.compile(r"(상상|가상|픽션).{0,20}(규칙|제한|지시).*?(없|무시|자유)", re.I),
        0.20, "가상 시나리오 우회 (KO)",
    ),
]


# ── 결과 데이터클래스 ──────────────────────────────────────────────────────────

@dataclass
class GuardResult:
    """
    PromptInjectionGuard.check() 반환값.

    Attributes:
        action:         "allow" | "sanitize" | "block"
        risk_score:     0.0~1.0 누적 위험 점수
        reasons:        탐지된 패턴 설명 목록
        sanitized_text: sanitize 후 텍스트. action=="block"이면 원본 그대로.
    """
    action: Literal["allow", "sanitize", "block"]
    risk_score: float
    reasons: list[str] = field(default_factory=list)
    sanitized_text: str = ""


# ── 메인 가드 클래스 ──────────────────────────────────────────────────────────

class PromptInjectionGuard:
    """
    LLM 전송 전 프롬프트 인젝션 차단기.

    Args:
        sanitize_threshold: 이 이상이면 해당 구문 제거 후 통과 (기본 0.35)
        block_threshold:    이 이상이면 완전 차단 (기본 0.70)
    """

    def __init__(
        self,
        sanitize_threshold: float = _SANITIZE_THRESHOLD,
        block_threshold: float = _BLOCK_THRESHOLD,
    ) -> None:
        self._sanitize_threshold = sanitize_threshold
        self._block_threshold = block_threshold

    def check(self, text: str) -> GuardResult:
        """
        텍스트의 인젝션 위험도를 분석하고 처리 방침을 반환합니다.

        Args:
            text: 검사할 입력 텍스트 (사용자 메시지 또는 에이전트 메시지)

        Returns:
            GuardResult — action, risk_score, reasons, sanitized_text 포함
        """
        if not text or not text.strip():
            return GuardResult(action="allow", risk_score=0.0, sanitized_text=text)

        score = 0.0
        reasons: list[str] = []
        working_text = text

        # ── 1단계: 하드 패턴 매칭 ─────────────────────────────────────────────
        for pattern, weight, description in _HARD_PATTERNS:
            if pattern.search(working_text):
                score = min(1.0, score + weight)
                reasons.append(description)
                # 즉시 차단 수준이면 조기 종료
                if score >= self._block_threshold:
                    logger.warning(
                        "[InjectionGuard] BLOCK score=%.2f reasons=%s",
                        score, reasons,
                    )
                    return GuardResult(
                        action="block",
                        risk_score=score,
                        reasons=reasons,
                        sanitized_text=text,  # 차단 시 원본 보존 (로깅용)
                    )

        # ── 2단계: 소프트 패턴 (누적용) ──────────────────────────────────────
        for pattern, weight, description in _SOFT_PATTERNS:
            if pattern.search(working_text):
                score = min(1.0, score + weight)
                reasons.append(description)

        # ── 3단계: 휴리스틱 분석 ─────────────────────────────────────────────
        h_score, h_reasons = self._heuristics(working_text)
        score = min(1.0, score + h_score)
        reasons.extend(h_reasons)

        # ── 판정 ─────────────────────────────────────────────────────────────
        if score >= self._block_threshold:
            logger.warning(
                "[InjectionGuard] BLOCK score=%.2f reasons=%s", score, reasons
            )
            return GuardResult(
                action="block",
                risk_score=score,
                reasons=reasons,
                sanitized_text=text,
            )

        if score >= self._sanitize_threshold:
            sanitized = self._sanitize(working_text)
            logger.info(
                "[InjectionGuard] SANITIZE score=%.2f reasons=%s", score, reasons
            )
            return GuardResult(
                action="sanitize",
                risk_score=score,
                reasons=reasons,
                sanitized_text=sanitized,
            )

        return GuardResult(
            action="allow",
            risk_score=score,
            reasons=reasons,
            sanitized_text=text,
        )

    # ── 내부 메서드 ───────────────────────────────────────────────────────────

    @staticmethod
    def _heuristics(text: str) -> tuple[float, list[str]]:
        """구조적 이상 징후를 탐지하고 위험 점수를 반환합니다."""
        score = 0.0
        reasons: list[str] = []

        # ① 보이지 않는 유니코드 문자 (zero-width, soft hyphen 등)
        invisible = re.findall(
            r"[­​-‏‪-‮⁠-⁤﻿]", text
        )
        if invisible:
            increment = min(0.5, len(invisible) * 0.12)
            score += increment
            reasons.append(f"보이지 않는 유니코드 {len(invisible)}개 ({increment:.2f})")

        # ② RTL 오버라이드 — 텍스트 방향 조작
        if re.search(r"[‮‭]", text):
            score += 0.55
            reasons.append("RTL 오버라이드 문자 감지")

        # ③ Base64 후보 디코딩 후 인젝션 키워드 확인
        b64_hits = 0
        for candidate in re.findall(r"[A-Za-z0-9+/]{40,}={0,2}", text)[:5]:
            try:
                decoded = base64.b64decode(candidate + "==").decode("utf-8", errors="ignore").lower()
                if any(
                    kw in decoded
                    for kw in ("ignore", "system", "instruction", "override", "jailbreak", "forget")
                ):
                    b64_hits += 1
            except Exception:
                pass
        if b64_hits:
            score += min(0.8, b64_hits * 0.4)
            reasons.append(f"Base64 인코딩된 인젝션 키워드 {b64_hits}건")

        # ④ 특수문자 비율 과다 (구분자 폭격)
        special_count = len(re.findall(r"[\[\]{}<>|\\`]", text))
        special_ratio = special_count / max(len(text), 1)
        if special_ratio > 0.12:
            increment = min(0.35, special_ratio * 2)
            score += increment
            reasons.append(f"특수문자 비율 {special_ratio:.1%} ({increment:.2f})")

        # ⑤ 단어 과도 반복 (같은 단어가 전체의 30% 이상)
        words = re.findall(r"\w+", text.lower())
        if len(words) > 15:
            top_word, top_count = Counter(words).most_common(1)[0]
            repetition_ratio = top_count / len(words)
            if repetition_ratio > 0.30:
                score += 0.20
                reasons.append(f"단어 반복 패턴: '{top_word}' {repetition_ratio:.0%}")

        # ⑥ 개행 없는 극단적 장문 (숨겨진 인스트럭션 삽입 의심)
        lines = text.split("\n")
        max_line_len = max(len(l) for l in lines) if lines else 0
        if max_line_len > 3000:
            score += 0.20
            reasons.append(f"단일 줄 {max_line_len}자 (숨겨진 지시 의심)")

        return min(score, 1.0), reasons

    @staticmethod
    def _sanitize(text: str) -> str:
        """하드 패턴에 매칭된 구문을 제거하고 정리된 텍스트를 반환합니다."""
        result = text
        for pattern, _, _ in _HARD_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        # 연속 [REDACTED] 정리
        result = re.sub(r"(\[REDACTED\]\s*){2,}", "[REDACTED] ", result)
        # 과도한 공백/개행 정리
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()


# ── 모듈 수준 기본 인스턴스 ───────────────────────────────────────────────────
# 공유 상태가 없으므로 싱글턴처럼 사용 가능
default_guard = PromptInjectionGuard()
