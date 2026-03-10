"""Tests for auth mode resolution."""

import pytest

from cross_review.auth import host_managed_warning, resolve_auth_mode


class TestResolveAuthMode:
    """Tests for resolve_auth_mode()."""

    def test_explicit_provider_managed(self):
        """Explicit provider_managed should be returned as-is."""
        result = resolve_auth_mode(auth_mode="provider_managed", has_sampling=False)
        assert result == "provider_managed"

    def test_explicit_host_managed(self):
        """Explicit host_managed should be returned as-is."""
        result = resolve_auth_mode(auth_mode="host_managed", has_sampling=True)
        assert result == "host_managed"

    def test_explicit_host_managed_without_sampling_raises(self):
        """host_managed without sampling support should raise."""
        with pytest.raises(RuntimeError, match="MCP sampling"):
            resolve_auth_mode(auth_mode="host_managed", has_sampling=False)

    def test_auto_with_api_keys(self, monkeypatch):
        """auto mode with API keys should resolve to provider_managed."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        result = resolve_auth_mode(auth_mode="auto", has_sampling=False)
        assert result == "provider_managed"

    def test_auto_with_sampling_no_keys(self, monkeypatch):
        """auto mode with sampling but no keys should resolve to host_managed."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        result = resolve_auth_mode(auth_mode="auto", has_sampling=True)
        assert result == "host_managed"

    def test_auto_no_keys_no_sampling_raises(self, monkeypatch):
        """auto mode with no keys and no sampling should raise."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="No API keys"):
            resolve_auth_mode(auth_mode="auto", has_sampling=False)

    def test_auto_prefers_keys_over_sampling(self, monkeypatch):
        """When both keys and sampling available, prefer provider_managed."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        result = resolve_auth_mode(auth_mode="auto", has_sampling=True)
        assert result == "provider_managed"

    def test_auto_detects_custom_provider_key(self, monkeypatch):
        """Custom provider key env vars should participate in auto detection."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        result = resolve_auth_mode(
            auth_mode="auto",
            has_sampling=False,
            api_key_vars=("DEEPSEEK_API_KEY",),
        )
        assert result == "provider_managed"


class TestHostManagedWarning:
    """Tests for host-managed warning generation."""

    def test_warning_mentions_single_provider(self):
        """Warning should explain the limitation."""
        warning = host_managed_warning(("OPENAI_API_KEY", "GEMINI_API_KEY"))
        assert "single-provider" in warning.lower() or "Single-provider" in warning

    def test_warning_mentions_api_keys(self):
        """Warning should tell users how to upgrade."""
        warning = host_managed_warning(("OPENAI_API_KEY", "GEMINI_API_KEY"))
        assert "API_KEY" in warning

    def test_warning_handles_empty_key_list(self):
        """Warning should stay readable when no provider keys are configured."""
        warning = host_managed_warning(())
        assert "Single-provider review" in warning
