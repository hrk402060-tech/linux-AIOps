from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.asset import Asset
from src.models.knowledge import KnowledgeSyncRun
from src.models.monitoring import MonitoringEvent
from src.models.scan_task import ScanTask, ScanStatus
from src.models.security_event import EventDisposition
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding


def list_events(session: Session) -> list[dict[str, object]]:
    assets = {item.id: item for item in session.scalars(select(Asset))
              if item.lifecycle_status.value != 'archived'}
    events: list[dict[str, object]] = []

    def append(source: str, identity: str, title: str, severity: str,
               asset_id: str | None, occurred_at: object, recovered: bool = False,
               status: str = 'open') -> None:
        if asset_id is not None and asset_id not in assets:
            return
        asset = assets.get(asset_id)
        events.append(dict(id=f'{source}:{identity}', source=source, title=title,
                           severity=severity, asset_id=asset_id,
                           asset_name=asset.name if asset else '平台',
                           occurred_at=occurred_at, recovered=recovered, status=status))

    for item in session.scalars(select(MonitoringEvent)):
        append('monitoring', item.id, item.message, 'medium', item.asset_id,
               item.occurred_at, item.event_type == 'recovered')
    for item in session.scalars(select(ScanTask).where(ScanTask.status == ScanStatus.FAILED,
                                                     ScanTask.archived.is_(False))):
        for asset_id in item.asset_ids:
            append('scan', f'{item.id}:{asset_id}', f'扫描失败：{item.name}', 'high',
                   asset_id, item.finished_at or item.created_at)
    for item in session.scalars(select(KnowledgeSyncRun).where(KnowledgeSyncRun.status == 'failed')):
        append('knowledge', item.id, '知识库同步失败', 'high', None, item.started_at)
    alert_statuses = {
        'open': 'open', 'acknowledged': 'acknowledged',
        'in_progress': 'in_progress', 'resolved': 'closed', 'closed': 'closed',
    }
    for item in session.scalars(select(Alert)):
        append(
            'alert', item.id, item.title, item.severity, item.asset_id,
            item.received_at, False, alert_statuses.get(item.status, 'open'),
        )
    for case in session.scalars(select(VulnerabilityCase)):
        finding = session.scalar(select(VulnerabilityFinding).where(
            VulnerabilityFinding.case_id == case.id
        ).order_by(VulnerabilityFinding.detected_at.desc()).limit(1))
        if finding is not None:
            append('vulnerability', case.id, case.cve_id, finding.severity, case.asset_id,
                   finding.detected_at, case.status == 'fixed')
    latest = {item.event_id: item for item in session.scalars(
        select(EventDisposition).order_by(EventDisposition.id)
    )}
    for event in events:
        disposition = latest.get(event['id'])
        if disposition:
            event['status'] = disposition.status
    return sorted(events, key=lambda item: str(item['occurred_at']), reverse=True)
