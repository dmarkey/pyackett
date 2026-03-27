# Pyackett

A Python clean room implementation of a Torznab-compatible indexer proxy. Works as a standalone server (Sonarr/Radarr compatible) and as an importable Python library.

Loads Jackett's 550+ YAML indexer definitions directly. Includes browser-grade TLS fingerprinting and automatic Cloudflare Turnstile bypass.

## Install

```bash
pip install pyackett

# With Cloudflare challenge support (adds Camoufox browser):
pip install pyackett[cloudflare]
```

## Quick Start

### As a server

```bash
# Download definitions from Jackett GitHub and start the server
pyackett --from-github jackett --port 9117

# With a SOCKS5 proxy
pyackett --from-github jackett --proxy socks5://user:pass@host:1080

# With local definitions
pyackett -d /path/to/definitions
```

Then point Sonarr/Radarr at `http://localhost:9117/api/v2.0/indexers/{id}/results/torznab/` with the API key shown in the web UI.

### As a Python library

```python
import asyncio
from pyackett import Pyackett

async def main():
    pk = Pyackett(proxy="socks5://user:pass@host:1080")
    pk.load_definitions_from_github(source="jackett")

    await pk.configure_indexer("1337x", {})
    await pk.configure_indexer("therarbg", {})

    results = await pk.search("breaking bad", categories=[5000])
    for r in results:
        print(f"{r.title} | S:{r.seeders} | {r.size}")

    await pk.close()

asyncio.run(main())
```

```python
# Synchronous wrapper for simple scripts
pk = Pyackett()
pk.load_definitions_from_github()
pk.configure_indexer("therarbg", {})
results = pk.search_sync("ubuntu")
```

## Features

### Torznab API
- Full Torznab XML API compatible with Sonarr, Radarr, Prowlarr, qBittorrent
- TorrentPotato JSON support
- Capabilities endpoint per indexer
- Multi-indexer search (`/indexers/all/results/torznab/`)

### Cardigann YAML Engine
- Loads all 550+ Jackett YAML indexer definitions
- Go-style template engine (`{{ if }}`, `{{ range }}`, `{{ eq }}`, etc.)
- CSS selector extraction (BeautifulSoup) and JSONPath
- 25+ filter functions (replace, dateparse, fuzzytime, regexp, etc.)
- 6 login methods (form, cookie, header/API key, GET, POST, captcha)

### HTTP Client
- **curl_cffi** with Chrome/Firefox TLS fingerprint impersonation
- Built-in SOCKS5/SOCKS4/HTTP proxy support
- Request delay per indexer definition
- Automatic Cloudflare detection and bypass

### Cloudflare Bypass (optional)
- Automatic detection of Cloudflare 403/503 challenges
- **Camoufox** (anti-detect Firefox) solves Turnstile challenges headlessly
- Local HTTP CONNECT proxy bridges SOCKS5 auth for browsers
- Firefox TLS fingerprint matching for cf_clearance cookie reuse
- Cookies cached per domain for the session
- Graceful fallback when Camoufox not installed (logs warning, skips CF sites)

### Web UI
- Bootstrap 5 dashboard at `http://localhost:9117/`
- Browse and filter all 550+ available indexers
- Dynamic configuration forms generated from YAML settings
- Manual search with results table
- Per-indexer Torznab URL copy, edit, test, delete

## CLI Options

```
pyackett [OPTIONS]

  --host HOST               Bind address (default: 0.0.0.0)
  -p, --port PORT           Port (default: 9117)
  --config-dir DIR          Config/cache directory
  -d, --definitions-dir DIR Local YAML definitions directory
  --from-github {jackett,prowlarr}
                            Download definitions from GitHub
  --branch BRANCH           GitHub branch (default: master)
  --update-definitions      Force re-download definitions
  --proxy URL               Proxy (socks5://host:port, http://host:port)
  --api-key KEY             API key (auto-generated if not set)
  --log-level {DEBUG,INFO,WARNING,ERROR}
```

## Architecture

```
pyackett/
  core/
    models.py          - ReleaseInfo, TorznabQuery, IndexerDefinition
    manager.py         - IndexerManager (load, configure, concurrent search)
    http.py            - curl_cffi client + Camoufox CF solver
    cache.py           - TTL result cache
    categories.py      - Torznab category mappings
    definitions_fetcher.py - GitHub tarball downloader
  engine/
    cardigann.py       - YAML definition interpreter
    template.py        - Go-style template engine
    filters.py         - 25+ data transformation filters
    selectors.py       - CSS + JSONPath extraction
  api/
    torznab.py         - Torznab XML generation + query parsing
  server/
    app.py             - FastAPI server + web UI
  pyackett.py          - Public library API
  cli.py               - CLI entry point
```

## Development

```bash
git clone <repo>
cd pyackett
uv sync
uv run pytest tests/ -v
```

## License

MIT
