# data

The lab's competition datasets, for members granted a competition:
`my.soundadam.com/data/` for people, `data.api.soundadam.com` for scripts.
Why it is shaped this way: platform-gitops
`docs/adr/datasets-own-service-per-competition-owner-grants.md`.

- **Catalog**: [`catalog.json`](src/dataservice/catalog.json). Each
  competition has an id, a title, a kind (its viewer, `kinds/`), and its
  release: one bucket and a prefix on the origin, `rclone serve s3` on g18.
- **Grants**: members owns who holds which competition (the owner grants
  it; there is no request). This service asks `GET /data/<github id>`
  on members' internal port and keeps the answer at most 60 s.
- **Releases**: a prefix is published once the publisher writes `_SUCCESS`
  last. The tables are read into memory (DuckDB), and the service reloads
  when `_SUCCESS` changes.
- **Bytes**: the origin is reachable only from this pod, through its SSH
  tunnel to g18. Pages and scripts get signed links to `<api base>/raw/<file>`,
  valid for 10 minutes and bound to one competition and file; the service
  streams the file from the origin with HTTP Range. When g18 cannot be
  reached, the link answers 502.

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

- `index.json`, with `listen`: the signals, the spectrogram's hop, tile and
  rows, and each session's AEC shift;
- `sessions` and `segments.parquet`;
- `listen/<S>/<signal>.mp3` and `.jpg` for `mic1`…`mic6`, `mean`, `ref` and
  `aec1`…`aec6`: each signal encoded once, and its spectrogram as one sheet of
  tiles stacked top to bottom;
- `Audio/`, `Audio_AEC/` and `Video/`.

The viewer ([`static/roboheard.js`](src/dataservice/static/roboheard.js)) is
rh_preview's whole-session player. One session's video, the chosen signal,
its spectrogram, the speaker lanes and the transcript share one clock. The
audio is the clock, and the video is the published mp4, muted and following it.
Nothing is encoded at request time: the page plays the published files over
HTTP Range on signed links and fetches them again when a link expires.

## Kind `corpus`

A sample bank, not a dataset: a dozen or two clips from every ASR corpus the
lab holds, so people can hear what each one is like before using it. It is
written by `rh-corpus release` in roboheard-train. The bank is kept on elon
(`s3://datasets/samples/<version>/` in its SeaweedFS); the origin on g18
holds a copy at the same bucket and prefix, written with `_SUCCESS` last.
A release holds:

- `corpora.json`: the bank's version and encoding, the kinds of recording
  (far-field meetings, in-car, dialogue, web, read, other languages), and each
  corpus with its size, channels, labels, licence and status. `sampled` has
  clips, `excluded` has clips but the corpus is not used, and `listed` has none;
- `clips.parquet`: one row per clip, with its group in the corpus (e.g. `solo`
  or `overlap`), text, speaker, seconds, and the file and seconds it was cut from;
- `clips/<corpus>/<clip_id>/<signal>.opus`: Ogg Opus, mono, 16 kHz, 32 kbps,
  one file per signal (`far`, `near` or `mono`), at most 20 s each.

The viewer ([`static/corpus.js`](src/dataservice/static/corpus.js)) lists the
corpora by kind and plays one corpus's clips group by group. Each clip is
fetched once on a signed link and played from a blob, and the page decodes the
same bytes to draw its spectrogram. Nothing derived is stored.

## Environment

| Variable | Default |
| --- | --- |
| `S3_ENDPOINT` | `http://127.0.0.1:18333` (the tunnel's local end) |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | the origin's `--auth-key`, Secret `data/origin` |
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
