# Rpy

MVP de RAG para resumo processual em Python/FastAPI, PostgreSQL e pgvector.

## Arquitetura

- **API FastAPI**: recebe webhooks da Judit e expõe leitura tenant-scoped.
- **PostgreSQL 16 + pgvector**: persistência, vetores e fila.
- **2+ workers**: claim atômico com `FOR UPDATE SKIP LOCKED`, heartbeat, retry e reclaim.
- **1 scheduler**: expurgo periódico protegido por advisory lock.
- **Claude Sonnet 5**: geração do resumo com prompt caching e validação pós-geração.
- **Embeddings condicionais**: somente processos com mais de 40 movimentos usam busca vetorial.

## Subir localmente

Pré-requisito: Docker com Compose.

Defina, quando necessário:

```bash
export ANTHROPIC_API_KEY='...'
export OPENAI_API_KEY='...'
export JUDIT_WEBHOOK_TOKEN='...'
export RPY_BEARER_TOKENS='{"seu-token":"00000000-0000-0000-0000-000000000000"}'
```

Então:

```bash
docker compose up --build
```

O stack local sobe:

- PostgreSQL/pgvector em `localhost:5432`;
- migration job de execução única;
- API em `localhost:8000`;
- `worker-1`;
- `worker-2`;
- exatamente um `scheduler`.

Health check:

```bash
curl http://localhost:8000/health
```

## Webhook Judit

Endpoint:

```text
POST /webhooks/judit/{token}
```

Regras principais:

- token inválido retorna `404`;
- `callback_id` é persistido para idempotência;
- `response_created` do tipo `lawsuit` é apenas staged;
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
- bearer token resolve tenant antes da leitura;
- processos sob sigilo são truncados antes de embeddings/LLM;
- `access_log` é imutável;
- expurgo remove versões, JSONB, movimentos, vetores e callbacks brutos da Judit, preservando o registro histórico de acesso.

## Testes e migration harness

```bash
pip install -e '.[dev]'
python scripts/migration_harness.py
pytest -q tests --ignore=tests/integration
```

Os testes de integração PostgreSQL são executados automaticamente no GitHub Actions com PostgreSQL 16 + pgvector.

## Contrato para agentes

Leia `AGENTS.md` antes de transplantar código de qualquer donor. O objetivo é reaproveitar as peças caras sem importar produto, UI, abstrações ou dependências desnecessárias.
