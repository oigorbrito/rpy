from pathlib import Path
from fastapi.testclient import TestClient
from app.api import app
FRONTEND=Path(__file__).parents[1]/"app"/"frontend"
def test_frontend_routes_are_served_without_database_lifespan()->None:
 client=TestClient(app); index=client.get("/"); assert index.status_code==200; assert "Rpy" in index.text; assert "Número do processo" in index.text; assert "Bearer token" in index.text; assert "Ir para o conteúdo" in index.text; assert 'aria-live="polite"' in index.text; assert "Contexto do processo" in index.text; assert "Últimos acontecimentos" in index.text; assert client.get("/app.css").status_code==200; assert client.get("/app.js").status_code==200
def test_frontend_does_not_persist_bearer_token_or_render_model_html()->None:
 source=(FRONTEND/"app.js").read_text(); assert "localStorage" not in source; assert "sessionStorage" not in source; assert "innerHTML" not in source; assert "textContent" in source; assert "Authorization" in source
def test_frontend_has_explicit_accessibility_and_state_contracts()->None:
 javascript=(FRONTEND/"app.js").read_text(); stylesheet=(FRONTEND/"app.css").read_text(); html=(FRONTEND/"index.html").read_text(); assert "aria-invalid" in javascript; assert "aria-busy" in javascript; assert "Sem conexão com o serviço" in javascript; assert "navigator.clipboard.writeText" in javascript; assert "prefers-reduced-motion" in javascript; assert ":focus-visible" in stylesheet; assert "prefers-reduced-motion" in stylesheet; assert 'id="action-feedback"' in html; assert 'role="status"' in html; assert "Resumo copiado para a área de transferência." in javascript
def test_frontend_renders_detail_data_as_text_nodes()->None:
 source=(FRONTEND/"app.js").read_text(); assert "renderParties" in source; assert "renderSubjects" in source; assert "renderHeader" in source; assert "renderSteps" in source; assert "recent_steps" in source; assert "createElement('time')" in source
def test_frontend_formats_cnj_money_instance_and_party_role_for_legal_reading()->None:
 source=(FRONTEND/"app.js").read_text(); html=(FRONTEND/"index.html").read_text(); assert "formatCnjInput" in source; assert "formatCnj(data.code)" in source; assert "Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'})" in source; assert "Polo ativo" in source; assert "Polo passivo" in source; assert "ª instância" in source; assert "formatação CNJ é aplicada automaticamente" in html
def test_frontend_maps_public_summary_states_without_job_details()->None:
 source=(FRONTEND/"app.js").read_text(); assert "summary_status" in source; assert "preparação em andamento" in source; assert "resumo indisponível" in source; assert "sem resumo publicado" in source; assert "error_log" not in source; assert "max_attempts" not in source
def test_frontend_requests_missing_process_only_after_explicit_action()->None:
 html=(FRONTEND/"index.html").read_text(); source=(FRONTEND/"app.js").read_text(); assert 'id="request-process"' in html; assert "Solicitar consulta" in html; assert "pendingRequest={code,token}" in source; assert "Processo ainda não disponível" in source; assert "requestButton.addEventListener('click'" in source; assert "/request" in source; assert "method:'POST'" in source
def test_frontend_explains_long_running_request_and_polls_boundedly()->None:
 source=(FRONTEND/"app.js").read_text(); assert "MAX_POLL_ATTEMPTS=20" in source; assert "POLL_INTERVAL_MS=3000" in source; assert "setTimeout" in source; assert "setInterval" not in source; assert "pagehide" in source; assert "não é necessário manter esta página aberta" in source; assert "consultar este CNJ novamente mais tarde" in source
def test_frontend_never_exposes_provider_request_identifier()->None:
 source=(FRONTEND/"app.js").read_text(); assert "request_id" not in source; assert "judit_request_id" not in source
def test_frontend_follows_processing_summary_without_blocking_process_details()->None:
 source=(FRONTEND/"app.js").read_text(); assert "MAX_SUMMARY_POLL_ATTEMPTS=20" in source; assert "SUMMARY_POLL_INTERVAL_MS=3000" in source; assert "scheduleSummaryCheck" in source; assert "checkSummary" in source; assert "Os dados processuais já estão disponíveis" in source; assert "O resumo desta versão foi atualizado automaticamente" in source; assert "data.summary_status==='processing'" in source
def test_summary_followup_stops_on_terminal_state_and_keeps_manual_retry()->None:
 source=(FRONTEND/"app.js").read_text(); assert "data.summary_status!=='processing'" in source; assert "O resumo continua em preparação" in source; assert "retry:true" in source; assert "stopPolling()" in source
def test_process_document_has_internal_navigation_and_source_ai_distinction()->None:
 html=(FRONTEND/"index.html").read_text(); css=(FRONTEND/"app.css").read_text(); assert 'aria-label="Seções do processo"' in html; assert 'href="#summary-title"' in html; assert 'href="#case-data-title"' in html; assert 'href="#movements-title"' in html; assert "SÍNTESE GERADA POR IA" in html; assert "DADOS DA FONTE PROCESSUAL" in html; assert ".document-nav" in css; assert "position:sticky" in css
