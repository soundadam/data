# data

The lab's competition datasets, for members granted a competition:
`my.soundadam.com/data/` for people, `data.api.soundadam.com` for scripts.
Why it is shaped this way: platform-gitops
`docs/adr/datasets-own-service-per-competition-grants-on-request.md`.

- **Catalog**: [`catalog.json`](src/dataservice/catalog.json). Each
  competition has an id, a title, a kind (its viewer, `kinds/`), and its
  release: one bucket and a prefix in SeaweedFS.
- **Grants**: members owns who holds which competition (request on the
  overview, the owner approves). This service asks `GET /data/<github id>`
  on members' internal port and keeps the answer at most 60 s.
- **Releases**: a prefix is published once the publisher writes `_SUCCESS`
  last. The tables are read into memory (DuckDB), and the service reloads
  when `_SUCCESS` changes.
- **Bytes**: audio, video and downloads never pass through the service.
  Pages and scripts get presigned GETs on `s3.api.soundadam.com`, valid for
  10 minutes.

## Ports

| Port | Reached as | Who | What |
| --- | --- | --- | --- |
| 8080 | `my.soundadam.com/data/` (prefix stripped) | SecurityPolicy `my`, then role `data` checked here | `/` competitions, `/keys` personal keys, `/c/<id>/` viewer, `/api/<id>/…` its JSON, `/row` the overview row |
| 8081 | members only (NetworkPolicy) | — | `/catalog`, `/healthz` |
| 8082 | `data.api.soundadam.com` | `Authorization: Bearer sdd_…` | `/v1/`, `/v1/<id>/files`, `/v1/<id>/presign?key=`, `/v1/<id>/<view…>` |

Personal keys are stored as SHA-256 hashes in `keys.sqlite3` on the PVC
(`STATE_DIR`, default `/var/lib/data`). There are at most 10 per person, and
each acts as the GitHub id it was issued to.

```sh
curl -H "Authorization: Bearer $KEY" https://data.api.soundadam.com/v1/roboheard-2027/files
curl -H "Authorization: Bearer $KEY" "https://data.api.soundadam.com/v1/roboheard-2027/presign?key=Audio/S001.wav"
```

## Kind `roboheard`

The release is written by `rh data publish` in roboheard-train:

- `index.json`, `stats.json`, `queries.json`;
- `sessions`, `segments`, `speakers` and `mining.parquet`;
- `frames/<S>.parquet`;
- `Audio/`, `Audio_AEC/` and `Video/`.

The viewer ([`static/roboheard.js`](src/dataservice/static/roboheard.js))
replaces rh_preview's three views:

- stats and the mining queries;
- one query's hits, in the order rh_preview draws with seed 0;
- one session's timeline: energy curve and speaker lanes, 60 s at a time.

Each view leads to a segment, which has:

- a context span;
- a choice of array mic, the 6-mic mean, AEC mic, or echo reference ch7;
- video from any published camera, muted and following the audio.

The browser reads the wav header and then only the span's bytes, using
HTTP Range on the presigned URL.

## Environment

| Variable | Default |
| --- | --- |
| `S3_ENDPOINT` | `http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333` |
| `S3_PUBLIC_ENDPOINT` | `https://s3.api.soundadam.com` (presigned URLs and the CSP) |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | the read-only key, Secret `data/s3-read` |
| `MEMBERS_URL` | `http://members.members.svc.cluster.local:8081` |
| `STATE_DIR` | `/var/lib/data` |
| `PORTAL_ORIGIN` | `https://my.soundadam.com` (the Origin actions must carry) |

## Develop and release

```sh
uv sync
make check          # ruff, pytest, requirements.txt equal to uv.lock
make requirements   # after changing dependencies
```

A tag `vX.Y.Z` publishes `ghcr.io/soundadam/data:X.Y.Z` and prints its
digest. Production runs only the digest that platform-gitops pins in
`clusters/production/apps/data/deployment.yaml`. Publish the tag before
that change, never after.

The data is CC BY-NC-SA. No release file is ever committed here: not
parquet, audio, video, or a subset. Tests build their own synthetic
release.
