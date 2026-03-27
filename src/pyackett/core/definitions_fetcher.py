"""Download Jackett YAML definitions from GitHub."""

from __future__ import annotations

import io
import logging
import tarfile
from pathlib import Path

from curl_cffi.requests import Session

logger = logging.getLogger("pyackett.definitions_fetcher")

JACKETT_REPO = "Jackett/Jackett"
DEFINITIONS_PATH_IN_REPO = "src/Jackett.Common/Definitions/"
TARBALL_URL = f"https://api.github.com/repos/{JACKETT_REPO}/tarball/master"

# Also support Prowlarr definitions
PROWLARR_REPO = "Prowlarr/Indexers"
PROWLARR_DEFINITIONS_PATH = "definitions/v10/"
PROWLARR_TARBALL_URL = f"https://api.github.com/repos/{PROWLARR_REPO}/tarball/master"


def fetch_definitions(
    output_dir: Path,
    source: str = "jackett",
    branch: str = "master",
) -> int:
    """Download YAML definitions from GitHub and extract to output_dir.

    Downloads the repo tarball and extracts only the definitions directory.
    This is much faster than fetching files individually via the API.

    Args:
        output_dir: Directory to write .yml files into.
        source: "jackett" or "prowlarr".
        branch: Git branch to download from.

    Returns:
        Number of definition files extracted.
    """
    if source == "prowlarr":
        tarball_url = f"https://api.github.com/repos/{PROWLARR_REPO}/tarball/{branch}"
        defs_path = PROWLARR_DEFINITIONS_PATH
    else:
        tarball_url = f"https://api.github.com/repos/{JACKETT_REPO}/tarball/{branch}"
        defs_path = DEFINITIONS_PATH_IN_REPO

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {source} definitions from GitHub ({branch})...")

    with Session(timeout=120, allow_redirects=True, impersonate="chrome") as client:
        resp = client.get(tarball_url)
        resp.raise_for_status()

    logger.info(f"Downloaded {len(resp.content) / 1024 / 1024:.1f} MB, extracting definitions...")

    count = 0
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        for member in tar.getmembers():
            # Tarball has a prefix like "Jackett-Jackett-abc1234/"
            # We need to find the definitions path within that
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


def fetch_definitions_if_missing(
    config_dir: Path,
    source: str = "jackett",
    branch: str = "master",
) -> Path:
    """Fetch definitions into config_dir/definitions/ if not already present.

    Returns the path to the definitions directory.
    """
    defs_dir = config_dir / "definitions" / source
    marker = defs_dir / ".fetched"

    if marker.exists():
        # Check if we have any yml files
        yml_count = len(list(defs_dir.glob("*.yml")))
        if yml_count > 0:
            logger.info(f"Using cached {source} definitions ({yml_count} files)")
            return defs_dir

    count = fetch_definitions(defs_dir, source=source, branch=branch)
    if count > 0:
        defs_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"fetched {count} definitions")
    return defs_dir


def update_definitions(
    config_dir: Path,
    source: str = "jackett",
    branch: str = "master",
) -> Path:
    """Force re-download definitions (for updates).

    Returns the path to the definitions directory.
    """
    defs_dir = config_dir / "definitions" / source
    marker = defs_dir / ".fetched"
    if marker.exists():
        marker.unlink()

    # Clear existing files
    if defs_dir.exists():
        for f in defs_dir.glob("*.yml"):
            f.unlink()

    count = fetch_definitions(defs_dir, source=source, branch=branch)
    if count > 0:
        marker.write_text(f"fetched {count} definitions")
    return defs_dir
