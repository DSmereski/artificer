"""GET /v1/models — surface the M2.1 catalog to the app's about panel.

PUT/DELETE /v1/models/helpers/{role} — runtime per-helper model
overrides. Persisted to `state_dir/helper_overrides.json` so the choice
survives restart. Mutating routes rebuild the affected helper in the
live pool so the next coordinator run picks up the new model.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from gateway.deps import require_device, state
from gateway.helpers.factory import rebuild_helper


router = APIRouter(prefix="/v1", tags=["models"])


def _helper_dict(catalog, role: str) -> dict:
    """Render a helper entry with its current effective override."""
    base = asdict(catalog.helper(role))
    base["override"] = catalog.get_override(role)
    return base


@router.get("/models")
def list_models(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Return the model + helper catalog. The phone app's about panel
    renders this so the user can see Terry's toolkit."""
    st = state(request)
    catalog = st.model_catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="model catalog not loaded")

    return {
        "models": [
            {
                **asdict(catalog.model(mid)),
                "available": catalog.is_available(mid),
            }
            for mid in catalog.model_ids
        ],
        "helpers": [
            _helper_dict(catalog, role) for role in catalog.helper_roles
        ],
    }


def _rebuild_in_pool(st, role: str) -> None:
    """Construct a fresh Helper for the role using the active catalog
    state and swap it into `app_state.helpers`. The HiveCoordinator
    reads `app_state.helpers` per turn so the next dispatch sees the
    new model."""
    catalog = st.model_catalog
    skill_registry = getattr(st, "skill_registry", None)
    config = st.config

    def _vault_factory():
        from shared.vault_client import VaultClient
        return VaultClient(
            vault_path=config.vault_path,
            daemon_host=config.vault_writer.host,
            daemon_port=config.vault_writer.port,
        )

    new_helper = rebuild_helper(
        catalog,
        role,
        skill_registry=skill_registry,
        vault_client_factory=_vault_factory,
        router=getattr(st, "router", None),
    )
    if new_helper is None:
        raise HTTPException(
            status_code=500,
            detail=f"no helper class registered for role {role!r}",
        )
    st.helpers[role] = new_helper


@router.put("/models/helpers/{role}")
def set_helper_override(
    role: str,
    payload: dict = Body(...),
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    st = state(request)
    catalog = st.model_catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="model catalog not loaded")
    model_id = payload.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail="model_id required")
    try:
        catalog.set_override(role, model_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _rebuild_in_pool(st, role)
    return _helper_dict(catalog, role)


@router.delete("/models/helpers/{role}")
def clear_helper_override(
    role: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    st = state(request)
    catalog = st.model_catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="model catalog not loaded")
    if role not in catalog.helper_roles:
        raise HTTPException(status_code=404, detail=f"unknown role {role!r}")
    catalog.clear_override(role)
    _rebuild_in_pool(st, role)
    return _helper_dict(catalog, role)
