"""Tests for configuration system."""


class TestEvalSettings:
    """Test configuration loading and defaults."""

    def test_default_weights_sum_to_one(self):
        """Weights should sum to 1.0."""
        # Import fresh to get defaults
        from raidar.config import ScoringWeights

        weights = ScoringWeights()
        total = (
            weights.functional
            + weights.acceptance
            + weights.visual
            + weights.verification_stability
        )
        assert abs(total - 1.0) < 0.001

    def test_default_timeouts_are_reasonable(self):
        """Timeouts should be positive integers."""
        from raidar.config import TimeoutSettings

        timeouts = TimeoutSettings()
        assert timeouts.build > 0
        assert timeouts.test > 0
        assert timeouts.typecheck > 0
        assert timeouts.gate > 0

    def test_llm_as_judge_defaults(self):
        """LLM-as-judge should have sensible defaults."""
        from raidar.config import LLMAsJudgeSettings

        judge = LLMAsJudgeSettings()
        assert judge.max_tokens > 0
        assert judge.max_source_chars > 0
        assert judge.max_retries >= 0
        assert judge.model == "gpt-5.5"
        assert judge.reasoning_effort == "low"
        assert judge.codex_auth_mode == "chatgpt"

    def test_settings_singleton_exports(self):
        """Settings singleton should be importable and have all subsections."""
        from raidar.config import settings

        assert settings.weights is not None
        assert settings.timeouts is not None
        assert settings.llm_as_judge is not None
        assert settings.verification_stability is not None
        assert settings.gate is not None
        assert settings.visual is not None
        assert settings.resource_efficiency is not None


class TestEnvironmentOverrides:
    """Test environment variable overrides."""

    def test_env_override_llm_model(self, monkeypatch):
        """Environment variable should override LLM model."""
        monkeypatch.setenv("EVAL_LLM_AS_JUDGE__MODEL", "test/model")

        # Need to reimport to pick up env var
        from raidar.config import LLMAsJudgeSettings

        settings = LLMAsJudgeSettings()
        assert settings.model == "test/model"

    def test_env_override_timeout(self, monkeypatch):
        """Environment variable should override timeout."""
        monkeypatch.setenv("EVAL_TIMEOUTS__BUILD", "300")

        from raidar.config import TimeoutSettings

        settings = TimeoutSettings()
        assert settings.build == 300
