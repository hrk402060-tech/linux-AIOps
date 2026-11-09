import logging
from uuid import uuid4
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.cmdb import CmdbApplication, CmdbBusiness, CmdbChangeAudit, CmdbLifecycle, CmdbService, CmdbServiceApi, CmdbServiceAsset, ServiceAssetStatus
from src.models.probes import HttpProbe, HttpProbeRun
from src.schemas.probes import ProbeCreate
from src.services.probe_access import require_context, scope_context
from src.services.probe_http import check_http, validate_url

logger = logging.getLogger(__name__)


def _commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Probe transaction failed")
        raise


def _context(session: Session, service_id: str, asset_id: str, api_id: str | None) -> tuple[CmdbBusiness, CmdbApplication, CmdbService, Asset]:
    service = session.get(CmdbService, service_id)
    application = session.get(CmdbApplication, service.application_id) if service else None
    business = session.get(CmdbBusiness, application.business_id) if application else None
    asset = session.get(Asset, asset_id)
    if not all(item and item.lifecycle_status == CmdbLifecycle.ACTIVE for item in (service, application, business)):
        raise ValueError("An active CMDB business, application and service are required")
    if asset is None or asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise ValueError("An enabled asset is required")
    relation = session.scalar(select(CmdbServiceAsset).where(CmdbServiceAsset.service_id == service_id, CmdbServiceAsset.asset_id == asset_id, CmdbServiceAsset.status == ServiceAssetStatus.ACTIVE))
    if relation is None:
        raise ValueError("Asset must be actively linked to the selected service")
    if api_id:
        api = session.get(CmdbServiceApi, api_id)
        if api is None or api.service_id != service_id or api.lifecycle_status != CmdbLifecycle.ACTIVE:
            raise ValueError("API must be active and belong to the selected service")
    return business, application, service, asset


def _audit(session: Session, probe: HttpProbe, action: str, operator: str, trace_id: str, snapshot: dict[str, object]) -> None:
    session.add(CmdbChangeAudit(id=str(uuid4()), entity_type="http_probe", entity_id=probe.id, action=action, operator_username=operator, trace_id=trace_id, after_snapshot=snapshot))


def serialize_run(run: HttpProbeRun) -> dict[str, object]:
    return {column.name: getattr(run, column.name) for column in run.__table__.columns}


def _latest_run(
    session: Session, probe_id: str, visible_asset_ids: set[str] | None,
) -> HttpProbeRun | None:
    statement = scope_context(
        select(HttpProbeRun).where(HttpProbeRun.probe_id == probe_id),
        HttpProbeRun, visible_asset_ids,
    )
    return session.scalar(statement.order_by(
        HttpProbeRun.checked_at.desc(), HttpProbeRun.id.desc()
    ).limit(1))


def serialize_probe(
    session: Session, probe: HttpProbe, visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    result = {column.name: getattr(probe, column.name) for column in probe.__table__.columns}
    latest = _latest_run(session, probe.id, visible_asset_ids)
    service = session.get(CmdbService, probe.service_id)
    asset = session.get(Asset, probe.asset_id)
    result.update(service_name=service.name if service else probe.service_id, asset_name=asset.name if asset else probe.asset_id, asset_host=asset.host if asset else "", latest_run=serialize_run(latest) if latest else None)
    return result


def create_probe(
    session: Session, payload: ProbeCreate, operator: str, allowed_cidrs: str = "",
    visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    require_context(session, payload.service_id, payload.asset_id, payload.api_id, visible_asset_ids)
    _, _, _, asset = _context(session, payload.service_id, payload.asset_id, payload.api_id)
    validate_url(payload.url, asset.host, allowed_cidrs)
    _validate_api(session, payload.api_id, payload.url, payload.method)
    probe = HttpProbe(id=str(uuid4()), **payload.model_dump(), operator_username=operator, status="active")
    session.add(probe)
    _audit(session, probe, "create", operator, str(uuid4()), payload.model_dump())
    _commit(session)
    return serialize_probe(session, probe, visible_asset_ids)


def _validate_api(session: Session, api_id: str | None, url: str, method: str) -> None:
    if api_id:
        api = session.get(CmdbServiceApi, api_id)
        if api is None or api.http_method != method or api.path != (urlsplit(url).path or "/"):
            raise ValueError("Probe method and path must match the selected GET/HEAD API")


def list_probes(
    session: Session, service_id: str | None, asset_id: str | None, status: str | None,
    offset: int, limit: int, visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    statement = scope_context(select(HttpProbe), HttpProbe, visible_asset_ids)
    if service_id:
        statement = statement.where(HttpProbe.service_id == service_id)
    if asset_id:
        statement = statement.where(HttpProbe.asset_id == asset_id)
    if status:
        if status not in {"active", "disabled"}:
            raise ValueError("Invalid probe status")
        statement = statement.where(HttpProbe.status == status)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(
        statement.order_by(HttpProbe.created_at.desc(), HttpProbe.id.desc())
        .offset(offset).limit(limit)
    ).all()
    return {
        "items": [serialize_probe(session, row, visible_asset_ids) for row in rows],
        "total": total,
    }


def get_probe(
    session: Session, probe_id: str, visible_asset_ids: set[str] | None = None,
) -> HttpProbe:
    probe = session.get(HttpProbe, probe_id)
    if probe is None:
        raise LookupError("Probe not found")
    require_context(session, probe.service_id, probe.asset_id, probe.api_id, visible_asset_ids)
    return probe


def run_probe(
    session: Session, probe_id: str, operator: str, allowed_cidrs: str = "",
    visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    probe = get_probe(session, probe_id, visible_asset_ids)
    if probe.status != "active":
        raise ValueError("Disabled probes cannot run")
    business, application, service, asset = _context(session, probe.service_id, probe.asset_id, probe.api_id)
    validate_url(probe.url, asset.host, allowed_cidrs)
    _validate_api(session, probe.api_id, probe.url, probe.method)
    snapshot = {key: getattr(probe, key) for key in ("name", "url", "method", "expected_status", "timeout_seconds")}
    result = check_http(probe.url, probe.method, probe.expected_status, probe.timeout_seconds)
    trace_id = str(uuid4())
    run = HttpProbeRun(id=str(uuid4()), probe_id=probe.id, business_id=business.id, application_id=application.id, service_id=service.id, api_id=probe.api_id, asset_id=asset.id, config_snapshot=snapshot, operator_username=operator, trace_id=trace_id, **result)
    session.add(run)
    _audit(session, probe, "run", operator, trace_id, {"run_id": run.id, "status": run.status})
    _commit(session)
    return serialize_run(run)


def list_runs(
    session: Session, probe_id: str, offset: int, limit: int,
    visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    get_probe(session, probe_id, visible_asset_ids)
    statement = scope_context(
        select(HttpProbeRun).where(HttpProbeRun.probe_id == probe_id),
        HttpProbeRun, visible_asset_ids,
    )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(statement.order_by(
        HttpProbeRun.checked_at.desc(), HttpProbeRun.id.desc()
    ).offset(offset).limit(limit)).all()
    return {"items": [serialize_run(row) for row in rows], "total": total}


def set_probe_status(
    session: Session, probe_id: str, status: str, operator: str,
    visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    probe = get_probe(session, probe_id, visible_asset_ids)
    previous = probe.status
    probe.status = status
    _audit(session, probe, "status", operator, str(uuid4()), {"before": previous, "after": status})
    _commit(session)
    return serialize_probe(session, probe, visible_asset_ids)
