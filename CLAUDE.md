# data

The data service behind my.soundadam.com/data/ and data.api. See README.md;
the decision is platform-gitops `docs/adr/datasets-own-service-per-competition-grants-on-request.md`.

- Verify with `make check` (ruff, pytest, requirements.txt in sync with uv.lock).
- Releases are CC BY-NC-SA: never commit, paste or upload parquet, audio,
  video or a subset of them; tests use the synthetic release in tests/conftest.py.
- Never log, commit or paste a personal key or a presigned URL.
- The console frame in `page.py` copies platform-gitops' portals; keep it equal to them.
- A release is a tag `vX.Y.Z`; platform-gitops pins the digest only after the image is published.
