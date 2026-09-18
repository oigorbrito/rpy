# Offline release candidate

Este documento consolida o que está evidenciado para um release offline e não substitui validação no ambiente de destino.

## Caminho canônico

Com Git, Docker e Docker Compose instalados, execute da raiz:

```powershell
.\scripts\smoke_offline.ps1
```

```sh
./scripts/smoke_offline.sh
```

O smoke cria PostgreSQL 16/pgvector, aplica migrations, executa API ASGI,
fila/worker, webhook sintético, finalização, validação/persistência de summary
e leitura autenticada. Usa dados sintéticos e não exige chaves pagas.

## Definition of Done

- [x] clone novo validado em ambiente externo limpo com Git + Docker + Docker Compose;
- [x] pré-requisitos documentados;
- [x] project harness (incluindo guardrails de migration/release), unitários, integração PostgreSQL, image smoke e backup/restore verdes;
- [x] smoke offline termina com `RPY OFFLINE SMOKE: PASS` e `providers=0`;
- [x] invariantes de dados, concorrência/recovery e sigilo evidenciados;
- [x] caminho vetorial com mais de 40 movimentos evidenciado offline;
- [x] contratos de providers testados com fakes e sem rede;
- [x] frontend comportamental offline verde;
- [x] exemplos de configuração conferidos contra o runtime;
- [x] PRs necessários integrados e CI do head exato verde.

O fresh clone foi validado em Windows fora do ambiente de desenvolvimento anterior:
a imagem foi reconstruída, migrations 001-014 foram aplicadas e o smoke terminou com
`RPY OFFLINE SMOKE: PASS`, `summary=valid`, `jobs=complete` e `providers=0`.

A versão formal deste primeiro release offline é `0.1.0`, em linha com o valor já
declarado em `pyproject.toml`. A tag prevista é `v0.1.0`.

## Matriz de evidências

| Capacidade | Evidência | Offline | Estado |
|---|---|---:|---|
| Guardrails de projeto | `scripts/project_harness.py` (inclui migration/release harness) | sim | repository gate |
| Fila/recovery | testes PostgreSQL de queue/worker | sim | repository gate |
| Retrieval >40 | E2E PostgreSQL com embeddings fake/pgvector | sim | repository gate |
| Sigilo | testes E2E/local determinístico | sim | repository gate |
| Provider contracts | fakes e monkeypatches | sim | repository gate |
| Pipeline operacional | `scripts/smoke_offline.*` | sim | repository gate |
| Frontend | harness Node comportamental | sim | repository gate |
| Fresh clone externo | smoke em checkout limpo no Windows | sim | passed |
| Judit real | provider acceptance controlado | não | deferred |
| Anthropic/OpenAI reais | provider acceptance controlado | não | deferred |

## Provider Acceptance — POST-OFFLINE-RELEASE / NON-BLOCKING

Somente com credenciais e orçamento controlados: realizar uma aquisição Judit,
confirmar callback/finalização, gerar um summary Anthropic, opcionalmente testar
um cenário >40 com embedding OpenAI, verificar logs/custos e confirmar ausência
de duplicidade. Não executar isso em CI e não considerar pré-requisito do smoke
offline.

## Segurança e operação

Nunca commite ou cole chaves em issues, PRs ou logs. Use `.env.production.example`
apenas como template e um secret manager para produção. Consulte os runbooks
de [produção](../deployment/production.md) e [backup/restore](../deployment/backup-restore.md).
