# avalon

Standalone CLI that analyzes, tags, and organizes a music library: BPM/key
extraction plus a broad set of Essentia-derived descriptors (mood, energy,
danceability, acousticness, vocal presence, genre), ID3-family tag
normalization, cover art embedding, optional format/bit-depth conversion,
and folder organization -- as a one-shot scan or a watching daemon.

## Requirements

- Python 3.10 or 3.11 (capped below 3.12 -- see the comment in
  `pyproject.toml` on the `essentia-tensorflow` pin: it's the newest release
  with both a macOS arm64 wheel built for an old-enough deployment target to
  run on common dev machines *and* a Linux x86_64 wheel in the same release)
- [uv](https://docs.astral.sh/uv/)
- `ffmpeg`/`ffprobe` on `PATH`
  - macOS: `brew install ffmpeg`
  - Linux: `apt install ffmpeg` (or your distro's equivalent)

## Installation

```bash
git clone <repository-url>
cd avalon
uv sync
```

On first run, avalon downloads and caches the Essentia pretrained models it
needs (~26.5MB total) to `~/.cache/avalon/models/`.

## Usage

```bash
# Analyze/tag/convert a single file or folder, in place
uv run avalon analyze ~/Music/Downloads --recursive

# ...or reorganize into {artist}/{album}/{title}.{ext} under a destination
uv run avalon analyze ~/Music/Downloads --recursive --dest ~/Music/Library

# Convert lossless sources (FLAC/ALAC/WAV/AIFF/...) + cap bit depth/sample
# rate while tagging; mp3/aac/other lossy sources are left untouched
uv run avalon analyze ~/Music/Downloads --dest ~/Music/Library \
    --convert-lossless-to aiff --max-bit-depth 16 --max-sample-rate 48000

# Watch folders continuously; processes the existing backlog on startup,
# then reacts to new/changed files
uv run avalon watch ~/Music/Downloads --dest ~/Music/Library

# Inspect what's actually stored in a file's tags (debugging)
uv run avalon inspect ~/Music/Library/Artist/Album/01\ -\ Title.aiff
```

## Tag schema

avalon writes two tags per file, both inside the ID3-family/Vorbis/MP4 tag
sets already used by common players -- nothing exotic:

- **Headline** (COMM for MP3/AIFF/WAV, DESCRIPTION for FLAC, `desc` for
  MP4): a short, human-scannable string -- `bpm:128;key:Am;camelot:8A;
  energy:0.71;genre:Techno`. `key` is standard notation (also what the
  canonical TKEY/INITIALKEY/MP4 key field gets, matching this library's
  existing tags); `camelot` is the DJ-wheel equivalent, kept alongside it
  rather than replacing it. Extends the convention already used by
  `swinsian-sync`'s `rekordbox_sync.py`; existing non-generated comment
  text is preserved rather than clobbered.
  - `--headline-format bpm,key,energy` picks which fields appear and in
    what order. Available fields: `bpm`, `key`, `camelot`, `energy`,
    `genre`, `dance`, `acoustic`, `electronic`, `vocal`, `happy`, `sad`,
    `relaxed`, `party`, `moodtheme`.
  - `--headline-tag NAME` redirects the headline to a different tag
    instead of the format's native comment field -- e.g.
    `--headline-tag AVALON_HEADLINE` writes a `TXXX:AVALON_HEADLINE`
    frame (ID3-family) or `----:com.avalon:AVALON_HEADLINE` atom (MP4)
    rather than touching COMM/desc at all. FLAC just uses the given name
    directly as the Vorbis comment field.
- **Extended** (`TXXX:AVALON_ANALYSIS` for MP3/AIFF/WAV, a second Vorbis
  comment field for FLAC, `----:com.avalon:analysis` for MP4): the full
  descriptor roster as the same style of compact `key=value;...` string,
  owned entirely by avalon.

Canonical fields (title/artist/album/genre/BPM/key) fill in only when
missing by default -- avalon won't overwrite values you already trust (e.g.
from Rekordbox/Mixed In Key) unless you pass `--force-reanalyze`.

## Phase 2 (not yet implemented)

MusicBrainz/AcoustID/Discogs ID lookup and enrichment is a deferred stretch
goal -- the `avalon/identity/` package is stubbed but not wired into the
pipeline yet.

## Development

```bash
uv run pytest
```
