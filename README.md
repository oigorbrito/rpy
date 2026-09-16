# Rpy

MVP de RAG para consulta e resumo processual em Python/FastAPI, PostgreSQL e pgvector.

## Fluxo do produto

A interface web é servida pela própria API em `/`. O usuário informa um bearer token provisionado para seu tenant e um número CNJ. O fluxo suportado é:

1. consultar um processo já autorizado;
2. se o CNJ ainda não estiver disponível, solicitar a aquisição à Judit;
3. acompanhar de forma limitada a chegada dos dados processuais;
4. ler partes, assuntos, contexto e movimentações assim que a versão estiver disponível;
5. acompanhar a geração do resumo enquanto `summary_status=processing`;
6. ler e copiar o resumo validado quando publicado.

O browser não recebe credenciais da Judit nem identificadores internos de request/job. O bearer token permanece apenas em memória da página; não é salvo em `localStorage` ou `sessionStorage`.

## Arquitetura

- **API FastAPI + frontend server-served**: consulta tenant-scoped, solicitação de CNJ e webhooks Judit.
- **PostgreSQL 16 + pgvector**: persistência, vetores e fila.
- **2+ workers**: claim atômico com `FOR UPDATE SKIP LOCKED`, heartbeat, retry e reclaim.
- **1 scheduler**: expurgo periódico protegido por advisory lock.
- **Claude Sonnet 5**: geração do resumo com prompt caching e validação pós-geração.
- **Embeddings condicionais**: somente processos com mais de 40 movimentos usam busca vetorial.

## Subir localmente

### Validação offline (caminho recomendado)

Pré-requisitos: Git, Docker e Docker Compose. O host não precisa de Python,
pytest ou credenciais de providers.

No Windows:

```powershell
.\scripts\smoke_offline.ps1
```

No Unix:

```bash
./scripts/smoke_offline.sh
```

O resultado esperado é `RPY OFFLINE SMOKE: PASS`, seguido de `summary=valid`,
`jobs=complete` e `providers=0`. O smoke usa um projeto Compose, rede e volume
descartáveis próprios. Para parar uma stack local criada manualmente, use
`docker compose down`; não use limpeza global do Docker.

### Modo com providers reais (opcional)

Para exercer o fluxo completo com Judit e geração externa, configure as chaves
em um ambiente controlado:

```bash
export ANTHROPIC_API_KEY='change-me'
export OPENAI_API_KEY='change-me'
export JUDIT_API_KEY='change-me'
export JUDIT_WEBHOOK_TOKEN='change-me'
export RPY_BEARER_TOKENS='{"example-only":"00000000-0000-0000-0000-000000000000"}'
```

Então:

```bash
docker compose up --build
```

O stack local sobe PostgreSQL/pgvector em `localhost:5432`, migration job, API/frontend em `localhost:8000`, dois workers e exatamente um scheduler.

Health check:

```bash
curl http://localhost:8000/health
```

Abra `http://localhost:8000/` para usar a interface.

## Contrato HTTP principal

- `GET /processes/{cnj}`: lê apenas processo autorizado ao tenant e retorna `404` fora do escopo.
- `POST /processes/{cnj}/request`: solicita aquisição de CNJ ausente, com idempotência por tenant/CNJ; retorna apenas estado público.
- `POST /webhooks/judit/{token}`: recebe callbacks assíncronos da Judit.
- `GET /health`: liveness.
- `GET /ready`: readiness com PostgreSQL.
- `GET /ops/metrics`: métricas protegidas por credencial operacional separada.

O estado público do resumo é `available`, `processing`, `not_generated` ou `unavailable`. Detalhes internos de fila, tentativas, worker, provider e erros não fazem parte do contrato público.

## Webhook Judit

Regras principais:

- token inválido retorna `404`;
- `callback_id` é persistido para idempotência;
- `response_created` do tipo `lawsuit` é apenas staged e concede acesso aos tenants associados à solicitação correspondente;
- `request_completed` enfileira a promoção no worker;
- quando existe resposta fresca (`cached_response=false`), ela vence a cacheada;
- resposta apenas cacheada pode ser promovida, mas não dispara LLM;
- o handler HTTP não executa geração nem promoção pesada.

## Retrieval

- até 40 movimentos: todos os movimentos entram no contexto, sem embeddings;
- acima de 40: BM25 real + pgvector em peso `0.5 / 0.5`;
- boost de recência;
- primeiro, último e cinco movimentos mais recentes são forçados;
- milestones judiciais são forçados independentemente do score.

## Segurança / LGPD

- portfólio autorizado via `tenant_processes`;
- bearer token resolve tenant antes da leitura ou solicitação;
- solicitação Judit é correlacionada de forma durável ao tenant antes de o callback conceder acesso;
- processos sob sigilo não enviam conteúdo para embeddings/LLM externos;
- `access_log` é imutável;
- expurgo remove versões, JSONB, movimentos, vetores e callbacks brutos da Judit, preservando o registro histórico de acesso.

## Testes e evidência de release

```bash
pip install -e '.[dev]'
python scripts/migration_harness.py
pytest -q tests --ignore=tests/integration
```

O GitHub Actions executa também PostgreSQL 16 + pgvector, contrato de compose de produção, smoke da imagem, drill de backup/restore, harness comportamental do frontend e o smoke offline provider-free. A suíte E2E cobre o caminho de produto CNJ ausente → solicitação → callback → acesso tenant-scoped → finalização → resumo validado publicado, além de callback fora de ordem/retry, resposta cached sem LLM, sigilo provider-free e retrieval vetorial com mais de 40 movimentos.

A produção usa `compose.production.yaml`, imagem imutável por digest e credenciais PostgreSQL separadas por responsabilidade. Consulte `docs/deployment/production.md` e `docs/deployment/backup-restore.md` antes de publicar.

## Limites atuais do MVP

- autenticação é por bearer token provisionado; não existe login/autocadastro no produto;
- não há dashboard, favoritos, alertas ou gestão de carteira;
- o frontend não faz polling ilimitado: após a janela automática, o usuário pode repetir a consulta manualmente;
- publicação real exige infraestrutura externa e credenciais válidas para registry, Judit e providers de IA.

## Contrato para agentes

Leia `AGENTS.md` antes de transplantar código de qualquer donor. O objetivo é reaproveitar as peças caras sem importar produto, UI, abstrações ou dependências desnecessárias.
