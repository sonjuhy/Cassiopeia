"""
models.py — Pydantic 스키마 및 유틸리티 함수 테스트
"""
from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from agents.cassiopeia_agent.models import (
    AGENT_TIMEOUT_MAP,
    NLUMetadata,
    NLU_CONFIDENCE_THRESHOLD,
    PlanStep,
    PlanStepMetadata,
    _build_timeout_map,
)


# ── NLUMetadata ───────────────────────────────────────────────────────────────

class TestNLUMetadata:
    def test_valid(self):
        m = NLUMetadata(reason="테스트", confidence_score=0.85, requires_user_approval=False)
        assert m.confidence_score == 0.85
        assert m.requires_user_approval is False

    def test_confidence_boundary_min(self):
        m = NLUMetadata(reason="low", confidence_score=0.0)
        assert m.confidence_score == 0.0

    def test_confidence_boundary_max(self):
        m = NLUMetadata(reason="high", confidence_score=1.0)
        assert m.confidence_score == 1.0

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            NLUMetadata(reason="bad", confidence_score=1.1)

    def test_confidence_negative(self):
        with pytest.raises(ValidationError):
            NLUMetadata(reason="bad", confidence_score=-0.1)

    def test_requires_approval_defaults_false(self):
        m = NLUMetadata(reason="r", confidence_score=0.5)
        assert m.requires_user_approval is False



class TestAgentTimeoutMap:
    def test_default_values(self):
        assert AGENT_TIMEOUT_MAP["archive_agent"] == 300
        assert AGENT_TIMEOUT_MAP["calendar_agent"] == 60

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_TIMEOUT_OVERRIDES", "archive_agent:900,file_agent:180")
        result = _build_timeout_map()
        assert result["archive_agent"] == 900
        assert result["file_agent"] == 180
        assert result["calendar_agent"] == 60  # unchanged

    def test_env_override_invalid_value_ignored(self, monkeypatch):
        monkeypatch.setenv("AGENT_TIMEOUT_OVERRIDES", "archive_agent:notanumber")
        result = _build_timeout_map()
        assert result["archive_agent"] == 300  # unchanged

    def test_env_override_empty(self, monkeypatch):
        monkeypatch.setenv("AGENT_TIMEOUT_OVERRIDES", "")
        result = _build_timeout_map()
        assert result["archive_agent"] == 300


# ── NLU_CONFIDENCE_THRESHOLD ──────────────────────────────────────────────────

class TestConfidenceThreshold:
    def test_default(self):
        assert NLU_CONFIDENCE_THRESHOLD == 0.7

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("NLU_CONFIDENCE_THRESHOLD", "0.8")
        from importlib import reload
        import agents.cassiopeia_agent.models as models_mod
        reload(models_mod)
        assert models_mod.NLU_CONFIDENCE_THRESHOLD == 0.8
        # 원복
        monkeypatch.delenv("NLU_CONFIDENCE_THRESHOLD", raising=False)
        reload(models_mod)
