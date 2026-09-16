from types import SimpleNamespace

from app.api_key_middleware import ApiKeySecurityMiddleware


def _request(method: str, path: str):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def test_api_key_middleware_covers_process_and_source_routes() -> None:
    assert ApiKeySecurityMiddleware._process_route(
        _request("GET", "/processes/0000000-00.2026.8.21.0001")
    ) == ("0000000-00.2026.8.21.0001", "api_key_read_process")
    assert ApiKeySecurityMiddleware._process_route(
        _request("GET", "/v1/processos/0000000-00.2026.8.21.0001/fontes")
    ) == ("0000000-00.2026.8.21.0001", "api_key_read_process_sources")


def test_api_key_middleware_does_not_match_unprotected_paths() -> None:
    assert ApiKeySecurityMiddleware._process_route(_request("GET", "/health")) is None
