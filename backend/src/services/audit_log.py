from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.auth import LoginAudit
from src.models.ai_provider import AIProviderAudit
from src.models.data_lifecycle import AssetCleanupAudit
from src.models.report import ScanReport
from src.models.scan_task_removal import ScanTaskRemoval
from src.models.security_event import EventDisposition
from src.models.terminal import TerminalCommandAudit
from src.models.vulnerability import VulnerabilityCase
from src.models.cmdb import CmdbChangeAudit
from src.models.alert_connector_audit import AlertConnectorAudit
from src.models.alert_notification import AlertNotificationAudit
from src.models.diagnostic_grant import DiagnosticGrantAudit
from src.models.alert import AlertTimeline
from src.models.ssh_host_key_audit import SshHostKeyAudit
from src.models.asset_access import UserAccessAudit


def list_entries(
    session: Session, visible_asset_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    items = [*_event_entries(session), *_cleanup_entries(session), *_asset_entries(session),
             *_vulnerability_entries(session), *_report_entries(session), *_command_entries(session),
             *_provider_entries(session), *_login_entries(session), *_cmdb_entries(session),
             *_connector_entries(session), *_notification_entries(session), *_diagnostic_entries(session),
             *_scan_removal_entries(session), *_recovery_entries(session),
             *_user_access_entries(session),
             *_ssh_host_key_entries(session)]
    if visible_asset_ids is not None:
        items = [
            item for item in items
            if item["source"] != "diagnostic_authorization"
            or str(item["target"]) in visible_asset_ids
        ]
    return sorted(items, key=lambda item: _time(item["occurred_at"]), reverse=True)


def _event_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(f"event:{item.id}", "event_disposition", item.status, item.operator,
                   item.reason, item.event_id, item.created_at)
            for item in session.scalars(select(EventDisposition))]


def _cleanup_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(f"cleanup:{item.id}", "asset_cleanup", "physically_deleted",
                   item.operator, item.reason, item.asset_name, item.deleted_at)
            for item in session.scalars(select(AssetCleanupAudit))]


def _asset_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(f"asset:{item.id}:{item.updated_at.isoformat()}", "asset_lifecycle",
                   item.lifecycle_status.value, item.lifecycle_operator or "system",
                   item.lifecycle_reason or "资产生命周期状态变更", item.name, item.updated_at)
            for item in session.scalars(select(Asset).where(Asset.lifecycle_operator.is_not(None)))]


def _vulnerability_entries(session: Session) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case in session.scalars(select(VulnerabilityCase)):
        for index, audit in enumerate(case.audit_history or []):
            rows.append(_entry(f"vulnerability:{case.id}:{index}", "vulnerability_disposition",
                               str(audit.get("status", "unknown")), str(audit.get("operator", "system")),
                               str(audit.get("reason", "")), f"{case.asset_name} · {case.cve_id}", audit.get("occurred_at")))
    return rows


def _report_entries(session: Session) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for report in session.scalars(select(ScanReport)):
        for index, audit in enumerate(report.audit_history or []):
            rows.append(_entry(f"report:{report.id}:{index}", "report", str(audit.get("action", "unknown")),
                               str(audit.get("operator", "system")), str(audit.get("detail", "")),
                               report.file_name or report.scan_task_name, audit.get("occurred_at")))
    return rows


def _command_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(f"command:{item.id}", "ai_command_approval", item.status,
                   item.reviewed_by or item.requested_by, item.purpose,
                   f"{item.risk_level} risk command approval", item.reviewed_at or item.created_at)
            for item in session.scalars(select(TerminalCommandAudit))]


def _provider_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(
        f"ai-provider:{item.id}", "ai_provider_config", item.action, item.operator,
        item.detail, item.provider_name, item.occurred_at,
    ) for item in session.scalars(select(AIProviderAudit))]


def _login_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(
        f"login:{item.id}", "authentication",
        "login_succeeded" if item.success else "login_failed",
        item.username, item.reason, item.client_ip or "unknown", item.occurred_at,
    ) for item in session.scalars(select(LoginAudit))]


def _cmdb_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(
        f"cmdb:{item.id}", "cmdb_change", item.action, item.operator_username,
        f"{item.entity_type} change; trace_id={item.trace_id}",
        f"{item.entity_type}:{item.entity_id}", item.occurred_at,
    ) for item in session.scalars(select(CmdbChangeAudit))]


def _connector_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(
        f"alert-connector:{item.id}", "alert_connector", item.action,
        item.operator, item.detail, item.connector_name, item.occurred_at,
    ) for item in session.scalars(select(AlertConnectorAudit))]


def _notification_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(
        f"notification:{item.id}", "alert_notification", item.action,
        item.operator, item.detail, item.notification_id or "smtp-admin", item.created_at,
    ) for item in session.scalars(select(AlertNotificationAudit))]


def _entry(identifier: str, source: str, action: str, operator: str, detail: str, target: str, occurred_at: Any) -> dict[str, object]:
    return {"id": identifier, "source": source, "action": action, "operator": operator,
            "detail": detail, "target": target, "occurred_at": _time(occurred_at)}


def _diagnostic_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(f'diagnostic:{item.id}', 'diagnostic_authorization', item.action,
                   item.operator, item.reason, item.asset_id, item.created_at)
            for item in session.scalars(select(DiagnosticGrantAudit).order_by(
                DiagnosticGrantAudit.created_at.desc()).limit(500))]


def _recovery_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(f'recovery:{item.id}', 'recovery_verification', item.event_type,
                   item.operator, item.note, item.alert_id, item.created_at)
            for item in session.scalars(select(AlertTimeline).where(
                AlertTimeline.event_type.like('recovery_%')).order_by(
                    AlertTimeline.created_at.desc(), AlertTimeline.id.desc()).limit(500))]


def _ssh_host_key_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(
        f'ssh-host-key:{item.id}', 'ssh_host_key', item.action, item.operator,
        f'{item.detail} · {item.key_type} · {item.fingerprint}',
        item.asset_name, item.occurred_at,
    ) for item in session.scalars(select(SshHostKeyAudit).order_by(
        SshHostKeyAudit.occurred_at.desc(), SshHostKeyAudit.id.desc()).limit(500))]


def _user_access_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(
        f"user-management:{item.id}", "user_management", item.action,
        item.operator_username, item.reason, item.username, item.occurred_at,
    ) for item in session.scalars(select(UserAccessAudit).order_by(
        UserAccessAudit.occurred_at.desc(), UserAccessAudit.id.desc()).limit(500))]


def _scan_removal_entries(session: Session) -> list[dict[str, object]]:
    return [_entry(
        f'scan-removal:{item.id}', 'scan_task_removal', 'removed', item.operator,
        item.reason, item.task_id, item.removed_at,
    ) for item in session.scalars(select(ScanTaskRemoval))]


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)
