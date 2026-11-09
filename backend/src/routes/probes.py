from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, authenticated_operator, require_admin, require_system_admin
from src.database import get_session
from src.schemas.probe_policy import ProbePolicyResponse, ProbePolicyUpdate
from src.schemas.probes import ProbeCreate, ProbeStatusUpdate
from src.services import asset_access, probe_policy, probes
from src.services.probe_access import ProbeAccessError

router = APIRouter(prefix="/probes", tags=["probes"])


@router.get("/policy", response_model=ProbePolicyResponse)
def read_policy(
    request: Request, _principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> ProbePolicyResponse:
    return ProbePolicyResponse.model_validate(
        probe_policy.read_policy(session, request.app.state.runtime_settings.probe_allowed_cidrs)
    )


@router.put("/policy", response_model=ProbePolicyResponse, dependencies=[Depends(require_system_admin)])
def update_policy(
    payload: ProbePolicyUpdate, request: Request, session: Session = Depends(get_session),
) -> ProbePolicyResponse:
    try:
        result = probe_policy.save_policy(
            session, payload.allowed_cidrs, authenticated_operator(request),
            request.app.state.runtime_settings.probe_allowed_cidrs,
        )
    except ValueError as exc:
        raise _error(exc) from exc
    return ProbePolicyResponse.model_validate(result)


def _error(exc: ValueError | LookupError | ProbeAccessError) -> HTTPException:
    code = 403 if isinstance(exc, ProbeAccessError) else (
        404 if isinstance(exc, LookupError) else 400
    )
    return HTTPException(status_code=code, detail=str(exc))


@router.get("")
def list_probes(
    service_id: str | None = Query(None, max_length=36),
    asset_id: str | None = Query(None, max_length=36),
    status: str | None = Query(None),
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return probes.list_probes(
            session, service_id, asset_id, status, offset, limit,
            asset_access.visible_asset_ids(session, principal),
        )
    except ValueError as exc:
        raise _error(exc) from exc


@router.post("", status_code=201)
def create_probe(
    payload: ProbeCreate, request: Request, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        allowed = probe_policy.resolve_allowed_cidrs(
            session, request.app.state.runtime_settings.probe_allowed_cidrs
        )
        return probes.create_probe(
            session, payload, authenticated_operator(request), allowed,
            asset_access.visible_asset_ids(session, principal),
        )
    except (ValueError, ProbeAccessError) as exc:
        raise _error(exc) from exc


@router.patch("/{probe_id}")
def set_probe_status(
    probe_id: str, payload: ProbeStatusUpdate, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return probes.set_probe_status(
            session, probe_id, payload.status, authenticated_operator(request),
            asset_access.visible_asset_ids(session, principal),
        )
    except (ValueError, LookupError, ProbeAccessError) as exc:
        raise _error(exc) from exc


@router.post("/{probe_id}/run", status_code=201)
def run_probe(
    probe_id: str, request: Request, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        allowed = probe_policy.resolve_allowed_cidrs(
            session, request.app.state.runtime_settings.probe_allowed_cidrs
        )
        return probes.run_probe(
            session, probe_id, authenticated_operator(request), allowed,
            asset_access.visible_asset_ids(session, principal),
        )
    except (ValueError, LookupError, ProbeAccessError) as exc:
        raise _error(exc) from exc


@router.get("/{probe_id}/runs")
def list_probe_runs(
    probe_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return probes.list_runs(
            session, probe_id, offset, limit, asset_access.visible_asset_ids(session, principal)
        )
    except (LookupError, ProbeAccessError) as exc:
        raise _error(exc) from exc
