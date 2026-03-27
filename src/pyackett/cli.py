"""CLI entry point for pyackett."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="pyackett",
        description="Pyackett - Python Torznab-compatible indexer proxy",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=9117, help="Port (default: 9117)")
    parser.add_argument("--config-dir", type=Path, default=None, help="Config directory")
    parser.add_argument("--definitions-dir", "-d", type=Path, default=None, help="YAML definitions directory (local path)")
    parser.add_argument("--api-key", default=None, help="API key (auto-generated if not set)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    # GitHub definitions source
    parser.add_argument(
        "--from-github",
        choices=["jackett", "prowlarr"],
        default=None,
        help="Download definitions from GitHub (jackett or prowlarr)",
    )
    parser.add_argument(
        "--branch",
        default="master",
        help="GitHub branch to download definitions from (default: master)",
    )
    parser.add_argument(
        "--update-definitions",
        action="store_true",
        help="Force re-download definitions from GitHub",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Proxy URL for indexer requests (e.g. socks5://127.0.0.1:1080, http://proxy:8080)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Total request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=5.0,
        help="Connection establishment timeout in seconds (default: 5)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    from pyackett import Pyackett

    pk = Pyackett(
        config_dir=args.config_dir,
        definitions_dir=args.definitions_dir,
        proxy=args.proxy,
        timeout=args.timeout,
        connect_timeout=args.connect_timeout,
    )

    if args.from_github:
        pk.load_definitions_from_github(
            source=args.from_github,
            branch=args.branch,
            force_update=args.update_definitions,
        )
    elif args.definitions_dir:
        pk.load_definitions(args.definitions_dir)
    else:
        # Try GitHub Jackett definitions as default fallback
        try:
            pk.load_definitions()
        except Exception:
            print("No local definitions found. Use --definitions-dir or --from-github jackett")
            sys.exit(1)

    print(f"Pyackett v0.1.0")
    print(f"Definitions: {len(pk.manager.definitions)} loaded")
    print(f"Configured: {len(pk.manager.configured_indexers)} indexers")
    print(f"Listening on http://{args.host}:{args.port}")
    print(f"Web UI: http://localhost:{args.port}/")

    pk.serve(host=args.host, port=args.port, api_key=args.api_key)


if __name__ == "__main__":
    main()
