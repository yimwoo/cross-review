"""Configuration loading for cross-review (design doc §7).

Precedence (highest to lowest):
    1. CLI flags  (applied by the caller after load_config)
    2. Environment variables  (CROSS_REVIEW_<SECTION>_<KEY>)
    3. Config file  (~/.config/cross-review/config.toml or explicit path)
    4. Built-in defaults
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class RouterConfig(BaseModel):
    """Router / mode-selection settings."""

    default_mode: str = "review"
    use_classifier: bool = False
    classifier_provider: str = "claude"
    classifier_model: str = "claude-haiku"


class BudgetDefaults(BaseModel):
    """Token / call / timeout budget defaults."""

    max_total_calls: int = 4
    max_reviewers: int = 2
    soft_token_limit: int = 20_000
    hard_token_limit: int = 30_000
    orchestration_timeout_seconds: int = 60


class RoleConfig(BaseModel):
    """Provider + model binding for a single role."""

    provider: str
    model: str


# ---------------------------------------------------------------------------
# Built-in role defaults
# ---------------------------------------------------------------------------

DEFAULT_ROLES: dict[str, RoleConfig] = {
    "builder": RoleConfig(provider="claude", model="claude-sonnet-4-5-20250514"),
    "skeptic_reviewer": RoleConfig(provider="openai", model="gpt-4.1"),
    "pragmatist_reviewer": RoleConfig(provider="gemini", model="gemini-2.5-pro"),
}


def _default_roles_factory() -> dict[str, RoleConfig]:
    """Return a deep copy of DEFAULT_ROLES so mutations are isolated."""
    return {k: v.model_copy() for k, v in DEFAULT_ROLES.items()}


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    """Top-level application configuration."""

    router: RouterConfig = RouterConfig()
    budget: BudgetDefaults = BudgetDefaults()
    roles: dict[str, RoleConfig] = None  # type: ignore[assignment]

    def model_post_init(self, __context: object) -> None:
        """Ensure roles gets a fresh copy of defaults when not provided."""
        if self.roles is None:
            self.roles = _default_roles_factory()


# ---------------------------------------------------------------------------
# Default config path
# ---------------------------------------------------------------------------


def _default_config_path() -> Path:
    """Return the platform default config file path."""
    return Path.home() / ".config" / "cross-review" / "config.toml"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_config_from_toml_string(toml_str: str) -> AppConfig:
    """Parse a TOML string and return an AppConfig merged with defaults.

    Keys present in the TOML override the corresponding defaults; missing
    keys retain their built-in default values.  Role entries in the TOML are
    merged with (not replacing) the default role map.
    """
    raw = tomllib.loads(toml_str) if toml_str.strip() else {}

    # --- router: partial override ---
    router_data = raw.get("router", {})
    router = RouterConfig(**router_data)

    # --- budget: partial override ---
    budget_data = raw.get("budget", {})
    budget = BudgetDefaults(**budget_data)

    # --- roles: merge with defaults ---
    roles = _default_roles_factory()
    for name, role_data in raw.get("roles", {}).items():
        roles[name] = RoleConfig(**role_data)

    return AppConfig(router=router, budget=budget, roles=roles)


def _apply_env_overrides(cfg: AppConfig) -> AppConfig:
    """Apply CROSS_REVIEW_<SECTION>_<KEY> environment variable overrides.

    Only a well-known set of keys is supported so that typos don't silently
    fail.  Env-var values are coerced to the target field type.
    """
    # Router overrides
    router_fields = cfg.router.model_fields
    router_updates: dict[str, object] = {}
    for field_name, field_info in router_fields.items():
        env_key = f"CROSS_REVIEW_ROUTER_{field_name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            annotation = field_info.annotation
            if annotation is bool:
                router_updates[field_name] = env_val.lower() in ("1", "true", "yes")
            elif annotation is int:
                router_updates[field_name] = int(env_val)
            else:
                router_updates[field_name] = env_val

    if router_updates:
        cfg = cfg.model_copy(update={"router": cfg.router.model_copy(update=router_updates)})

    # Budget overrides
    budget_fields = cfg.budget.model_fields
    budget_updates: dict[str, object] = {}
    for field_name, field_info in budget_fields.items():
        env_key = f"CROSS_REVIEW_BUDGET_{field_name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            annotation = field_info.annotation
            if annotation is int:
                budget_updates[field_name] = int(env_val)
            else:
                budget_updates[field_name] = env_val

    if budget_updates:
        cfg = cfg.model_copy(update={"budget": cfg.budget.model_copy(update=budget_updates)})

    return cfg


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load configuration with full precedence chain.

    1. CLI flags — applied by the caller after this function returns.
    2. Environment variables (``CROSS_REVIEW_*``).
    3. Config file (*config_path*, or ``~/.config/cross-review/config.toml``).
    4. Built-in defaults.
    """
    path = config_path if config_path is not None else _default_config_path()

    if path.is_file():
        toml_str = path.read_text(encoding="utf-8")
        cfg = load_config_from_toml_string(toml_str)
    else:
        cfg = AppConfig()

    cfg = _apply_env_overrides(cfg)
    return cfg
