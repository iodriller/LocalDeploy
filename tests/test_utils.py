"""Unit tests for localdeploy.utils — URL helpers and env parsing."""
from __future__ import annotations

import pytest

from localdeploy.utils import (
    BackendCallError,
    api_auth_headers,
    api_client_base_url,
    env_bool,
    env_float,
    env_int,
    get_backend_base_url,
    is_loopback_url,
)


def test_api_client_base_url_normalizes_bind_all_and_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "8123")
    monkeypatch.setenv("API_HOST", "0.0.0.0")
    assert api_client_base_url() == "http://127.0.0.1:8123"
    assert api_client_base_url("::", 9000) == "http://127.0.0.1:9000"
    assert api_client_base_url("::1", 9000) == "http://[::1]:9000"


def test_api_auth_headers_follow_optional_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_TOKEN", raising=False)
    assert api_auth_headers() == {}
    monkeypatch.setenv("API_TOKEN", " secret ")
    assert api_auth_headers() == {"X-API-Token": "secret"}


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
            get_backend_base_url({}, "cloud")

    def test_local_provider_backends_use_loopback_defaults(self) -> None:
        assert get_backend_base_url({}, "lmstudio") == "http://127.0.0.1:1234"
        assert get_backend_base_url({}, "vllm") == "http://127.0.0.1:8001"

    def test_rejects_non_local_override(self) -> None:
        with pytest.raises(BackendCallError):
            get_backend_base_url({"base_url": "http://10.0.0.5:11434"}, "ollama")

    def test_uses_profile_url_when_loopback(self) -> None:
        url = get_backend_base_url({"base_url": "http://127.0.0.1:11434/"}, "ollama")
        assert url == "http://127.0.0.1:11434"

    def test_defaults_use_ip_literal_not_localhost_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Resolving the "localhost" hostname can take seconds on some Windows
        # configurations (IPv6 attempted first, then falls back to IPv4),
        # while "127.0.0.1" is an IP literal with no resolution cost. Defaults
        # must use the literal so every request doesn't pay that tax.
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("LLAMACPP_BASE_URL", raising=False)
        assert get_backend_base_url({}, "ollama") == "http://127.0.0.1:11434"
        assert get_backend_base_url({}, "llamacpp") == "http://127.0.0.1:8080"


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
