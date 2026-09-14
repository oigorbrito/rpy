from pathlib import Path
from fastapi.testclient import TestClient
from app.api import app
FRONTEND=Path(__file__).parents[1]/"app"/"frontend"
def test_frontend_routes_are_served_without_database_lifespan()->None:
 client=TestClient(app); index=client.get("/"); assert index.status_code==200; assert "Rpy" in index.text; assert "Número do processo" in index.text; assert "Bearer token" in index.text; assert "Ir para o conteúdo" in index.text; assert 'aria-live="polite"' in index.text; assert 'aria-describedby="process-code-hint process-code-error"' in index.text; assert "Contexto do processo" in index.text; assert "Últimos acontecimentos" in index.text
 css=client.get("/app.css"); assert css.status_code==200; assert css.headers["content-type"].startswith("text/css")
 js=client.get("/app.js"); assert js.status_code==200; assert js.headers["content-type"].startswith("text/javascript")
def test_frontend_does_not_persist_bearer_token_or_render_model_html()->None:
 source=(FRONTEND/"app.js").read_text(); assert "localStorage" not in source; assert "sessionStorage" not in source; assert "innerHTML" not in source; assert "textContent" in source; assert "Authorization" in source
def test_frontend_has_explicit_accessibility_and_state_contracts()->None:
 javascript=(FRONTEND/"app.js").read_text(); stylesheet=(FRONTEND/"app.css").read_text(); assert "aria-invalid" in javascript; assert "aria-busy" in javascript; assert "NOT_FOUND" in javascript; assert "Sem conexão com o serviço" in javascript; assert "navigator.clipboard.writeText" in javascript; assert "prefers-reduced-motion" in javascript; assert ":focus-visible" in stylesheet; assert "prefers-reduced-motion" in stylesheet; assert ".skip-link" in stylesheet
def test_frontend_renders_detail_data_as_text_nodes()->None:
 source=(FRONTEND/"app.js").read_text(); assert "renderParties" in source; assert "renderSubjects" in source; assert "renderHeader" in source; assert "renderSteps" in source; assert "recent_steps" in source; assert "createElement('time')" in source
