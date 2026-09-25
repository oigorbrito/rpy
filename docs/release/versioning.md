# Release versioning

Rpy uses Semantic Versioning 2.0.0 for release identities.

The project is still in the `0.y.z` initial-development phase. A release tag is immutable: once a version has been released, later repository changes require a new version and must never be presented as the contents of the already-published tag.

## Published baseline

The historical first release is:

- version: `0.1.0`;
- tag: `v0.1.0`;
- immutable tag commit: `ea3399ec4780756bf149f2ebd2bbb3bd3a790705`.

Do not move, recreate or retarget `v0.1.0`.

## Current main

Current `main` contains substantial work after `v0.1.0`. The release owner selected `0.1.1` for the next release. Release preparation is tracked in `docs/release/next-release.md`; the immutable tag must not be created until the exact release head has passed the complete gates and its published image digest has been smoke-tested and attested.

Documentation must not call current `main` “v0.1.0”, and the historical `v0.1.0` tag must never be moved or reused.

## Version selection rule

When the release owner selects the next identifier:

1. review changes since the previous tag against the documented public API and deployment contract;
2. select a SemVer-compatible version appropriate for the compatibility scope;
3. update `pyproject.toml` and create matching release notes in one release-preparation change;
4. run the complete project/release gates on the exact release head;
5. build the image once in trusted CI, smoke-test its digest, and record/verify its attestation;
6. create the new immutable tag only after those gates are green.

A pre-release identifier (for example `-rc.1`) may be used when a formally versioned candidate is needed before the final release. Its use must also be explicit; the repository does not infer or generate it automatically.

## Source

Policy basis: Semantic Versioning 2.0.0, https://semver.org/. In particular, a released version must not be modified and later modifications require a new version. Major version zero remains the initial-development phase.
