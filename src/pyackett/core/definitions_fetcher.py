"""Download Jackett YAML definitions from GitHub."""

from __future__ import annotations

import io
import json
import logging
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from curl_cffi.requests import Session

logger = logging.getLogger("pyackett.definitions_fetcher")

JACKETT_REPO = "Jackett/Jackett"
DEFINITIONS_PATH_IN_REPO = "src/Jackett.Common/Definitions/"

PROWLARR_REPO = "Prowlarr/Indexers"
PROWLARR_DEFINITIONS_PATH = "definitions/v10/"

# Don't check for updates more than once per this interval
CHECK_INTERVAL = timedelta(hours=12)


def _get_repo(source: str) -> tuple[str, str]:
    if source == "prowlarr":
        return PROWLARR_REPO, PROWLARR_DEFINITIONS_PATH
    return JACKETT_REPO, DEFINITIONS_PATH_IN_REPO


def _get_remote_sha(repo: str, branch: str) -> str | None:
    """Get the latest commit SHA from GitHub (lightweight API call)."""
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    try:
        with Session(timeout=15, impersonate="chrome") as client:
            resp = client.get(url, headers={"Accept": "application/vnd.github.sha"})
            if resp.status_code == 200:
                return resp.text.strip()
    except Exception as e:
        logger.debug(f"Failed to check remote SHA: {e}")
    return None


def _read_marker(marker_path: Path) -> dict:
    """Read the .fetched marker file."""
    if not marker_path.exists():
        return {}
    try:
        return json.loads(marker_path.read_text())
    except (json.JSONDecodeError, Exception):
        # Old-style marker (plain text), treat as no data
        return {}


def _write_marker(marker_path: Path, sha: str | None, count: int):
    """Write the .fetched marker with metadata."""
    marker_path.write_text(json.dumps({
        "sha": sha,
        "count": count,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


def fetch_definitions(
    output_dir: Path,
    source: str = "jackett",
    branch: str = "master",
) -> int:
    """Download YAML definitions from GitHub and extract to output_dir."""
    repo, defs_path = _get_repo(source)
    tarball_url = f"https://api.github.com/repos/{repo}/tarball/{branch}"

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading {source} definitions from GitHub ({branch})...")

    with Session(timeout=120, allow_redirects=True, impersonate="chrome") as client:
        resp = client.get(tarball_url)
        resp.raise_for_status()

    logger.info(f"Downloaded {len(resp.content) / 1024 / 1024:.1f} MB, extracting definitions...")

    count = 0
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = member.name.split("/", 1)
            if len(parts) < 2:
                continue
            relative = parts[1]
            if relative.startswith(defs_path) and relative.endswith(".yml"):
                filename = Path(relative).name
                f = tar.extractfile(member)
                if f is None:
                    continue
                dest = output_dir / filename
                dest.write_bytes(f.read())
                count += 1

    logger.info(f"Extracted {count} definition files to {output_dir}")
    return count


def fetch_definitions_if_needed(
    config_dir: Path,
    source: str = "jackett",
    branch: str = "master",
) -> Path:
    """Fetch definitions, checking GitHub for updates first.

    Logic:
      1. No definitions on disk -> download
      2. Have definitions, checked recently (< 12h) -> skip
      3. Have definitions, haven't checked recently -> compare SHA
         - Same SHA -> skip (touch marker timestamp)
         - Different SHA -> re-download

    Returns the path to the definitions directory.
    """
    repo, _ = _get_repo(source)
    defs_dir = config_dir / "definitions" / source
    marker_path = defs_dir / ".fetched"
    marker = _read_marker(marker_path)

    yml_count = len(list(defs_dir.glob("*.yml"))) if defs_dir.exists() else 0

    # Case 1: No definitions at all
    if yml_count == 0:
        sha = _get_remote_sha(repo, branch)
        count = fetch_definitions(defs_dir, source=source, branch=branch)
        if count > 0:
            defs_dir.mkdir(parents=True, exist_ok=True)
            _write_marker(marker_path, sha, count)
        return defs_dir

    # Case 2: Have definitions, checked recently
    fetched_at_str = marker.get("fetched_at")
    if fetched_at_str:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            if datetime.now(timezone.utc) - fetched_at < CHECK_INTERVAL:
                logger.info(f"Using cached {source} definitions ({yml_count} files, checked {fetched_at_str})")
                return defs_dir
        except (ValueError, TypeError):
            pass

    # Case 3: Check if upstream has changed
    saved_sha = marker.get("sha")
    remote_sha = _get_remote_sha(repo, branch)

    if remote_sha and saved_sha == remote_sha:
        logger.info(f"Definitions up to date ({yml_count} files, sha={saved_sha[:8]})")
        _write_marker(marker_path, saved_sha, yml_count)  # refresh timestamp
        return defs_dir

    if remote_sha and saved_sha and remote_sha != saved_sha:
        logger.info(f"Definitions outdated (local={saved_sha[:8]}, remote={remote_sha[:8]}), updating...")
    elif not remote_sha:
        logger.info(f"Couldn't check for updates, using cached definitions ({yml_count} files)")
        return defs_dir

    # Re-download
    count = fetch_definitions(defs_dir, source=source, branch=branch)
    if count > 0:
        _write_marker(marker_path, remote_sha, count)
    return defs_dir


def update_definitions(
    config_dir: Path,
    source: str = "jackett",
    branch: str = "master",
) -> Path:
    """Force re-download definitions regardless of cache state."""
    repo, _ = _get_repo(source)
    defs_dir = config_dir / "definitions" / source
    marker_path = defs_dir / ".fetched"

    if defs_dir.exists():
        for f in defs_dir.glob("*.yml"):
            f.unlink()
        if marker_path.exists():
            marker_path.unlink()

    sha = _get_remote_sha(repo, branch)
    count = fetch_definitions(defs_dir, source=source, branch=branch)
    if count > 0:
        defs_dir.mkdir(parents=True, exist_ok=True)
        _write_marker(marker_path, sha, count)
    return defs_dir
