# SueNow — v1 brief: "Is my music in a known AI training dataset?"

## Goal

A web tool where a musician pastes their Spotify artist link (and/or YouTube
channel link) and gets back a list of their tracks/videos that appear in
publicly known research datasets used to train and evaluate music-generation
models. Each hit says which dataset, which row, what that dataset is, and what
the artist can do next. Target: working end-to-end in a day, shipped to a
public URL within a week so real musicians can try it.

Model for the product: Spawning's Have I Been Trained (image datasets), for
audio.

## What v1 is NOT

- No audio upload, no fingerprinting, no ML. Pure ID lookup.
- No claim that Suno (or anyone) trained on a specific track. Reports say
  "present in dataset X", plus neutral context about how X is used.
- No indexing of leaked or hacked data.
- No MTG-Jamendo / FMA (artists already know they uploaded there). Later tier.

## Mechanic

1. Artist pastes a Spotify artist URL and/or a YouTube channel URL.
2. We pull all their Spotify track IDs (artist → albums → tracks, incl.
   singles/compilations/appears_on) and YouTube video IDs (channel → uploads
   playlist → items). Cache results per artist for 24h.
3. Look those IDs up in local tables built from the dataset manifests.
4. Render a report.

## Datasets to load (manifests only, all public downloads)

| Dataset | Key | Size | Source |
|---|---|---|---|
| DISCO-10M (ETH Zurich, 2023) | Spotify track ID, YouTube URL, artist, title | ~10M rows | Hugging Face `DISCOX/DISCO-10M` |
| AudioSet (Google, 2017) | YouTube ID + start/end seconds + labels | ~2M rows (filter to music labels) | research.google.com/audioset — unbalanced_train, balanced_train, eval CSVs |
| MusicCaps (Google, 2023) | YouTube ID + start/end + caption | 5.5k rows | Hugging Face `google/MusicCaps` |
| Million Song Dataset (2011) | artist, title only | 1M rows | optional, fuzzy artist+title match, flag as "probable" |

Inspect each manifest's actual schema before writing loaders; column names in
this table are approximate. Normalise to one table:

```
hits(dataset TEXT, dataset_row_id TEXT, key_type TEXT,  -- 'spotify_track' | 'youtube_video' | 'artist_title'
     key TEXT, artist TEXT, title TEXT, start_s REAL, end_s REAL, extra JSON)
INDEX ON (key_type, key)
```

SQLite is fine for 12M rows. Loader script: `suenow load <dataset>`,
idempotent, resumable.

Each dataset also needs a descriptor (YAML) with: name, publisher, year,
licence terms, how it's used (training/evaluation, which known models cite
it), a neutral litigation-context paragraph with source URL and last-reviewed
date. This text is shown to users verbatim, so keep it careful.

## Platform lookups

- Spotify Web API, client-credentials flow. `GET /artists/{id}/albums`
  (include_groups=album,single,compilation,appears_on, paginate) then
  `GET /albums/{id}/tracks`. Collect track IDs, names, ISRCs if available.
- YouTube Data API v3. `channels.list` → `contentDetails.relatedPlaylists.uploads`
  → `playlistItems.list` (paginate). Collect video IDs and titles.
- Accept the common URL forms (open.spotify.com/artist/…, youtube.com/@handle,
  /channel/UC…, /c/…). Handle @handles via `channels.list?forHandle=`.
- API keys from env vars. Fail gracefully with a clear message if quota hit.

## Report

Per artist: number of tracks checked, number of hits, then per hit:
dataset, matched key, the dataset's row (artist/title/clip times/labels/
caption as available), dataset description, and a "what this means / what
you can do" block drawn from the descriptor. Include a not-legal-advice line.

"What you can do" content (write once, reuse): opt-out/registries, relevant
collective actions and collecting-society routes (PRS for Music, GEMA),
the GEMA v Suno ruling summary, the Warner/BMG/Believe licensing context.
Keep every item sourced and dated.

Shareable: each report gets a stable URL (`/r/<artist_id>`) and an OG image
with "N of your M tracks appear in AI training datasets". That's the growth
loop.

Export: JSON, plus a "download evidence report" PDF with timestamp, tool
version, dataset versions, and SHA-256 of the JSON.

## Stack

Python 3.12, FastAPI, SQLite, `httpx`, `typer` for the CLI, Jinja2
templates + minimal CSS (no JS framework). Deploy on a single small VPS or
Fly.io. `weasyprint` for PDF.

Tests: a fixture of ~50 fake manifest rows and a fake Spotify/YouTube client;
assert hits/no-hits and report rendering. No network in CI.

## Milestones

1. Loaders for MusicCaps and AudioSet (small, fast to validate). Table +
   index. `suenow check --spotify <url> --youtube <url>` CLI printing hits.
2. DISCO-10M loader (the big one; stream from HF, don't load into RAM).
3. Spotify + YouTube clients with caching.
4. Web UI: one input box, report page, share URL, PDF.
5. Deploy. Post it where musicians gather. Watch the hit rate and whether
   anyone shares a report.

## Later (not now)

- Fingerprint AudioSet/MusicCaps clips to catch fan re-uploads of an artist's
  songs (needs fetching 10s YouTube clips + Chromaprint index).
- MTG-Jamendo / FMA tier.
- Output-similarity checks using Versions' cover-detection pipeline.
- Prospective protection (per-artist watermark/canary).

## Wording rules

Never "proof", "stolen", "Suno trained on this". Always "present in dataset X
(row Y)", then context. Every litigation claim cites a source and date.
