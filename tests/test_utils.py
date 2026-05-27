"""Unit tests for localdeploy.utils — URL helpers and env parsing."""
from __future__ import annotations

import pytest

from localdeploy.utils import (
    BackendCallError,
    env_bool,
    env_float,
    env_int,
    get_backend_base_url,
    is_loopback_url,
)


class TestIsLoopbackUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:11434",
            "http://localhost:8080",
            "http://[::1]:8000",
            "https://127.0.0.1",
        ],
    )
    def test_accepts_loopback(self, url: str) -> None:
        assert is_loopback_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com",
            "http://10.0.0.5:11434",
            "http://192.168.1.10",
            "file:///etc/passwd",
            "ftp://localhost",
            "not a url",
            "",
        ],
    )
    def test_rejects_non_loopback(self, url: str) -> None:
        assert is_loopback_url(url) is False


class TestGetBackendBaseUrl:
    def test_rejects_unknown_backend(self) -> None:
        with pytest.raises(BackendCallError):
            get_backend_base_url({}, "vllm")

    def test_rejects_non_local_override(self) -> None:
        with pytest.raises(BackendCallError):
            get_backend_base_url({"base_url": "http://10.0.0.5:11434"}, "ollama")

    def test_uses_profile_url_when_loopback(self) -> None:
        url = get_backend_base_url({"base_url": "http://127.0.0.1:11434/"}, "ollama")
        assert url == "http://127.0.0.1:11434"


class TestEnvHelpers:
    def test_env_bool_parses_truthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for value in ("1", "true", "TRUE", "Yes", "y", "on"):
            monkeypatch.setenv("LOCALDEPLOY_TEST", value)
            assert env_bool("LOCALDEPLOY_TEST", False) is True

    def test_env_bool_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOCALDEPLOY_TEST", raising=False)
        assert env_bool("LOCALDEPLOY_TEST", True) is True
        assert env_bool("LOCALDEPLOY_TEST", False) is False

    def test_env_int_falls_back_on_garbage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCALDEPLOY_TEST", "not-a-number")
        assert env_int("LOCALDEPLOY_TEST", 42) == 42

    def test_env_float_falls_back_on_garbage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCALDEPLOY_TEST", "not-a-number")
        assert env_float("LOCALDEPLOY_TEST", 1.5) == 1.5
