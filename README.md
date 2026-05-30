# ICE Portal Audiobook Downloader

Downloads audiobooks from the [ICE Portal](https://iceportal.de) — the onboard WiFi portal of Deutsche Bahn ICE trains.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
uv sync
```

## Usage

### Download all audiobooks

```bash
uv run download.py
```

Audiobooks are saved under `data/hoerbuecher/<title>/` as `.mp4` files.
A `done` marker is written when a book is fully downloaded.

### List available audiobooks

```bash
uv run download.py --list
```

Shows all titles and their download status.

### Filter by name

```bash
uv run download.py --filter "Sylt"
```

Downloads only audiobooks whose title contains the given substring (case-insensitive).

Combine with `--list` to preview matches without downloading:

```bash
uv run download.py --list --filter "Sylt"
```

## How it works

1. Fetches a session cookie from `iceportal.de`
2. Queries `/api1/rs/page/hoerbuecher` for the audiobook list
3. For each book, fetches metadata from `/api1/rs/page/hoerbuecher/<id>/`
4. Resolves each episode's CDN URL via `/api1/rs/audiobook/path/<id>/<n>`
5. Downloads with resume support (byte-range requests)

## File layout

```
data/
└── hoerbuecher/
    └── <title>/
        ├── done          # marker when complete
        ├── working       # timestamp while downloading
        ├── page.json     # metadata from API
        └── audiobook/
            └── path/
                └── <id>/
                    └── <n>   # episode .mp4 files
```

## License

MIT
