import pytest
import requests

from apps.exams.codes import ErrorCode
from apps.exams.extraction.errors import ExtractionError
from apps.exams.extraction.llm import OllamaClient, assert_local_inference


@pytest.fixture(autouse=True)
def _no_remote_override(monkeypatch):
    monkeypatch.delenv("AI_ALLOW_REMOTE_INFERENCE", raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
        "http://ollama:11434",
    ],
)
def test_local_hosts_are_accepted(url):
    assert_local_inference(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost.evil.com",
        "http://127.0.0.1@evil.com",
        "http://evil.com#@localhost",
        "https://example.com",
    ],
)
def test_non_local_hosts_are_rejected(url):
    with pytest.raises(ExtractionError) as info:
        assert_local_inference(url)
    assert info.value.code == ErrorCode.INFERENCE_NOT_LOCAL


def test_remote_override_allows_remote_host(monkeypatch):
    monkeypatch.setenv("AI_ALLOW_REMOTE_INFERENCE", "true")
    assert_local_inference("https://example.com")
    OllamaClient(url="https://example.com")


def test_client_refuses_remote_url_without_override():
    with pytest.raises(ExtractionError):
        OllamaClient(url="http://evil.com:11434")


def test_session_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.evil.com:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.evil.com:3128")
    assert OllamaClient()._session.trust_env is False


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = ""

    def json(self):
        return {}

    def iter_lines(self, chunk_size=None):
        return iter(())

    def close(self):
        pass


def test_redirect_is_inference_unavailable_without_following(monkeypatch):
    client = OllamaClient()
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return _Response(307)

    monkeypatch.setattr(client._session, "post", fake_post)
    with pytest.raises(ExtractionError) as info:
        client.transcribe_page(b"png")
    assert info.value.code == ErrorCode.INFERENCE_UNAVAILABLE
    assert info.value.params == {}
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["stream"] is True


def test_connection_error_has_no_url_in_params(monkeypatch):
    client = OllamaClient(url="http://user:segredo@localhost:11434")

    def boom(url, **kwargs):
        raise requests.ConnectionError("recusada")

    monkeypatch.setattr(client._session, "post", boom)
    with pytest.raises(ExtractionError) as info:
        client.extract_header("texto")
    assert info.value.code == ErrorCode.INFERENCE_UNAVAILABLE
    assert info.value.params == {}


def test_server_error_has_no_params(monkeypatch):
    client = OllamaClient()
    monkeypatch.setattr(client._session, "post", lambda url, **kw: _Response(500))
    with pytest.raises(ExtractionError) as info:
        client.extract_header("texto")
    assert info.value.code == ErrorCode.INFERENCE_UNAVAILABLE
    assert info.value.params == {}
