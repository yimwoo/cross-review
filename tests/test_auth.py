"""Tests for auth mode resolution."""

import pytest

from cross_review.auth import resolve_auth_mode, HOST_MANAGED_WARNING


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


class TestHostManagedWarning:
    """Tests for the host-managed warning constant."""

    def test_warning_mentions_single_provider(self):
        """Warning should explain the limitation."""
        assert (
            "single-provider" in HOST_MANAGED_WARNING.lower()
            or "Single-provider" in HOST_MANAGED_WARNING
        )

    def test_warning_mentions_api_keys(self):
        """Warning should tell users how to upgrade."""
        assert "API_KEY" in HOST_MANAGED_WARNING
