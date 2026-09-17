# Attachment processing boundary

Issue #137 introduces attachment support in staged blocks. This document defines the processing contract before downloader or OCR code is enabled.

## Phase 1: durable state foundation

This phase stores only attachment metadata, processing state and normalized text chunks. It does **not** enable Judit attachment download and keeps `with_attachments: false` until the source download contract is implemented and tested.

Attachment states are:

- `pending`: known attachment whose bytes have not completed local processing;
- `ready`: local parsing/chunking completed and chunks are eligible for retrieval;
- `unavailable`: the authorized source could not provide the attachment;
- `corrupt`: bytes were obtained but fail format/integrity parsing;
- `unreadable`: supported content was obtained but no usable text could be extracted, including OCR failure.

A failure state is attachment-local. It must not delete movements, invalidate other attachments, or make a process summary unusable by itself.

## Phase 2: bounded local text processing

The first local parser accepts only source bytes already obtained through an authorized caller and identified as `text/plain` (content-type parameters such as `charset=utf-8` are normalized away). It does not fetch URLs and does not retain raw bytes.

Runtime limits:

- `ATTACHMENT_MAX_BYTES`, default 10 MiB;
- `ATTACHMENT_CHUNK_CHARS`, default 4000 and minimum 256 characters.

UTF-8 text is decoded locally, CRLF/CR line endings are normalized to LF, empty text is rejected, and NUL/invalid UTF-8 bytes are classified as `corrupt`. Oversized, empty or unsupported content is classified as `unreadable`. The SHA-256 stored on `process_attachments` is calculated from the received bytes; per-chunk SHA-256 continues to be calculated from normalized chunk text.

Text chunking is deterministic and non-overlapping. It prefers a whitespace/newline boundary in the latter half of the configured chunk window and stores normalized character positions as half-open `[char_start, char_end)` offsets. Reprocessing the same source attachment replaces chunks atomically through the Phase 1 persistence primitive.

## Phase 3: PDF text-layer processing

`application/pdf` bytes already obtained by an authorized caller are parsed locally with `pypdf`. The same byte limit is applied before parsing. The parser requires a PDF header and does not fetch fonts, images, models or any other network resource.

For each page with extractable text:

- line endings are normalized;
- text is chunked with the same deterministic non-overlapping algorithm;
- `page_start`/`page_end` identify the one-based source page;
- `char_start`/`char_end` are half-open offsets inside that normalized page text.

Malformed/non-PDF bytes are `corrupt`. Password-protected PDFs are `unreadable` because Rpy has no authorized password contract. PDFs with no extractable text layer are `unreadable` with `pdf_text_unavailable`; they are not silently treated as empty or sent to an external service. That state is the explicit handoff point for the future local OCR path.

`pypdf` is pinned through the repository dependency lock. Raw PDF bytes are never persisted by this layer.

## Planned local processing

The supported parsing targets are:

1. PDF documents with an extractable text layer — implemented by Phase 3;
2. UTF-8 plain text where the source metadata identifies a textual attachment — implemented by Phase 2;
3. raster/image-only PDF or supported image content only through a local OCR path — pending benchmark and implementation.

OCR remains intentionally deferred until an offline engine and supported image formats are benchmarked. OCR must use a local/offline engine rather than sending document images or bytes to an external model. No network-dependent model download may be added to CI or the standard offline image.

## Chunking contract

Chunks are attachment-specific and retain:

- process id and process version id;
- attachment id and stable source attachment id;
- zero-based chunk order;
- optional page start/end;
- optional character start/end;
- SHA-256 of normalized chunk text.

Page and character bounds are paired and monotonic. Reprocessing an attachment atomically replaces its chunks, so retries and source redelivery cannot accumulate duplicate chunks. Overlap is not assumed by default; a parser may introduce bounded overlap only when the document structure/benchmark justifies it.

## Security and tenancy

Attachment retrieval is fail-closed:

- tenant authorization is checked through `tenant_processes`;
- process id and current version id must match before any chunk reaches RAG context;
- only attachments in `ready` state are retrievable;
- secret processes (`secrecy_level > 0`) never return attachment text to the external-provider path;
- the foundation stores no provider response payload and no raw attachment bytes;
- fixtures and logs must use synthetic text and identifiers only.

A later downloader must fetch bytes only from the source authorized for the process/tenant and must never log document bodies, signed download URLs, credentials or raw provider payloads.

## Retrieval and provenance

The foundation does not yet mix attachment chunks into BM25/vector retrieval. When that stage is added, attachment candidates must preserve the same tenant/process/current-version filters before ranking and must keep attachment/chunk identifiers and source positions so used passages can be persisted as safe summary provenance.

## Judit activation gate

`with_attachments` remains `false` until all of the following exist in one tested rollout:

- an authenticated/authorized download contract;
- local byte-size/content-type limits;
- parser dispatch and failure classification;
- idempotent state/chunk persistence;
- secret-process short-circuit;
- synthetic integration coverage proving failed attachments do not damage valid process data.
