# YouTrained

Is my music in a known AI training dataset? Pure identifier lookup against the public
manifests of research datasets used to train and evaluate music-generation models.
See `YOUTRAINED_BRIEF.md` for the product brief and wording rules.

## Setup

```sh
uv sync
uv run youtrained init-db
uv run youtrained load musiccaps        # 5.5k rows, seconds
uv run youtrained load audioset         # ~130 MB download, ~1M music rows, a few minutes
uv run youtrained load laion_disco_12m  # ~750 MB parquet download, 12.3M rows, streamed
uv run youtrained status
```

Loads are idempotent and resumable: re-running skips files already marked done
(`--force` reloads), and a crash mid-file is safe to re-run. Downloads go to
`data/cache/` (override with `YOUTRAINED_CACHE_DIR`); the database is `data/youtrained.sqlite`
(override with `YOUTRAINED_DB`).

## Checking ids

```sh
uv run youtrained check --youtube-ids dQw4w9WgXcQ,zzzzzzzzzzz
uv run youtrained check --youtube-ids dQw4w9WgXcQ --json
```

```sh
cp .env.example .env            # then paste YOUTUBE_API_KEY (and Spotify creds) into it
uv run youtrained check --youtube https://youtube.com/@yourhandle
```

`--youtube <channel url>` resolves the channel (handle, /channel/UC…, /c/…, /user/…),
walks its uploads playlist via the YouTube Data API v3, and caches the video list for
24 hours in the database. A lookup costs about 1 quota unit per 50 videos. `--spotify
<artist url>` is parsed today; the Spotify client is the next milestone-3 piece.

## Channel mapping (optional, recommended)

```sh
uv run youtrained map-channels --budget 9000   # 50 video ids per API call, 1 quota unit each
uv run youtrained status                       # shows mapped / missing / queued
uv run youtrained top-channels                 # channels with the most dataset clips
```

Maps every AudioSet and MusicCaps video id to its owning channel. The queue is seeded from
the loaded rows and drained in batches; the run stops cleanly when the budget is spent or
YouTube reports the daily quota exhausted, and re-running resumes. About 1.07M ids, so
roughly 21,400 calls, or three days at the default 10,000 units/day. Once the queue is empty,
`check` and the web UI resolve a channel with one API call and read its videos from the
mapping instead of walking the uploads playlist. LAION-DISCO-12M is not mapped: it already
carries the artist's YouTube Music channel id.

## Artist matching (LAION-DISCO-12M)

LAION lists songs under the artist's **YouTube Music** channel id, not the channel the artist
uploads to, so upload-id matching alone misses them. After loading LAION, build the artist
index once (a few minutes, one sequential scan):

```sh
uv run youtrained index-artists
uv run youtrained check --artist "Bonobo"                                  # probable, by name
uv run youtrained check --youtube https://music.youtube.com/channel/UC...  # exact, by artist id
```

Every channel check also tries the channel id as an artist id (exact) and the channel title
as an artist name (probable). Artist-level results appear in their own report section,
labelled with the basis of the match.

## Web UI

```sh
uv run youtrained serve                 # http://127.0.0.1:8000
```

One input box. Submitting a channel link redirects to a stable report URL, `/r/yt_<channel id>`,
which is safe to share and is overwritten when the channel is checked again. Each report has
`/r/<id>.json` (with an `X-Report-SHA256` header, the hash is also printed on the page),
`/r/<id>.pdf` (evidence report) and `/r/<id>/og.png` (the share image used by the Open Graph
tags). Templates live in `src/youtrained/web/templates`, CSS in `src/youtrained/web/static`.

PDF export uses WeasyPrint, which needs pango installed on the machine:

```sh
brew install pango          # macOS; on Debian/Ubuntu: apt install libpango-1.0-0 libpangoft2-1.0-0
uv sync --extra pdf
```

Without it the PDF route answers 501 with that instruction; everything else works.

## Development

```sh
uv run pytest          # no network: loaders run against tests/fixtures
uv run ruff check src tests && uv run ruff format --check src tests
```

Dataset descriptors (the text shown to users) live in `src/youtrained/datasets/*.yaml`;
`_common.yaml` holds the shared litigation context and "what you can do" actions.
A test rejects any descriptor text containing the banned phrases from the brief.

## Deploy (GCP e2-micro, GitHub → GHCR → VM)

Push to `main` runs tests and publishes `ghcr.io/<owner>/youtrained:latest`. On the VM
(Debian 12, e2-micro, 30 GB disk), once:

```sh
git clone https://github.com/<owner>/youtrained && cd youtrained
sudo bash deploy/setup-vm.sh <owner>/youtrained     # swap, docker, caddy, systemd unit
sudo nano /opt/youtrained/.env                       # add YOUTUBE_API_KEY
sudo systemctl start youtrained
```

Point `youtrained.com` A records at the VM's IP; Caddy fetches the certificate. From your
machine afterwards: `deploy/deploy.sh` restarts on the latest image, `deploy/upload-db.sh`
ships the local database (checkpointed, compressed with zstd, swapped in atomically).
