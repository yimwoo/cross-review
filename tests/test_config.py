"""Tests for cross-review configuration loading (design doc §7)."""

from pathlib import Path
from unittest.mock import patch

from cross_review.config import (
    AppConfig,
    RoleConfig,
    load_config,
    load_config_from_toml_string,
)


# ---------------------------------------------------------------------------
# Default config tests
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    """AppConfig() with no arguments should produce sane built-in defaults."""

    def test_default_router_mode_is_review(self):
        cfg = AppConfig()
        assert cfg.router.default_mode == "review"

    def test_default_router_use_classifier_is_false(self):
        cfg = AppConfig()
        assert cfg.router.use_classifier is False

    def test_default_router_classifier_provider(self):
        cfg = AppConfig()
        assert cfg.router.classifier_provider == "claude"

    def test_default_router_classifier_model(self):
        cfg = AppConfig()
        assert cfg.router.classifier_model == "claude-haiku"

    def test_default_budget_values(self):
        cfg = AppConfig()
        assert cfg.budget.max_total_calls == 4
        assert cfg.budget.max_reviewers == 2
        assert cfg.budget.soft_token_limit == 20_000
        assert cfg.budget.hard_token_limit == 30_000
        assert cfg.budget.orchestration_timeout_seconds == 60

    def test_default_roles_has_builder(self):
        cfg = AppConfig()
        assert "builder" in cfg.roles
        assert cfg.roles["builder"].provider == "claude"
        assert cfg.roles["builder"].model == "claude-sonnet-4-5-20250514"

    def test_default_roles_has_skeptic_reviewer(self):
        cfg = AppConfig()
        assert "skeptic_reviewer" in cfg.roles
        assert cfg.roles["skeptic_reviewer"].provider == "openai"
        assert cfg.roles["skeptic_reviewer"].model == "gpt-4.1"

    def test_default_roles_has_pragmatist_reviewer(self):
        cfg = AppConfig()
        assert "pragmatist_reviewer" in cfg.roles
        assert cfg.roles["pragmatist_reviewer"].provider == "gemini"
        assert cfg.roles["pragmatist_reviewer"].model == "gemini-2.5-pro"

    def test_default_roles_is_independent_copy(self):
        """Mutating one AppConfig's roles must not affect another."""
        cfg1 = AppConfig()
        cfg2 = AppConfig()
        cfg1.roles["builder"] = RoleConfig(provider="test", model="test-model")
        assert cfg2.roles["builder"].provider == "claude"


# ---------------------------------------------------------------------------
# TOML loading tests
# ---------------------------------------------------------------------------


class TestLoadFromTomlString:
    """load_config_from_toml_string should parse TOML and merge with defaults."""

    def test_empty_toml_returns_defaults(self):
        cfg = load_config_from_toml_string("")
        assert cfg.router.default_mode == "review"
        assert cfg.budget.max_total_calls == 4
        assert "builder" in cfg.roles

    def test_toml_overrides_router(self):
        toml_str = """\
[router]
default_mode = "arbitrate"
use_classifier = true
"""
        cfg = load_config_from_toml_string(toml_str)
        assert cfg.router.default_mode == "arbitrate"
        assert cfg.router.use_classifier is True
        # Non-overridden field keeps default
        assert cfg.router.classifier_provider == "claude"

    def test_toml_overrides_budget_partially(self):
        toml_str = """\
[budget]
max_total_calls = 10
"""
        cfg = load_config_from_toml_string(toml_str)
        assert cfg.budget.max_total_calls == 10
        # Other budget fields keep defaults
        assert cfg.budget.max_reviewers == 2
        assert cfg.budget.soft_token_limit == 20_000

    def test_toml_overrides_single_role(self):
        toml_str = """\
[roles.builder]
provider = "openai"
model = "gpt-4.1"
"""
        cfg = load_config_from_toml_string(toml_str)
        assert cfg.roles["builder"].provider == "openai"
        assert cfg.roles["builder"].model == "gpt-4.1"
        # Other roles still present from defaults
        assert "skeptic_reviewer" in cfg.roles
        assert "pragmatist_reviewer" in cfg.roles

    def test_toml_adds_new_role(self):
        toml_str = """\
[roles.security_reviewer]
provider = "claude"
model = "claude-sonnet-4-5-20250514"
"""
        cfg = load_config_from_toml_string(toml_str)
        assert "security_reviewer" in cfg.roles
        assert cfg.roles["security_reviewer"].provider == "claude"
        # Default roles still present
        assert "builder" in cfg.roles

    def test_full_toml_example(self):
        toml_str = """\
[router]
default_mode = "review"
use_classifier = false

[budget]
max_total_calls = 4
max_reviewers = 2
soft_token_limit = 20000
hard_token_limit = 30000
orchestration_timeout_seconds = 60

[roles.builder]
provider = "claude"
model = "claude-sonnet"

[roles.skeptic_reviewer]
provider = "openai"
model = "gpt-5"

[roles.pragmatist_reviewer]
provider = "gemini"
model = "gemini-2.5-pro"
"""
        cfg = load_config_from_toml_string(toml_str)
        assert cfg.router.default_mode == "review"
        assert cfg.budget.max_total_calls == 4
        assert cfg.roles["builder"].model == "claude-sonnet"
        assert cfg.roles["skeptic_reviewer"].model == "gpt-5"


# ---------------------------------------------------------------------------
# load_config() tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """load_config() should respect config file precedence."""

    def test_returns_defaults_when_no_file_exists(self):
        cfg = load_config(config_path=Path("/nonexistent/path/config.toml"))
        assert cfg.router.default_mode == "review"
        assert cfg.budget.max_total_calls == 4
        assert "builder" in cfg.roles

    def test_reads_file_when_it_exists(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """\
[router]
default_mode = "arbitrate"

[budget]
max_total_calls = 8
"""
        )
        cfg = load_config(config_path=config_file)
        assert cfg.router.default_mode == "arbitrate"
        assert cfg.budget.max_total_calls == 8
        # Non-overridden defaults preserved
        assert cfg.budget.max_reviewers == 2
        assert "builder" in cfg.roles

    def test_returns_defaults_when_no_path_and_no_default_file(self, tmp_path: Path):
        """When config_path is None and the default file doesn't exist, return defaults."""
        with patch(
            "cross_review.config._default_config_path",
            return_value=tmp_path / "nonexistent" / "config.toml",
        ):
            cfg = load_config()
        assert cfg.router.default_mode == "review"
        assert cfg.budget.max_total_calls == 4

    def test_reads_default_file_location(self, tmp_path: Path):
        """When config_path is None, fall back to default config file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """\
[router]
default_mode = "arbitrate"
"""
        )
        with patch(
            "cross_review.config._default_config_path",
            return_value=config_file,
        ):
            cfg = load_config()
        assert cfg.router.default_mode == "arbitrate"

    def test_env_var_overrides_file(self, tmp_path: Path):
        """Environment variables take precedence over config file values."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """\
[router]
default_mode = "review"
"""
        )
        with patch.dict(
            "os.environ",
            {"CROSS_REVIEW_ROUTER_DEFAULT_MODE": "arbitrate"},
        ):
            cfg = load_config(config_path=config_file)
        assert cfg.router.default_mode == "arbitrate"

    def test_env_var_overrides_budget(self, tmp_path: Path):
        """Env vars can override budget settings."""
        with patch.dict(
            "os.environ",
            {"CROSS_REVIEW_BUDGET_MAX_TOTAL_CALLS": "12"},
        ):
            cfg = load_config(config_path=Path("/nonexistent/config.toml"))
        assert cfg.budget.max_total_calls == 12
