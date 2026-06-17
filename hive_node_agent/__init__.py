"""Hive node agent — runs on a paired compute node.

Phase 1 ships: probe, pairing, heartbeat. Runtimes, worker, updater
arrive in Phase 2+.
"""

from hive_node_agent.version import __version__

__all__ = ["__version__"]
