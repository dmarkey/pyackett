"""Pyackett - A Python Torznab-compatible indexer proxy."""

# Canonical version — defined before submodule imports so anything that does
# `from pyackett import __version__` during import stays safe.
__version__ = "0.4.0"

from pyackett.core.models import ReleaseInfo, TorznabQuery
from pyackett.core.manager import IndexerManager
from pyackett.pyackett import Pyackett

__all__ = ["Pyackett", "IndexerManager", "ReleaseInfo", "TorznabQuery"]
