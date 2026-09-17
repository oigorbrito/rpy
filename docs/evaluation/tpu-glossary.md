# TPU glossary source contract

## Purpose

Rpy resolves process class/subject codes against a pinned local snapshot of the
CNJ Tabelas Processuais Unificadas (TPU). Runtime generation must not query an
external terminology service and must not infer a definition by name when a code
is absent from the pinned snapshot.

## Authoritative source

Publisher: Conselho Nacional de Justiça (CNJ).

Public entry point:

- https://www.cnj.jus.br/tabela-processuais-unificadas/

The CNJ public TPU system states that tables are continuously maintained, exposes
previous versions, downloadable Excel/SQL artifacts and a public API/WebService.
The public class/subject pages observed while preparing this snapshot reported TPU
version `12/09/2026`.

Version/download pages:

- https://www.cnj.jus.br/sgt/versoes.php
- https://www.cnj.jus.br/sgt/versoes_anteriores.php
- https://www.cnj.jus.br/sgt/infWebService.php

The repository snapshot is `data/tpu/2026-09-12.json`. It is deliberately small:
it proves the versioned-source/runtime contract without vendoring the complete CNJ
dataset before redistribution terms have been reviewed.

## Licensing / redistribution status

The CNJ pages above provide public consultation and downloads. During the source
review for this change, no explicit content license governing redistribution of a
complete TPU dump was identified on those pages. Rpy therefore does **not** claim
that the full CNJ dataset is licensed for unrestricted redistribution.

Until the redistribution terms are reviewed, repository snapshots must contain
only the minimum normalized entries needed for the product contract/tests, with
source references. A full snapshot must not be imported merely because an Excel,
SQL or API endpoint is publicly reachable.

## Snapshot schema

Each file contains:

- `schema_version`: Rpy's local snapshot schema;
- `tpu_version`: immutable snapshot identifier (`YYYY-MM-DD` for CNJ releases);
- publisher/source/version metadata;
- `entries[]`, keyed by `(kind, code)` where `kind` is `class` or `subject`;
- source name, normalized definition and source reference for every entry.

Unknown codes are omitted. The resolver never uses fuzzy/name matching and never
falls back to network access.

## Update procedure

1. Check the CNJ version/download page and record the published TPU version.
2. Review the source artifact/API response and the redistribution terms applicable
   at that time.
3. Create a **new** snapshot file; never overwrite a prior released snapshot.
4. Preserve numeric TPU codes as strings and distinguish classes from subjects.
5. Normalize only whitespace/encoding needed for deterministic JSON; do not invent
   explanatory text absent from the reviewed source.
6. Add/update tests that resolve known codes, omit unknown codes and prove the
   selected snapshot version is present in generated context metadata.
7. Run the complete Rpy CI, including offline release smoke. Runtime must remain
   provider-free for glossary resolution.

## Current integration boundary

This change establishes the local versioned catalog and deterministic resolver.
Injection into the RAG provider context and PostgreSQL integration evidence are a
separate #138 block. Secret-process context must continue to bypass external
providers regardless of glossary availability.
