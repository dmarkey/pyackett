"""FastAPI web server for Pyackett - Torznab-compatible API + Web UI."""

from __future__ import annotations

import base64
import json
import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pyackett.api.torznab import (
    ERROR_APIKEY,
    ERROR_GENERAL,
    ERROR_MISSING_PARAMETER,
    caps_xml,
    error_xml,
    parse_torznab_query,
    results_to_xml,
)
from pyackett.core.cache import ResultCache
from pyackett.core.manager import IndexerManager
from pyackett.core.models import TorznabQuery

logger = logging.getLogger("pyackett.server")


def create_app(
    manager: IndexerManager,
    api_key: str | None = None,
    config_dir: Path | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    Args:
        manager: The IndexerManager with loaded definitions.
        api_key: API key for authentication. Generated if not provided.
        config_dir: Directory for server config persistence.
    """
    config_dir = config_dir or Path.home() / ".config" / "pyackett"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Load or generate API key
    server_config_path = config_dir / "server_config.json"
    saved_cfg = {}
    if server_config_path.exists():
        try:
            saved_cfg = json.loads(server_config_path.read_text())
        except Exception:
            pass
    if api_key is None:
        api_key = saved_cfg.get("api_key", "")
    if not api_key:
        api_key = secrets.token_hex(16)
    # Persist
    saved_cfg["api_key"] = api_key
    server_config_path.write_text(json.dumps(saved_cfg, indent=2))

    cache = ResultCache()

    app = FastAPI(title="Pyackett", version="0.1.0")

    # --- Torznab API ---

    @app.get("/api/v2.0/indexers/{indexer_id}/results/torznab")
    @app.get("/api/v2.0/indexers/{indexer_id}/results/torznab/")
    async def torznab_api(request: Request, indexer_id: str):
        """Torznab-compatible search API endpoint."""
        params = dict(request.query_params)

        # Validate API key
        req_key = params.get("apikey", "")
        if req_key != api_key:
            return Response(
                content=error_xml(ERROR_APIKEY, "Invalid API key"),
                media_type="application/xml",
                status_code=200,
            )

        query_type = params.get("t", "")

        # Handle capabilities request
        if query_type == "caps":
            return _handle_caps(indexer_id)

        # Handle search
        if indexer_id == "all":
            return await _handle_search_all(params, request)
        else:
            return await _handle_search(indexer_id, params, request)

    def _rewrite_download_links(results, request: Request):
        """Rewrite HTTP download links to go through our proxy endpoint."""
        for r in results:
            if r.link and not r.link.startswith("magnet:"):
                r.link = _proxy_link(request, r.link)

    async def _handle_search(indexer_id: str, params: dict, request: Request) -> Response:
        indexer = manager.get_indexer(indexer_id)
        if not indexer:
            return Response(
                content=error_xml(ERROR_MISSING_PARAMETER, f"Unknown indexer: {indexer_id}"),
                media_type="application/xml",
            )
        if not indexer.is_configured:
            return Response(
                content=error_xml(ERROR_GENERAL, f"Indexer not configured: {indexer_id}"),
                media_type="application/xml",
            )

        query = parse_torznab_query(params)

        # Check cache
        cached = cache.get(indexer_id, query)
        if cached is not None and query.cache:
            results = cached
        else:
            try:
                results = await indexer.search(query)
                cache.put(indexer_id, query, results)
            except Exception as e:
                logger.error(f"Search error: {e}")
                return Response(
                    content=error_xml(ERROR_GENERAL, str(e)),
                    media_type="application/xml",
                )

        # Apply limit/offset
        total = len(results)
        if query.offset > 0:
            results = results[query.offset:]
        if query.limit > 0:
            results = results[:query.limit]

        _rewrite_download_links(results, request)
        xml = results_to_xml(
            results,
            channel_title=indexer.name,
            channel_link=indexer.site_link,
            self_link=str(request.url),
        )
        return Response(content=xml, media_type="application/xml")

    async def _handle_search_all(params: dict, request: Request) -> Response:
        query = parse_torznab_query(params)

        try:
            results = await manager.search(query)
        except Exception as e:
            logger.error(f"Search error: {e}")
            return Response(
                content=error_xml(ERROR_GENERAL, str(e)),
                media_type="application/xml",
            )

        if query.offset > 0:
            results = results[query.offset:]
        if query.limit > 0:
            results = results[:query.limit]

        _rewrite_download_links(results, request)
        xml = results_to_xml(
            results,
            channel_title="Pyackett",
            channel_link=str(request.base_url),
            self_link=str(request.url),
        )
        return Response(content=xml, media_type="application/xml")

    def _handle_caps(indexer_id: str) -> Response:
        if indexer_id == "all":
            # Aggregate caps
            xml = caps_xml("all", "Pyackett (All)", [], {
                "search": ["q"],
                "tv-search": ["q", "season", "ep", "imdbid", "tvdbid"],
                "movie-search": ["q", "imdbid", "tmdbid"],
                "music-search": ["q", "album", "artist"],
                "book-search": ["q", "author", "title"],
            })
            return Response(content=xml, media_type="application/xml")

        indexer = manager.get_indexer(indexer_id)
        if not indexer:
            return Response(
                content=error_xml(ERROR_MISSING_PARAMETER, f"Unknown indexer: {indexer_id}"),
                media_type="application/xml",
            )

        defn = indexer.definition
        caps_data = defn.get_capabilities()

        # Build category list for XML
        cat_list = []
        from pyackett.core.categories import CATEGORIES
        seen = set()
        for cm in caps_data.categories:
            cat_id = CATEGORIES.get(cm.torznab_cat)
            if cat_id and cat_id not in seen:
                cat_list.append({"id": cat_id, "name": cm.torznab_cat})
                seen.add(cat_id)

        xml = caps_xml(
            indexer_id,
            defn.name,
            cat_list,
            caps_data.search_modes,
        )
        return Response(content=xml, media_type="application/xml")

    # --- Management API ---

    @app.get("/api/v2.0/server/config")
    async def get_server_config():
        """Get server configuration."""
        return {
            "api_key": api_key,
            "app_version": "0.1.0",
            "configured_indexers": len(manager.configured_indexers),
            "total_definitions": len(manager.definitions),
        }

    @app.get("/api/v2.0/indexers")
    async def list_indexers(configured: bool = Query(default=False)):
        """List indexers."""
        if configured:
            return manager.list_configured()
        return manager.list_available()

    @app.get("/api/v2.0/indexers/{indexer_id}/config")
    async def get_indexer_config(indexer_id: str):
        """Get indexer configuration schema."""
        indexer = manager.get_indexer(indexer_id)
        if not indexer:
            defn = manager.definitions.get(indexer_id)
            if not defn:
                return JSONResponse({"error": "Unknown indexer"}, status_code=404)
            return {
                "id": defn.id,
                "name": defn.name,
                "description": defn.description,
                "type": defn.type,
                "site_link": defn.site_link,
                "settings": defn.settings,
                "configured": False,
            }
        return {
            "id": indexer.id,
            "name": indexer.name,
            "type": indexer.indexer_type,
            "site_link": indexer.site_link,
            "settings": indexer.definition.settings,
            "configured": indexer.is_configured,
        }

    @app.post("/api/v2.0/indexers/{indexer_id}/config")
    async def configure_indexer(indexer_id: str, request: Request):
        """Configure an indexer."""
        body = await request.json()
        success = await manager.configure_indexer(indexer_id, body)
        if success:
            return {"status": "ok", "message": f"Indexer {indexer_id} configured"}
        return JSONResponse(
            {"status": "error", "message": "Configuration failed"},
            status_code=400,
        )

    @app.delete("/api/v2.0/indexers/{indexer_id}")
    async def delete_indexer(indexer_id: str):
        """Remove an indexer configuration."""
        manager.remove_indexer(indexer_id)
        return {"status": "ok"}

    @app.get("/api/v2.0/indexers/{indexer_id}/results")
    async def manual_search(indexer_id: str, request: Request):
        """Manual search returning JSON (for Web UI)."""
        params = dict(request.query_params)
        query = parse_torznab_query(params)

        if indexer_id == "all":
            results = await manager.search(query)
        else:
            indexer = manager.get_indexer(indexer_id)
            if not indexer or not indexer.is_configured:
                return JSONResponse({"error": "Indexer not available"}, status_code=404)
            results = await indexer.search(query)

        _rewrite_download_links(results, request)
        return {
            "Results": [
                {
                    "Title": r.title,
                    "Guid": r.guid,
                    "Link": r.link,
                    "Details": r.details,
                    "PublishDate": r.publish_date.isoformat() if r.publish_date else None,
                    "Category": r.category,
                    "Size": r.size,
                    "Seeders": r.seeders,
                    "Peers": r.peers,
                    "MagnetUri": r.magnet_uri,
                    "InfoHash": r.info_hash,
                    "Tracker": r.origin_name,
                    "TrackerId": r.origin_id,
                    "DownloadVolumeFactor": r.download_volume_factor,
                    "UploadVolumeFactor": r.upload_volume_factor,
                    "Gain": r.gain,
                }
                for r in results
            ],
            "Indexers": [
                {"ID": idx.id, "Name": idx.name}
                for idx in manager.configured_indexers.values()
            ],
        }

    # --- Download Proxy ---

    def _proxy_link(request: Request, url: str) -> str:
        """Rewrite a download URL to go through our proxy endpoint.

        Magnet URIs are returned as-is (no proxy needed).
        HTTP(S) .torrent links are rewritten to /api/v2.0/dl?url=<base64>&apikey=<key>
        so the server fetches them through its configured proxy.
        """
        if not url or url.startswith("magnet:"):
            return url
        encoded = base64.urlsafe_b64encode(url.encode()).decode()
        return f"{request.base_url}api/v2.0/dl?url={encoded}&apikey={api_key}"

    @app.get("/api/v2.0/dl")
    async def proxy_download(url: str, apikey: str = ""):
        """Proxy a .torrent download through the server's HTTP client + proxy."""
        if apikey != api_key:
            return Response(content="Invalid API key", status_code=403)

        try:
            decoded_url = base64.urlsafe_b64decode(url.encode()).decode()
        except Exception:
            return Response(content="Invalid URL", status_code=400)

        # Use the manager's shared HTTP client (which has the proxy configured)
        client = None
        for idx in manager.all_indexers.values():
            if idx.client:
                client = idx.client
                break

        if not client:
            from pyackett.core.http import create_http_client
            client = create_http_client()

        try:
            resp = await client.get(decoded_url, cf_retry=True)
            content_type = resp.headers.get("content-type", "application/x-bittorrent")
            # Try to get filename from content-disposition or URL
            filename = decoded_url.rstrip("/").split("/")[-1]
            if not filename.endswith(".torrent"):
                filename += ".torrent"
            return Response(
                content=resp.content,
                media_type=content_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as e:
            logger.error(f"Download proxy error: {e}")
            return Response(content=f"Download failed: {e}", status_code=502)

    # --- Web UI ---

    @app.get("/", response_class=HTMLResponse)
    async def web_ui():
        """Serve the web UI."""
        web_dir = Path(__file__).parent.parent.parent.parent / "web"
        index = web_dir / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text())
        return HTMLResponse(_MINIMAL_UI)

    @app.get("/UI/Dashboard", response_class=HTMLResponse)
    async def dashboard():
        """Redirect for Jackett-compatible UI path."""
        return HTMLResponse(_MINIMAL_UI)

    return app


_MINIMAL_UI = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pyackett</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding: 20px 0; background: #f5f5f5; }
        .card { border: none; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
        .result-row:hover { background: #e9ecef; }
        #results-table { font-size: 0.85em; }
        .badge-public { background: #198754; }
        .badge-private { background: #dc3545; }
        .badge-semi-private { background: #fd7e14; }
        .indexer-card { transition: background .15s; }
        .indexer-card:hover { background: #f8f9fa; }
        .available-list { max-height: 400px; overflow-y: auto; }
        .available-item { cursor: pointer; padding: 8px 12px; border-bottom: 1px solid #eee; }
        .available-item:hover { background: #e2e6ea; }
        .copy-btn { cursor: pointer; opacity: .6; }
        .copy-btn:hover { opacity: 1; }
        .toast-container { position: fixed; bottom: 20px; right: 20px; z-index: 9999; }
        .settings-info { font-size: .85em; color: #6c757d; margin-bottom: 10px; }
        .settings-info a { color: #0d6efd; }
        #config-modal .modal-body { max-height: 70vh; overflow-y: auto; }
    </style>
</head>
<body>
<div class="container-fluid" style="max-width:1400px;">

    <!-- Header -->
    <div class="d-flex justify-content-between align-items-center mb-3">
        <h3 class="mb-0">Pyackett <small class="text-muted">v0.1.0</small></h3>
        <button class="btn btn-success btn-sm" data-bs-toggle="modal" data-bs-target="#add-modal">+ Add Indexer</button>
    </div>

    <!-- API Info -->
    <div class="card mb-3">
        <div class="card-body py-2">
            <div id="api-info" class="small">Loading...</div>
        </div>
    </div>

    <!-- Search -->
    <div class="card mb-3">
        <div class="card-body">
            <div class="input-group">
                <input type="text" class="form-control" id="search-input" placeholder="Search...">
                <select class="form-select" id="indexer-select" style="max-width:200px;">
                    <option value="all">All Indexers</option>
                </select>
                <button class="btn btn-primary" id="search-btn">Search</button>
            </div>
        </div>
    </div>

    <!-- Results -->
    <div id="results-container" class="card mb-3" style="display:none;">
        <div class="card-body p-0">
            <div class="p-2 border-bottom d-flex justify-content-between align-items-center">
                <strong>Results <span id="result-count" class="badge bg-secondary">0</span></strong>
            </div>
            <div style="max-height:500px; overflow-y:auto;">
                <table class="table table-sm table-striped mb-0" id="results-table">
                    <thead class="table-light"><tr>
                        <th>Tracker</th><th>Title</th><th>Size</th><th>S</th><th>P</th><th>Date</th><th></th>
                    </tr></thead>
                    <tbody id="results-body"></tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Configured Indexers -->
    <div class="card">
        <div class="card-body p-0">
            <div class="p-2 border-bottom d-flex justify-content-between align-items-center">
                <strong>Configured Indexers <span id="indexer-count" class="badge bg-secondary">0</span></strong>
            </div>
            <div id="indexers-list"></div>
            <div id="no-indexers" class="p-3 text-center text-muted" style="display:none;">
                No indexers configured. Click <strong>+ Add Indexer</strong> to get started.
            </div>
        </div>
    </div>
</div>

<!-- Add Indexer Modal -->
<div class="modal fade" id="add-modal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">Add Indexer</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <input type="text" class="form-control mb-2" id="add-filter" placeholder="Filter by name...">
        <div class="d-flex gap-2 mb-2">
            <button class="btn btn-sm btn-outline-secondary filter-type active" data-type="all">All</button>
            <button class="btn btn-sm btn-outline-success filter-type" data-type="public">Public</button>
            <button class="btn btn-sm btn-outline-danger filter-type" data-type="private">Private</button>
            <button class="btn btn-sm btn-outline-warning filter-type" data-type="semi-private">Semi-Private</button>
        </div>
        <div class="available-list" id="available-list"></div>
      </div>
    </div>
  </div>
</div>

<!-- Configure Indexer Modal -->
<div class="modal fade" id="config-modal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="config-modal-title">Configure Indexer</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p id="config-modal-desc" class="text-muted small"></p>
        <form id="config-form"></form>
        <div id="config-error" class="alert alert-danger mt-2" style="display:none;"></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary" id="config-save-btn">Save & Test</button>
      </div>
    </div>
  </div>
</div>

<!-- Toast -->
<div class="toast-container"><div id="toast" class="toast" role="alert">
    <div class="toast-body" id="toast-body"></div>
</div></div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
const API = '/api/v2.0';
let allIndexers = [];
let apiKey = '';

function formatSize(bytes) {
    if (!bytes) return '-';
    const units = ['B','KB','MB','GB','TB'];
    let i = 0, s = bytes;
    while (s >= 1024 && i < units.length-1) { s /= 1024; i++; }
    return s.toFixed(1)+' '+units[i];
}

function showToast(msg, type='success') {
    const el = document.getElementById('toast');
    const body = document.getElementById('toast-body');
    body.textContent = msg;
    el.className = 'toast text-bg-' + type;
    new bootstrap.Toast(el, {delay:3000}).show();
}

function typeBadge(type) {
    const cls = type === 'private' ? 'badge-private' : type === 'semi-private' ? 'badge-semi-private' : 'badge-public';
    return `<span class="badge ${cls}">${type}</span>`;
}

// ---- Load server config ----
async function loadConfig() {
    const resp = await fetch(API + '/server/config');
    const data = await resp.json();
    apiKey = data.api_key;
    document.getElementById('api-info').innerHTML =
        `API Key: <code id="api-key-display">${apiKey}</code> ` +
        `<span class="copy-btn" onclick="copyText('${apiKey}')" title="Copy">&#128203;</span> | ` +
        `Torznab: <code>${location.origin}/api/v2.0/indexers/<em>{id}</em>/results/torznab/</code> | ` +
        `${data.configured_indexers} / ${data.total_definitions} configured`;
}

function copyText(text) {
    navigator.clipboard.writeText(text);
    showToast('Copied to clipboard');
}

// ---- Configured indexers ----
async function loadConfigured() {
    const resp = await fetch(API + '/indexers?configured=true');
    const data = await resp.json();
    const list = document.getElementById('indexers-list');
    const select = document.getElementById('indexer-select');
    document.getElementById('indexer-count').textContent = data.length;
    document.getElementById('no-indexers').style.display = data.length ? 'none' : '';

    // Rebuild select
    select.innerHTML = '<option value="all">All Indexers</option>';
    data.forEach(idx => {
        select.innerHTML += `<option value="${idx.id}">${idx.name}</option>`;
    });

    list.innerHTML = '';
    data.forEach(idx => {
        const torznabUrl = `${location.origin}/api/v2.0/indexers/${idx.id}/results/torznab/`;
        list.innerHTML += `
        <div class="d-flex align-items-center justify-content-between px-3 py-2 border-bottom indexer-card">
            <div>
                <strong>${idx.name}</strong> ${typeBadge(idx.type)}
                <span class="text-muted ms-2 small">${idx.site_link || ''}</span>
            </div>
            <div class="d-flex gap-1">
                <button class="btn btn-outline-secondary btn-sm" onclick="copyText('${torznabUrl}')" title="Copy Torznab URL">URL</button>
                <button class="btn btn-outline-primary btn-sm" onclick="openConfig('${idx.id}')" title="Edit config">Edit</button>
                <button class="btn btn-outline-info btn-sm" onclick="testIndexer('${idx.id}')" title="Test search">Test</button>
                <button class="btn btn-outline-danger btn-sm" onclick="deleteIndexer('${idx.id}')" title="Remove">X</button>
            </div>
        </div>`;
    });
}

async function deleteIndexer(id) {
    if (!confirm('Remove indexer ' + id + '?')) return;
    await fetch(`${API}/indexers/${id}`, {method:'DELETE'});
    showToast('Removed ' + id);
    loadConfigured();
    loadConfig();
}

async function testIndexer(id) {
    showToast('Testing ' + id + '...', 'info');
    try {
        const resp = await fetch(`${API}/indexers/${id}/results?t=search&q=test`);
        const data = await resp.json();
        if (data.Results) {
            showToast(`${id}: ${data.Results.length} results returned`);
        } else {
            showToast(`${id}: ${data.error || 'unknown error'}`, 'danger');
        }
    } catch(e) { showToast('Test failed: ' + e, 'danger'); }
}

// ---- Available indexers (Add modal) ----
async function loadAvailable() {
    const resp = await fetch(API + '/indexers');
    allIndexers = await resp.json();
    renderAvailable();
}

function renderAvailable(filter='', typeFilter='all') {
    const list = document.getElementById('available-list');
    let items = allIndexers;
    if (filter) items = items.filter(i => i.name.toLowerCase().includes(filter.toLowerCase()) || i.id.toLowerCase().includes(filter.toLowerCase()));
    if (typeFilter !== 'all') items = items.filter(i => i.type === typeFilter);

    list.innerHTML = items.slice(0, 200).map(i =>
        `<div class="available-item d-flex justify-content-between align-items-center" onclick="openConfig('${i.id}')">
            <div>
                <strong>${i.name}</strong> ${typeBadge(i.type)}
                <span class="text-muted ms-2 small">${i.language || ''}</span>
                ${i.configured ? '<span class="badge bg-info ms-1">configured</span>' : ''}
            </div>
            <div class="small text-muted text-truncate ms-3" style="max-width:300px">${i.description || ''}</div>
        </div>`
    ).join('');
    if (items.length > 200) list.innerHTML += '<div class="p-2 text-muted text-center">Showing first 200 of ' + items.length + '...</div>';
    if (!items.length) list.innerHTML = '<div class="p-3 text-muted text-center">No matching indexers</div>';
}

document.getElementById('add-filter').addEventListener('input', e => {
    const active = document.querySelector('.filter-type.active');
    renderAvailable(e.target.value, active ? active.dataset.type : 'all');
});

document.querySelectorAll('.filter-type').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-type').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderAvailable(document.getElementById('add-filter').value, btn.dataset.type);
    });
});

// ---- Configure modal ----
let configIndexerId = null;

async function openConfig(id) {
    configIndexerId = id;
    // Close add modal if open
    const addModal = bootstrap.Modal.getInstance(document.getElementById('add-modal'));
    if (addModal) addModal.hide();

    const resp = await fetch(`${API}/indexers/${id}/config`);
    const data = await resp.json();

    document.getElementById('config-modal-title').textContent = data.name + (data.configured ? ' (edit)' : '');
    document.getElementById('config-modal-desc').innerHTML =
        `${data.description || ''}<br><a href="${data.site_link}" target="_blank">${data.site_link || ''}</a>`;
    document.getElementById('config-error').style.display = 'none';

    const form = document.getElementById('config-form');
    form.innerHTML = '';

    const settings = data.settings || [];
    if (!settings.length || settings.every(s => s.type && s.type.startsWith('info'))) {
        form.innerHTML = '<p class="text-muted">This is a public indexer with no required settings.</p>';
    }

    settings.forEach(s => {
        // Skip pure info fields
        if (s.type === 'info') {
            form.innerHTML += `<div class="settings-info">${s.default || s.label || ''}</div>`;
            return;
        }
        if (s.type && s.type.startsWith('info_')) {
            return; // Skip info_flaresolverr, info_cookie etc.
        }

        const id = 'cfg-' + s.name;
        const label = s.label || s.name;

        if (s.type === 'checkbox') {
            const checked = s.default === true || s.default === 'true' ? 'checked' : '';
            form.innerHTML += `
                <div class="form-check mb-2">
                    <input class="form-check-input cfg-input" type="checkbox" id="${id}" name="${s.name}" ${checked}>
                    <label class="form-check-label" for="${id}">${label}</label>
                </div>`;
        } else if (s.type === 'select' && s.options) {
            let opts = '';
            if (typeof s.options === 'object' && !Array.isArray(s.options)) {
                for (const [val, lbl] of Object.entries(s.options)) {
                    const sel = val === s.default ? 'selected' : '';
                    opts += `<option value="${val}" ${sel}>${lbl}</option>`;
                }
            }
            form.innerHTML += `
                <div class="mb-2">
                    <label class="form-label small" for="${id}">${label}</label>
                    <select class="form-select form-select-sm cfg-input" id="${id}" name="${s.name}">${opts}</select>
                </div>`;
        } else if (s.type === 'password') {
            form.innerHTML += `
                <div class="mb-2">
                    <label class="form-label small" for="${id}">${label}</label>
                    <input type="password" class="form-control form-control-sm cfg-input" id="${id}" name="${s.name}" value="${s.default || ''}" autocomplete="off">
                </div>`;
        } else {
            // text or unknown
            form.innerHTML += `
                <div class="mb-2">
                    <label class="form-label small" for="${id}">${label}</label>
                    <input type="text" class="form-control form-control-sm cfg-input" id="${id}" name="${s.name}" value="${s.default || ''}">
                </div>`;
        }
    });

    new bootstrap.Modal(document.getElementById('config-modal')).show();
}

document.getElementById('config-save-btn').addEventListener('click', async () => {
    const config = {};
    document.querySelectorAll('.cfg-input').forEach(el => {
        if (el.type === 'checkbox') {
            config[el.name] = el.checked;
        } else {
            config[el.name] = el.value;
        }
    });

    const errEl = document.getElementById('config-error');
    errEl.style.display = 'none';

    const btn = document.getElementById('config-save-btn');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
        const resp = await fetch(`${API}/indexers/${configIndexerId}/config`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config),
        });
        const data = await resp.json();

        if (data.status === 'ok') {
            bootstrap.Modal.getInstance(document.getElementById('config-modal')).hide();
            showToast(configIndexerId + ' configured successfully');
            loadConfigured();
            loadConfig();
            loadAvailable();
        } else {
            errEl.textContent = data.message || 'Configuration failed';
            errEl.style.display = '';
        }
    } catch(e) {
        errEl.textContent = 'Error: ' + e;
        errEl.style.display = '';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save & Test';
    }
});

// ---- Search ----
document.getElementById('search-btn').addEventListener('click', async () => {
    const q = document.getElementById('search-input').value;
    const idx = document.getElementById('indexer-select').value;
    const btn = document.getElementById('search-btn');
    btn.disabled = true; btn.textContent = 'Searching...';

    try {
        const resp = await fetch(`${API}/indexers/${idx}/results?t=search&q=${encodeURIComponent(q)}`);
        const data = await resp.json();
        const body = document.getElementById('results-body');
        body.innerHTML = '';
        document.getElementById('results-container').style.display = '';
        document.getElementById('result-count').textContent = (data.Results||[]).length;
        (data.Results||[]).forEach(r => {
            const date = r.PublishDate ? new Date(r.PublishDate).toLocaleDateString() : '-';
            const dl = r.Link || r.MagnetUri || '#';
            const fl = r.DownloadVolumeFactor === 0 ? '<span class="badge bg-success">FL</span>' : '';
            body.innerHTML += `<tr class="result-row">
                <td>${r.Tracker || '-'}</td>
                <td><a href="${r.Details || '#'}" target="_blank">${r.Title}</a> ${fl}</td>
                <td>${formatSize(r.Size)}</td>
                <td class="text-success">${r.Seeders ?? '-'}</td>
                <td class="text-danger">${r.Peers ?? '-'}</td>
                <td>${date}</td>
                <td><a href="${dl}" class="btn btn-sm btn-outline-primary py-0">DL</a></td>
            </tr>`;
        });
    } catch(e) { showToast('Search failed: '+e, 'danger'); }
    finally { btn.disabled = false; btn.textContent = 'Search'; }
});

document.getElementById('search-input').addEventListener('keypress', e => {
    if (e.key === 'Enter') document.getElementById('search-btn').click();
});

// ---- Init ----
loadConfig();
loadConfigured();
loadAvailable();
</script>
</body>
</html>"""
