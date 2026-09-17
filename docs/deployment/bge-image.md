# Offline BGE image variant

The default `Dockerfile` remains the small application image. BGE deployments derive a second immutable application image from that already-published base using `Dockerfile.bge`.

The BGE build is intentionally offline. It accepts two BuildKit contexts supplied by release automation rather than storing large artifacts in Git:

- `bge_wheels`: a complete wheelhouse plus `requirements.lock`; the lock must pin every package transitively, include `FlagEmbedding==1.4.2`, and carry SHA-256 hashes for every artifact;
- `bge_model`: the complete pre-provisioned `BAAI/bge-m3` model artifact.

Example:

```sh
docker buildx build -f Dockerfile.bge \
  --build-arg RPY_BASE_IMAGE=ghcr.io/oigorbrito/rpy@sha256:<base-digest> \
  --build-context bge_wheels=/secure/artifacts/bge-wheelhouse \
  --build-context bge_model=/secure/artifacts/bge-m3 \
  -t ghcr.io/oigorbrito/rpy-bge:<candidate> .
```

The derived build sets `PIP_NO_INDEX=1`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and `HF_DATASETS_OFFLINE=1`. Package installation uses `--require-hashes` and only the supplied wheelhouse. The image build fails if the lock is absent/unhashed, a required wheel is unavailable, `FlagEmbedding` cannot be imported, the local BGE artifact is missing `config.json`, a declared `hidden_size` is not 1024, or `BGE_EMBEDDING_PATH` is not the verified artifact directory.

The verifier is copied from the release checkout into the derived build explicitly; the already-published base image is not assumed to contain it. This keeps the base digest immutable while allowing release automation to verify the candidate image contract.

After the build, run the normal image runtime smoke plus the BGE verifier against the exact candidate image. Publish and record its registry digest. Production must set `RPY_IMAGE` to that immutable BGE-capable digest before enabling `EMBEDDING_SPACE_RUNTIME_ENABLED=true` with `EMBEDDING_PROVIDER=bge`.

The same `RPY_IMAGE` digest continues to be used by migrate, API, workers and scheduler. This deliberately favors one auditable release artifact over service-specific application images. Non-worker services simply carry the unused BGE dependency layer.

Do not enable BGE solely because the image builds. The historical reindex and retrieval-quality benchmark required by issue #124 remain separate rollout gates.
