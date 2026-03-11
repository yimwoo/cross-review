"""Unit tests for cross_review.oca_discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cross_review.oca_discovery import (
    OCA_DEFAULT_MODELS,
    OCA_TOKEN_ENV,
    build_oca_config,
    can_resolve_credentials,
    find_oca_token,
)


# ---------------------------------------------------------------------------
# find_oca_token
# ---------------------------------------------------------------------------


class TestFindOcaToken:
    """Token discovery from env, secrets.json, and token file."""

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCA_TOKEN", "tok-from-env")
        assert find_oca_token() == "tok-from-env"

    def test_env_var_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCA_TOKEN", "  tok-padded  ")
        assert find_oca_token() == "tok-padded"

    def test_from_cline_secrets(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("OCA_TOKEN", raising=False)
        secrets_dir = tmp_path / ".cline" / "data"
        secrets_dir.mkdir(parents=True)
        secrets_file = secrets_dir / "secrets.json"
        secrets_file.write_text(json.dumps({"ocaApiKey": "tok-from-cline"}))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert find_oca_token() == "tok-from-cline"

    def test_from_token_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("OCA_TOKEN", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        token_dir = tmp_path / ".oca"
        token_dir.mkdir()
        (token_dir / "token").write_text("tok-from-file\n")
        assert find_oca_token() == "tok-from-file"

    def test_returns_none_when_nothing_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("OCA_TOKEN", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert find_oca_token() is None

    def test_precedence_env_over_cline(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Env var wins even when Cline secrets exist."""
        monkeypatch.setenv("OCA_TOKEN", "tok-env")
        secrets_dir = tmp_path / ".cline" / "data"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "secrets.json").write_text(
            json.dumps({"ocaApiKey": "tok-cline"})
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert find_oca_token() == "tok-env"

    def test_precedence_cline_over_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Cline secrets win over ~/.oca/token."""
        monkeypatch.delenv("OCA_TOKEN", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Set up both
        secrets_dir = tmp_path / ".cline" / "data"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "secrets.json").write_text(
            json.dumps({"ocaApiKey": "tok-cline"})
        )
        oca_dir = tmp_path / ".oca"
        oca_dir.mkdir()
        (oca_dir / "token").write_text("tok-file")
        assert find_oca_token() == "tok-cline"

    def test_skips_empty_env_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("OCA_TOKEN", "  ")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert find_oca_token() is None

    def test_skips_corrupt_secrets_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("OCA_TOKEN", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        secrets_dir = tmp_path / ".cline" / "data"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "secrets.json").write_text("NOT JSON")
        assert find_oca_token() is None


# ---------------------------------------------------------------------------
# build_oca_config
# ---------------------------------------------------------------------------


class TestBuildOcaConfig:
    """Ephemeral config construction."""

    def test_default_roles_and_models(self) -> None:
        cfg = build_oca_config("test-token")
        assert "oca" in cfg.providers
        assert cfg.providers["oca"].type == "openai_compatible"
        assert cfg.providers["oca"].api_key_env == OCA_TOKEN_ENV
        for role_name, expected_model in OCA_DEFAULT_MODELS.items():
            assert cfg.roles[role_name].provider == "oca"
            assert cfg.roles[role_name].model == expected_model

    def test_custom_models(self) -> None:
        cfg = build_oca_config(
            "test-token",
            models={"builder": "oca/custom-model"},
        )
        assert cfg.roles["builder"].model == "oca/custom-model"
        # Others unchanged
        assert cfg.roles["skeptic_reviewer"].model == OCA_DEFAULT_MODELS["skeptic_reviewer"]

    def test_custom_base_url(self) -> None:
        cfg = build_oca_config("test-token", base_url="https://custom.example.com/v1")
        assert cfg.providers["oca"].base_url == "https://custom.example.com/v1"

    def test_empty_token_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            build_oca_config("")

    def test_no_default_providers_leaked(self) -> None:
        """Config should only contain the 'oca' provider, not defaults."""
        cfg = build_oca_config("test-token")
        assert set(cfg.providers.keys()) == {"oca"}


# ---------------------------------------------------------------------------
# can_resolve_credentials
# ---------------------------------------------------------------------------


class TestCanResolveCredentials:
    """Credential preflight checks."""

    def test_returns_true_with_valid_env_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
        monkeypatch.setenv("OPENAI_API_KEY", "ok-test")
        monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
        from cross_review.config import AppConfig

        cfg = AppConfig()  # default providers with env-based keys
        assert can_resolve_credentials(cfg, "review") is True

    def test_returns_false_when_keys_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        from cross_review.config import AppConfig

        cfg = AppConfig()
        assert can_resolve_credentials(cfg, "review") is False

    def test_fast_mode_only_needs_builder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        from cross_review.config import AppConfig

        cfg = AppConfig()
        assert can_resolve_credentials(cfg, "fast") is True

    def test_review_mode_needs_all_providers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        from cross_review.config import AppConfig

        cfg = AppConfig()
        assert can_resolve_credentials(cfg, "review") is False

    def test_oca_config_with_token_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OCA_TOKEN_ENV, "oca-tok")
        cfg = build_oca_config("oca-tok")
        assert can_resolve_credentials(cfg, "review") is True

    def test_oca_config_without_token_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(OCA_TOKEN_ENV, raising=False)
        cfg = build_oca_config("oca-tok")
        assert can_resolve_credentials(cfg, "review") is False
