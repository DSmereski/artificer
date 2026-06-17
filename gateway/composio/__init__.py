"""Composio integration shell (OpenSwarm Phase B).

Optional SaaS-action surface. Activates only when the
`COMPOSIO_API_KEY` environment variable is set AND the optional
`composio-core` package is importable. Otherwise every call returns
`{"error": "composio_unavailable", ...}` so the synthesizer can
surface a graceful explanation instead of crashing.

Mirrors the `ollama_probe.py` pattern: presence-detection at module
import, no hard runtime dep.
"""
