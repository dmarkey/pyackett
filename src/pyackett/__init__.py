"""Pyackett - A Python Torznab-compatible indexer proxy."""

from pyackett.core.models import ReleaseInfo, TorznabQuery
from pyackett.core.manager import IndexerManager
from pyackett.pyackett import Pyackett

__version__ = "0.2.0"
__all__ = ["Pyackett", "IndexerManager", "ReleaseInfo", "TorznabQuery"]
