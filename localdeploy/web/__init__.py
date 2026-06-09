"""Additive web UI and control-plane endpoints for LocalDeploy.

This package is mounted only when ``ENABLE_WEB_UI`` is true (the default). It
never changes the behavior of the existing API: every route lives under a new
prefix (``/system/*``, ``/registry/*``, ``/models/*``) so it cannot collide
with the original endpoints in ``api_server.py``.

Feature routers are included here as each implementation step lands.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

# --- feature routers (registered as steps land) ------------------------------
from . import hardware  # noqa: E402  (import after router exists)

router.include_router(hardware.router)

from . import fit  # noqa: E402

router.include_router(fit.router)

from . import registry  # noqa: E402

router.include_router(registry.router)

from . import models  # noqa: E402

router.include_router(models.router)
