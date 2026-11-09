import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.database import get_session
from src.models.alert import AlertConnector
from src.services import alert_ingest_runtime

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/runtime")
def read_connector_runtime(
    _admin: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        return alert_ingest_runtime.summarize(session)
    except SQLAlchemyError as exc:
        session.rollback()
        logger.warning("Alert connector runtime query failed")
        raise HTTPException(status_code=503, detail="接入运行状态暂不可用") from exc


@router.get("/{connector_id}/receipts")
def read_connector_receipts(
    connector_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    result: Literal["success", "failure"] | None = None,
    _admin: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        if session.get(AlertConnector, connector_id) is None:
            raise HTTPException(status_code=404, detail="告警连接器不存在")
        return alert_ingest_runtime.list_receipts(
            session, connector_id, offset, limit, result,
        )
    except SQLAlchemyError as exc:
        session.rollback()
        logger.warning("Alert connector receipt query failed")
        raise HTTPException(status_code=503, detail="接收记录暂不可用") from exc
