"""Tests for provider API key validation."""

from pathlib import Path

import pytest

from cross_review.config import AppConfig, ProviderEntry, RoleConfig, resolve_model
from cross_review.providers.base import check_api_key, create_provider, resolve_api_key


class TestCheckApiKey:
    """Tests for API key validation."""

    def test_missing_anthropic_key(self, monkeypatch):
        """Should raise RuntimeError with clear message for missing Anthropic key."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            check_api_key("claude")

    def test_missing_openai_key(self, monkeypatch):
        """Should raise RuntimeError with clear message for missing OpenAI key."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            check_api_key("openai")

    def test_missing_gemini_key(self, monkeypatch):
        """Should raise RuntimeError with clear message for missing Gemini key."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            check_api_key("gemini")

    def test_empty_key_treated_as_missing(self, monkeypatch):
        """Empty string should be treated as missing."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            check_api_key("claude")

    def test_valid_key_passes(self, monkeypatch):
        """Should not raise when key is set."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        check_api_key("claude")  # should not raise

    def test_unknown_provider_passes(self):
        """Unknown providers should pass without error."""
        check_api_key("unknown_provider")  # should not raise

    def test_error_message_includes_export_hint(self, monkeypatch):
        """Error message should include the export command."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="export OPENAI_API_KEY="):
            check_api_key("openai")

    def test_resolve_api_key_reads_file_when_env_missing(
        self, monkeypatch, tmp_path: Path
    ):
        """Provider tokens can be loaded from a configured file."""
        token_file = tmp_path / "oca-token"
        token_file.write_text("file-token\n", encoding="utf-8")
        monkeypatch.delenv("OCA_TOKEN", raising=False)

        providers = {
            "oca": ProviderEntry(
                type="openai_compatible",
                base_url="https://oca.example.com/v1",
                api_key_env="OCA_TOKEN",
                api_key_file=str(token_file),
                default_model="oca/gpt-5.4",
            )
        }

        assert resolve_api_key("oca", providers) == "file-token"


class TestCreateProviderValidation:
    """Tests that create_provider checks API key before instantiation."""

    def test_create_provider_rejects_missing_key(self, monkeypatch):
        """create_provider should fail early with clear message."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            create_provider("claude", "claude-sonnet-4-20250514")

    def test_create_provider_succeeds_with_key(self, monkeypatch):
        """create_provider should succeed when key is set."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        adapter = create_provider("claude", "claude-sonnet-4-20250514")
        assert adapter.name() == "claude"

    def test_create_provider_supports_custom_registry_entry(self):
        """Custom providers should resolve through the supplied registry."""
        cfg = AppConfig()
        cfg.providers["custom"] = ProviderEntry(
            type="openai_compatible",
            base_url="http://localhost:11434/v1",
            default_model="llama3.2",
        )
        adapter = create_provider("custom", None, cfg.providers)
        assert adapter.name() == "custom"

    def test_create_provider_uses_registry_default_model(self):
        """Provider creation should use provider.default_model when role model is omitted."""
        cfg = AppConfig()
        adapter = create_provider("ollama", None, cfg.providers)
        assert adapter.name() == "ollama"

    def test_unknown_provider_lists_available_names(self):
        """Unknown providers should raise a helpful error."""
        with pytest.raises(ValueError, match="Supported:"):
            create_provider("does-not-exist", None, AppConfig().providers)


class TestResolveModel:
    """Tests for provider model resolution helpers."""

    def test_resolve_model_uses_provider_default_when_role_model_missing(self):
        role = RoleConfig(provider="ollama", model=None)
        provider = ProviderEntry(type="openai_compatible", default_model="llama3.2")
        assert resolve_model("builder", role, provider) == "llama3.2"
