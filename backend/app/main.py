"""FastAPI application entrypoint.

Phase 1 stub. Implemented in Phase 2: constructs the FastAPI app, mounts the
routers from `app.api`, wires up startup/shutdown hooks for the ModelManager,
the tool registry, and the memory stores, and binds to 127.0.0.1 only with a
per-session auth token (see docs/ARCHITECTURE.md section 8).
"""

from __future__ import annotations


def create_app():
    """Construct and return the FastAPI application.

    Not implemented until Phase 2.
    """
    raise NotImplementedError("create_app() is implemented in Phase 2 (backend).")
