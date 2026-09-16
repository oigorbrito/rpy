# Offline release candidate

Este documento consolida o que está evidenciado para um release offline e não substitui validação no ambiente de destino.

## Status

**Release offline: pronto para entrega.**

O head `cbb80cda19fc3488d44769937f1879b7a03fe438` (`docs: prepare offline release candidate (#95)`) passou o workflow `ci` no GitHub Actions em 2026-09-16. O gate inclui migrations, testes unitários, frontend comportamental, image smoke, backup/restore, integração PostgreSQL e smoke offline com chaves de providers vazias.

Judit, Anthropic e OpenAI reais permanecem deliberadamente fora do gate e não bloqueiam a entrega offline. A ativação desses providers é uma etapa posterior, dependente de credenciais válidas, orçamento e aprovação operacional.

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

- [x] checkout limpo e pré-requisitos documentados;
- [x] migration harness, unitários, integração PostgreSQL, image smoke e backup/restore verdes;
- [x] smoke offline termina com `RPY OFFLINE SMOKE: PASS` e `providers=0`;
- [x] invariantes de dados, concorrência/recovery e sigilo evidenciadas;
- [x] caminho vetorial com mais de 40 movimentos evidenciado offline;
- [x] contratos de providers testados com fakes e sem rede;
- [x] frontend comportamental offline verde;
- [x] exemplos de configuração conferidos contra o runtime;
- [x] PRs de hardening offline integrados e CI do head exato verde.

A versão permanece `0.1.0` até existir uma política explícita de versionamento;
nenhuma tag é criada por este documento.

## Matriz de evidências

| Capacidade | Evidência | Offline | Estado |
|---|---|---:|---|
| Migrations | `scripts/migration_harness.py` | sim | release gate |
| Fila/recovery | testes PostgreSQL de queue/worker e hardening #90 | sim | release gate |
| Retrieval >40 | `tests/integration/test_pipeline_vector_e2e.py` | sim | release gate |
| Sigilo | `tests/integration/test_pipeline_secrecy_e2e.py` | sim | release gate |
| Provider contracts | hardening offline #91, fakes e monkeypatches | sim | release gate |
| Pipeline operacional | `scripts/smoke_offline.*` | sim | release gate |
| Frontend | harness Node e fechamento E2E offline #94 | sim | release gate |
| CI provider-free | workflow `.github/workflows/ci.yml` | sim | verde no head assinado |
| Judit real | provider acceptance controlado | não | deferred |
| Anthropic/OpenAI reais | provider acceptance controlado | não | deferred |

## Evidência do head assinado

- commit: `cbb80cda19fc3488d44769937f1879b7a03fe438`;
- workflow `ci`: run `35050703870`, conclusão `success`;
- CodeQL do mesmo head: run `35050703530`, conclusão `success`;
- sequência de hardening integrada no `main`: #86 (vector E2E), #87 (sigilo), #89 (invariantes de dados), #90 (concorrência/recovery), #91 (provider contracts), #92 (single-command smoke), #93 (CI gate), #94 (frontend offline), #95 (release candidate docs).

## Provider Acceptance — POST-OFFLINE-RELEASE / NON-BLOCKING

Somente com credenciais e orçamento controlados: realizar uma aquisição Judit,
confirmar callback/finalização, gerar um summary Anthropic, opcionalmente testar
um cenário >40 com embedding OpenAI, verificar logs/custos e confirmar ausência
de duplicidade. Não executar isso em CI e não considerar pré-requisito do smoke
offline.

Critério mínimo quando essa etapa for autorizada:

1. usar credenciais novas/rotacionadas e nunca expô-las em chat, logs ou commits;
2. executar uma única aquisição Judit controlada;
3. validar callback, finalização e idempotência antes de nova consulta paga;
4. executar uma geração Anthropic em processo não sigiloso;
5. executar OpenAI embeddings somente se o cenário escolhido tiver mais de 40 movimentos;
6. registrar custo, latência e erros do provider sem registrar conteúdo sensível;
7. encerrar o teste sem ampliar carga até revisão dos resultados.

## Segurança e operação

Nunca commite ou cole chaves em issues, PRs ou logs. Use `.env.production.example`
apenas como template e um secret manager para produção. Consulte os runbooks
de [produção](../deployment/production.md) e [backup/restore](../deployment/backup-restore.md).
