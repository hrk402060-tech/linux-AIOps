from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, authenticated_operator, require_admin, require_system_admin
from src.database import get_session
from src.models.alert import Alert
from src.schemas.alert_notification import NotificationConfigUpdate, NotificationResponse
from src.services import asset_access
from src.services import alert_notification_config as config
from src.services import alert_notifications as notifications

router = APIRouter(tags=['alert-notifications'])


@router.get('/alert-notification-config')
def read_configuration(
    session: Session = Depends(get_session), _principal: AuthPrincipal = Depends(require_admin),
) -> dict[str, object]:
    return config.serialize(config.get_record(session))


@router.patch('/alert-notification-config')
def update_configuration(
    payload: NotificationConfigUpdate, request: Request, session: Session = Depends(get_session),
    _principal: AuthPrincipal = Depends(require_system_admin),
) -> dict[str, object]:
    try:
        item = config.save_config(session, payload, authenticated_operator(request), request.app.state.credential_vault)
    except config.NotificationConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return config.serialize(item)


@router.get('/alert-notifications')
def list_deliveries(
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> dict[str, object]:
    return notifications.list_notifications(
        session, offset, limit, asset_access.visible_asset_ids(session, principal)
    )


@router.get('/alerts/{alert_id}/notifications')
def read_alert_deliveries(
    alert_id: str, session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> dict[str, object]:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail='Alert not found')
    asset_access.require_asset(session, principal, alert.asset_id)
    return notifications.list_for_alert(session, alert_id)


@router.post('/alert-notifications/{notification_id}/retry', response_model=NotificationResponse)
def retry_delivery(
    notification_id: str, request: Request, session: Session = Depends(get_session),
    _principal: AuthPrincipal = Depends(require_system_admin),
) -> NotificationResponse:
    try:
        item = notifications.retry_notification(session, notification_id, authenticated_operator(request))
    except (notifications.NotificationNotFoundError, notifications.NotificationConflictError) as exc:
        raise _notification_error(exc) from exc
    return NotificationResponse.model_validate(item)


@router.post('/alert-notifications/{notification_id}/acknowledge', response_model=NotificationResponse)
def acknowledge_delivery(
    notification_id: str, request: Request, session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> NotificationResponse:
    if principal.auth_type != 'session' or principal.user_id is None or principal.role not in {'admin', 'operator'}:
        raise HTTPException(status_code=403, detail='通知人工确认必须使用管理员或操作员的用户登录会话')
    try:
        item = notifications.get_notification(session, notification_id)
        if item.alert_id is not None:
            alert = session.get(Alert, item.alert_id)
            if alert is None:
                raise HTTPException(status_code=404, detail='Alert not found')
            asset_access.require_asset(session, principal, alert.asset_id)
        item = notifications.acknowledge(session, notification_id, authenticated_operator(request))
    except (notifications.NotificationNotFoundError, notifications.NotificationConflictError) as exc:
        raise _notification_error(exc) from exc
    return NotificationResponse.model_validate(item)


def _notification_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, notifications.NotificationNotFoundError) else 409, detail=str(exc))
