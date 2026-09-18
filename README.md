# Rpy

Rpy é um serviço de RAG para consulta e resumo processual em Python/FastAPI, PostgreSQL 16 e pgvector. O foco do `v0.1.0` é um caminho operacional pequeno, auditável e reproduzível, com fila PostgreSQL, multitenancy, sigilo provider-free e validação offline sem credenciais pagas.

## Estado do projeto

O caminho offline está qualificado por CI e por fresh clone real em Windows. A validação canônica reconstrói a imagem, aplica as migrations, executa API/fila/worker com dados sintéticos e termina com:

```text
RPY OFFLINE SMOKE: PASS
process=0000000-00.2026.8.21.0001
summary=valid
jobs=complete
providers=0
```

Judit, Anthropic, OpenAI e Cohere reais são uma etapa posterior de **Provider Acceptance**, controlada por ambiente e não bloqueante para a qualificação offline. BGE real também exige artifact/benchmark próprios antes de ativação. Consulte `docs/release/provider-acceptance.md`, `docs/release/offline-release-candidate.md` e `docs/release/v0.1.0.md`.

## Princípios de engenharia

- PostgreSQL é a fonte de verdade e também a fila de jobs.
- Claims concorrentes usam `FOR UPDATE SKIP LOCKED` com ownership por `worker_id`.
- Retries e callbacks externos são idempotentes em fronteiras duráveis.
- Processos sigilosos não chamam LLM nem embeddings externos.
- Migrations, recuperação, retenção e isolamento entre tenants são tratados como invariantes, não como detalhes de implementação.
- Mudanças devem ser pequenas, reversíveis e sustentadas por testes/CI; o contrato completo para agentes e contribuições está em `AGENTS.md` e `CONTRIBUTING.md`.

## Arquitetura

- **FastAPI + frontend server-served**: consulta tenant-scoped, solicitação de CNJ e webhooks Judit.
- **PostgreSQL 16 + pgvector**: dados processuais, embeddings, auditoria e fila.
- **2+ workers**: claim atômico, heartbeat, retry, fencing e reclaim.
- **1 scheduler**: expurgo periódico protegido por advisory lock.
- **Claude Sonnet 5**: geração de resumo não sigiloso, prompt caching e validação pós-geração.
- **Embeddings condicionais**: processos com mais de 40 movimentos usam retrieval lexical + vetorial.

Fluxo simplificado:

```text
Browser/API
    |
    v
FastAPI -----> Judit (opcional, provider real)
    |
    v
PostgreSQL/pgvector <---- workers
    |                    |
    |                    +---- Anthropic/OpenAI (somente quando permitido)
    |
    +---- scheduler/reclaimer
```

## Quickstart offline

Pré-requisitos:

- Git;
- Docker;
- Docker Compose;
- Docker daemon em execução.

O host não precisa de Python, pytest nem chaves de providers para o smoke offline.

### Windows

```powershell
git clone https://github.com/oigorbrito/rpy.git
cd rpy
.\scripts\smoke_offline.ps1
```

### Linux/macOS

```bash
git clone https://github.com/oigorbrito/rpy.git
cd rpy
./scripts/smoke_offline.sh
```

O smoke usa projeto Compose, rede e volume descartáveis próprios. Não use comandos globais destrutivos de limpeza do Docker para executar ou encerrar esse fluxo.

## Desenvolvimento local

Para trabalhar fora do container, use Python 3.12 e instale as dependências sob o arquivo de constraints:

```bash
python -m pip install pip==26.2.1
python -m pip install --constraint requirements/constraints.txt -e '.[dev]'
```

Checks rápidos:

```bash
python scripts/project_harness.py
pytest -q tests --ignore=tests/integration
node tests/frontend_behavior_test.mjs
```

Integração PostgreSQL:

```bash
pytest -q tests/integration
```

Antes de abrir PR, leia `CONTRIBUTING.md`. Mudanças arquiteturais, de migration, fila, retrieval, provider, sigilo ou deployment também devem respeitar `AGENTS.md`.

## Modo com providers reais — opcional

Use apenas ambiente controlado, credenciais rotacionáveis e orçamento explícito. Nunca use chaves reais no CI ou em exemplos commitados.

Exemplo de variáveis esperadas:

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

A stack local sobe PostgreSQL/pgvector em `localhost:5432`, migration job, API/frontend em `localhost:8000`, dois workers e exatamente um scheduler.

Health/readiness:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Abra `http://localhost:8000/` para usar a interface.

## Fluxo do produto

A interface web é servida pela própria API em `/`. O usuário informa um bearer token provisionado para seu tenant e um número CNJ. O fluxo suportado é:

1. consultar um processo já autorizado;
2. se o CNJ ainda não estiver disponível, solicitar aquisição à Judit;
3. acompanhar a chegada dos dados processuais;
4. ler partes, assuntos, contexto e movimentações quando a versão estiver disponível;
5. acompanhar a geração enquanto `summary_status=processing`;
6. ler/copiar o resumo validado quando publicado.

O browser não recebe credenciais da Judit nem identificadores internos de request/job. O bearer token permanece somente em memória da página e não é salvo em `localStorage`/`sessionStorage`.

## Contrato HTTP principal

- `GET /processes/{cnj}`: lê apenas processo autorizado ao tenant e retorna `404` fora do escopo.
- `POST /processes/{cnj}/request`: solicita aquisição de CNJ ausente, com idempotência por tenant/CNJ; retorna somente estado público.
- `POST /webhooks/judit/{token}`: recebe callbacks assíncronos da Judit.
- `GET /health`: liveness.
- `GET /ready`: readiness com PostgreSQL.
- `GET /ops/metrics`: métricas protegidas por credencial operacional separada.

O estado público do resumo é `available`, `processing`, `not_generated` ou `unavailable`. Detalhes internos de fila, tentativas, worker, provider e erros não fazem parte do contrato público.

## Webhook Judit

- token inválido retorna `404`;
- `callback_id` é persistido para idempotência;
- `response_created` do tipo `lawsuit` é staged e concede acesso somente aos tenants correlacionados à solicitação;
- `request_completed` enfileira promoção no worker;
- resposta fresca (`cached_response=false`) vence a cacheada;
- resposta apenas cacheada pode ser promovida, mas não dispara LLM;
- o handler HTTP não executa geração nem promoção pesada.

## Retrieval

- até 40 movimentos: todos entram no contexto, sem embeddings;
- acima de 40: BM25 real + pgvector com pesos `0.5 / 0.5`;
- boost de recência;
- primeiro, último e cinco movimentos mais recentes são force-included;
- milestones judiciais relevantes são force-included independentemente do score.

## Segurança e LGPD

- autorização de portfólio via `tenant_processes`;
- bearer token resolve tenant antes da leitura ou solicitação;
- solicitação Judit é correlacionada de forma durável ao tenant antes de callbacks concederem acesso;
- processos sob sigilo usam caminho determinístico local e não enviam conteúdo para embeddings/LLM externos;
- `access_log` é imutável;
- expurgo remove versões, JSONB, movimentos, vetores e callbacks brutos da Judit, preservando histórico de acesso.

Não publique vulnerabilidades, credenciais ou dados processuais sensíveis em issues. Consulte `SECURITY.md`.

## CI e evidência de release

O GitHub Actions executa:

- validação do contrato de Compose de produção;
- project harness (que agrega os guardrails de migration/release e verifica o wiring das demais evidências);
- testes unitários;
- harness comportamental do frontend;
- smoke da imagem;
- drill de backup/restore;
- integração PostgreSQL;
- smoke offline provider-free.

A suíte E2E cobre CNJ ausente → solicitação → callback → acesso tenant-scoped → finalização → resumo validado, além de callbacks fora de ordem/retry, resposta cached sem LLM, sigilo provider-free e retrieval vetorial com mais de 40 movimentos.

## Operação e deployment

Produção usa `compose.production.yaml`, imagem imutável por digest e credenciais PostgreSQL separadas por responsabilidade.

Documentação operacional:

- `docs/deployment/local-offline.md` — validação local provider-free;
- `docs/deployment/production.md` — topologia e deployment;
- `docs/deployment/backup-restore.md` — backup e restore drill;
- `docs/release/offline-release-candidate.md` — Definition of Done/evidências;
- `docs/release/provider-acceptance.md` — aceitação controlada de providers/artifacts reais;
- `docs/release/v0.1.0.md` — release notes;
- `docs/engineering/empirical-engineering.md` — política de evidência técnica;
- `docs/engineering/project-harness.md` — contrato executável do harness do projeto.

## Estrutura do repositório

```text
app/           aplicação FastAPI, fila, retrieval, RAG e frontend
sql/           migrations PostgreSQL
scripts/       harnesses, validações e operações
tests/         unitários, frontend e integração PostgreSQL
docs/          engenharia, deployment e release
requirements/  constraints reprodutíveis
```

## Limites atuais do MVP

- autenticação por bearer token provisionado; sem login/autocadastro;
- sem dashboard, favoritos, alertas ou gestão de carteira;
- polling do frontend é limitado;
- providers reais exigem infraestrutura, credenciais válidas e aceitação operacional separada;
- o projeto evita deliberadamente Redis/Celery, vector DB externo e frameworks RAG pesados enquanto o stack atual for suficiente.

## Contribuição e segurança

- engenharia e PRs: `CONTRIBUTING.md`;
- contrato para agentes/coding assistants: `AGENTS.md`;
- reporte responsável de vulnerabilidades: `SECURITY.md`.

## Licença e atribuições

O Rpy é distribuído sob a licença MIT. Consulte `LICENSE` para os termos do projeto e `NOTICE` para provenance e atribuições de componentes/implementações de terceiros.
