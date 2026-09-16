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

- [ ] clone novo e pré-requisitos documentados;
- [ ] migration harness, unitários, integração PostgreSQL, image smoke e backup/restore verdes;
- [ ] smoke offline termina com `RPY OFFLINE SMOKE: PASS` e `providers=0`;
- [ ] invariantes de dados, concorrência/recovery e sigilo evidenciadas;
- [ ] caminho vetorial com mais de 40 movimentos evidenciado offline;
- [ ] contratos de providers testados com fakes e sem rede;
- [ ] frontend comportamental offline verde;
- [ ] exemplos de configuração conferidos contra o runtime;
- [ ] PRs necessários integrados e CI do head exato verde.

A versão permanece `0.1.0` até existir uma política explícita de versionamento;
nenhuma tag é criada por este documento.

## Matriz de evidências

| Capacidade | Evidência | Offline | Estado |
|---|---|---:|---|
| Migrations | `scripts/migration_harness.py` | sim | repository gate |
| Fila/recovery | testes PostgreSQL de queue/worker | sim | repository gate |
| Retrieval >40 | E2E PostgreSQL com embeddings fake/pgvector | sim | repository gate |
| Sigilo | testes E2E/local determinístico | sim | repository gate |
| Provider contracts | fakes e monkeypatches | sim | repository gate |
| Pipeline operacional | `scripts/smoke_offline.*` | sim | repository gate |
| Frontend | harness Node comportamental | sim | repository gate |
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
