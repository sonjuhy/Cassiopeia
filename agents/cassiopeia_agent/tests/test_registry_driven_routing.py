"""
지휘자(CassiopeiaManager)의 '완전 독립' 보장 테스트.

지휘자는 에이전트 이름을 코드에서 특별 취급하지 않는다. 라우팅 파라미터 가이드,
작업 타임아웃, 커뮤니케이션 수신자는 모두 레지스트리에 저장된 self-describing
메타데이터(params_schema / default_timeout / routing)에서만 결정되어야 한다.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from agents.cassiopeia_agent.manager import (
    CassiopeiaManager,
    _GENERIC_PARAMS_GUIDE,
    _resolve_timeout,
    _tool_parameters_for,
)
from agents.cassiopeia_agent.models import DEFAULT_AGENT_TIMEOUT


# ── _tool_parameters_for: params_schema 레지스트리 기반화 ──────────────────────

class TestToolParametersFor:
    def test_uses_declared_params_schema(self):
        reg = {"params_schema": {"action": "search", "params": {"query": "검색어"}}}
        assert _tool_parameters_for(reg) == {"action": "search", "params": {"query": "검색어"}}

    def test_generic_fallback_when_missing(self):
        assert _tool_parameters_for({}) == _GENERIC_PARAMS_GUIDE

    def test_generic_fallback_when_none(self):
        assert _tool_parameters_for({"params_schema": None}) == _GENERIC_PARAMS_GUIDE

    def test_generic_fallback_when_empty_dict(self):
        assert _tool_parameters_for({"params_schema": {}}) == _GENERIC_PARAMS_GUIDE

    def test_returns_copy_not_shared_generic(self):
        """generic 가이드를 반환할 때 모듈 상수를 그대로 노출해 변형 위험을 만들지 않는다."""
        out = _tool_parameters_for({})
        out["action"] = "mutated"
        assert _GENERIC_PARAMS_GUIDE["action"] != "mutated"

    def test_no_agent_name_branching(self):
        """에이전트 이름과 무관하게 동일 규칙이 적용된다 (이름 하드코딩 부재)."""
        schema = {"action": "x", "params": {}}
        for name in ("sandbox_agent", "cassiopeia_agent", "totally_new_sdk_agent"):
            reg = {"name": name, "params_schema": schema}
            assert _tool_parameters_for(reg) == schema


# ── _resolve_timeout: default_timeout 레지스트리 기반화 ────────────────────────

class TestResolveTimeout:
    def test_uses_agent_declared_timeout(self):
        reg = {"default_timeout": 90}
        assert _resolve_timeout("any_agent", reg, overrides={}) == 90

    def test_global_default_when_not_declared(self):
        assert _resolve_timeout("any_agent", {}, overrides={}) == DEFAULT_AGENT_TIMEOUT

    def test_global_default_when_declared_none(self):
        assert _resolve_timeout("any_agent", {"default_timeout": None}, overrides={}) == DEFAULT_AGENT_TIMEOUT

    def test_global_default_when_declared_non_positive(self):
        assert _resolve_timeout("a", {"default_timeout": 0}, overrides={}) == DEFAULT_AGENT_TIMEOUT
        assert _resolve_timeout("a", {"default_timeout": -5}, overrides={}) == DEFAULT_AGENT_TIMEOUT

    def test_operator_override_wins_over_declaration(self):
        """운영자가 env로 지정한 오버라이드가 에이전트 선언보다 우선한다."""
        reg = {"default_timeout": 90}
        assert _resolve_timeout("archive_agent", reg, overrides={"archive_agent": 900}) == 900

    def test_no_hardcoded_agent_names(self):
        """이전에 하드코딩되던 이름(communication_agent 등)도 특별 취급 없이 전역 기본값."""
        assert _resolve_timeout("communication_agent", {}, overrides={}) == DEFAULT_AGENT_TIMEOUT
        assert _resolve_timeout("sandbox_agent", {}, overrides={}) == DEFAULT_AGENT_TIMEOUT
