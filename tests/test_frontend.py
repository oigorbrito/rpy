from pathlib import Path

from fastapi.testclient import TestClient

from app.api import app


def test_frontend_routes_are_served_without_database_lifespan() -> None:
    client = TestClient(app)

    index = client.get("/")
    assert index.status_code == 200
    assert "Rpy" in index.text
    assert "Número do processo" in index.text
    assert "Bearer token" in index.text

    css = client.get("/app.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")

    js = client.get("/app.js")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("text/javascript")


def test_frontend_does_not_persist_bearer_token_or_render_model_html() -> None:
    source = (Path(__file__).parents[1] / "app" / "frontend" / "app.js").read_text()

    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "innerHTML" not in source
    assert "textContent" in source
    assert "Authorization" in source
