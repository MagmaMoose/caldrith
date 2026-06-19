"""Tests for AppConfig env loading."""

from __future__ import annotations

import pytest

from caldrith.settings import AppConfig, get_config


def test_config_reads_env() -> None:
    cfg = get_config()
    assert cfg.app_id == "123456"
    assert cfg.webhook_secret == "test-secret"
    assert cfg.admin_repo == "admin"
    assert cfg.github_api_url == "https://api.github.com"


def test_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("GITHUB_API_URL", "ADMIN_REPO", "CONFIG_PATH", "SETTINGS_FILE_PATH"):
        monkeypatch.delenv(key, raising=False)
    get_config.cache_clear()
    cfg = AppConfig()  # type: ignore[call-arg]
    assert cfg.github_api_url == "https://api.github.com"
    assert cfg.admin_repo == "admin"
    assert cfg.config_path == ".github"
    assert cfg.settings_file_path == "settings.yml"


def test_escaped_newlines_in_private_key_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRIVATE_KEY", "-----BEGIN KEY-----\\nABC\\n-----END KEY-----")
    get_config.cache_clear()
    cfg = AppConfig()  # type: ignore[call-arg]
    assert "\n" in cfg.private_key
    assert "\\n" not in cfg.private_key
