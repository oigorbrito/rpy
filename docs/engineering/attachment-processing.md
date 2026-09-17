# Attachment processing boundary

Issue #137 introduces attachment support in staged blocks. This document defines the processing contract before downloader, parser or OCR code is enabled.

## Phase 1: durable state foundation

This phase stores only attachment metadata, processing state and normalized text chunks. It does **not** enable Judit attachment download and keeps `with_attachments: false` until the source download contract is implemented and tested.

Attachment states are:

- `pending`: known attachment whose bytes have not completed local processing;
- `ready`: local parsing/chunking completed and chunks are eligible for retrieval;
- `unavailable`: the authorized source could not provide the attachment;
- `corrupt`: bytes were obtained but fail format/integrity parsing;
- `unreadable`: supported content was obtained but no usable text could be extracted, including OCR failure.

A failure state is attachment-local. It must not delete movements, invalidate other attachments, or make a process summary unusable by itself.

## Planned local processing

The initial supported parsing targets are:

1. PDF documents with an extractable text layer;
2. UTF-8 plain text where the source metadata identifies a textual attachment;
3. raster/image-only PDF or supported image content only through a local OCR path.

OCR is intentionally not implemented in the foundation PR. The implementation should use a local/offline OCR engine rather than sending document images or bytes to an external model. The exact OCR dependency and supported image formats must be benchmarked before activation. No network-dependent model download may be added to CI or the standard offline image.

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
