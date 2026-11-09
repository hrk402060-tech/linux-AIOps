from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.services import asset_access
from src.services.audit_log import list_entries

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("")
def read_audit_log(
    source: str | None = None, operator: str | None = None, action: str | None = None,
    client_ip: str | None = Query(default=None, min_length=1, max_length=64),
    failure_reason: str | None = Query(default=None, min_length=1, max_length=64),
    login_success: bool | None = None,
    since: datetime | None = None, until: datetime | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    items = list_entries(session, asset_access.visible_asset_ids(session, principal))
    if any(value is not None for value in (client_ip, failure_reason, login_success)):
        items = [item for item in items if item["source"] == "authentication"]
    if source:
        items = [item for item in items if item["source"] == source]
    if operator:
        needle = operator.strip().casefold()
        items = [item for item in items if needle in str(item["operator"]).casefold()]
    if action:
        items = [item for item in items if item["action"] == action]
    if client_ip is not None:
        needle = client_ip.strip().casefold()
        items = [item for item in items if needle == str(item["target"]).casefold()]
    if failure_reason is not None:
        needle = failure_reason.strip().casefold()
        items = [
            item for item in items
            if item["action"] == "login_failed"
            and needle == str(item["detail"]).casefold()
        ]
    if login_success is not None:
        expected_action = "login_succeeded" if login_success else "login_failed"
        items = [item for item in items if item["action"] == expected_action]
    if since:
        items = [item for item in items if item["occurred_at"].timestamp() >= since.timestamp()]
    if until:
        items = [item for item in items if item["occurred_at"].timestamp() <= until.timestamp()]
    start = (page - 1) * page_size
    authentication = [item for item in items if item["source"] == "authentication"]
    return {
        "total": len(items),
        "items": items[start:start + page_size],
        "summary": {
            "authentication_total": len(authentication),
            "login_succeeded": sum(item["action"] == "login_succeeded" for item in authentication),
            "login_failed": sum(item["action"] == "login_failed" for item in authentication),
            "locked": sum(item["detail"] == "locked" for item in authentication),
            "mfa_failed": sum(
                item["detail"] in {
                    "mfa_required", "mfa_setup_required", "invalid_mfa",
                }
                for item in authentication
            ),
        },
    }
