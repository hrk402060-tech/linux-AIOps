from datetime import datetime, timezone
import socket
from time import monotonic
from uuid import uuid4

import paramiko
from paramiko.ssh_exception import NoValidConnectionsError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from src.database import Base
from src.models.ai_agent import AgentRecommendation
from src.models.alert import Alert, RootCauseAnalysis
from src.models.asset import Asset, AssetEnvironment, AssetLifecycleStatus, AssetStatus
from src.models.diagnostic_grant import DiagnosticGrant, DiagnosticGrantAudit
from src.models.credential import Credential, CredentialType
from src.models.data_lifecycle import AssetCleanupAudit
from src.models.monitoring import (
    MonitoringAlertState, MonitoringEvent, MonitoringPolicy, MonitoringSnapshot,
)
from src.models.incident import Incident
from src.models.report import ScanReport
from src.models.recovery import RecoveryVerification
from src.models.scan_task import ScanStatus, ScanTask
from src.models.scan_task_removal import ScanTaskRemoval
from src.services.scan_task_lifecycle import ScanTaskConflictError, lock_for_write
from src.models.ssh_host_key import SshHostKey
from src.services.ssh_connection import open_client
from src.models.terminal import (
    TerminalAssistantAnalysis, TerminalCommandAudit, TerminalSession,
)
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.schemas.asset import (
    AssetCleanupRequest, AssetCreate, AssetLifecycleUpdate, AssetUpdate,
)
from src.services.crypto import CredentialVault


class AssetNameConflictError(ValueError):
    """Raised when an asset name is already in use."""


class CredentialReferenceError(ValueError):
    """Raised when an asset references a missing credential."""


class AssetInUseError(ValueError):
    """Raised when immutable history still references an asset."""


def list_assets(
    session: Session, lifecycle: str = "active", visible_ids: set[str] | None = None,
    unrestricted: bool = False,
) -> list[Asset]:
    statement = (
        select(Asset)
        .options(selectinload(Asset.credential))
        .order_by(Asset.updated_at.desc())
    )
    if lifecycle == "active":
        statement = statement.where(Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED)
    elif lifecycle != "all":
        try:
            statement = statement.where(
                Asset.lifecycle_status == AssetLifecycleStatus(lifecycle)
            )
        except ValueError as exc:
            raise ValueError("无效的资产生命周期筛选") from exc
    if not unrestricted:
        statement = statement.where(Asset.id.in_(visible_ids or set()))
    return list(session.scalars(statement))


def get_asset(session: Session, asset_id: str) -> Asset | None:
    statement = (
        select(Asset)
        .where(Asset.id == asset_id)
        .options(selectinload(Asset.credential))
    )
    return session.scalar(statement)


def create_asset(session: Session, payload: AssetCreate, vault: CredentialVault) -> Asset:
    values = payload.model_dump()
    credential_id = _resolve_inline_credential(session, values, payload.name, vault)
    values.pop('ssh_username', None)
    values.pop('ssh_credential_type', None)
    values.pop('ssh_secret', None)
    values['credential_id'] = credential_id
    _ensure_credential_exists(session, credential_id)
    asset = Asset(id=str(uuid4()), **values)
    session.add(asset)
    _commit_or_conflict(session)
    asset = _refresh_asset(session, asset.id)
    return check_asset(session, asset, vault)


def update_asset(session: Session, asset: Asset, payload: AssetUpdate, vault: CredentialVault) -> Asset:
    values = payload.model_dump(exclude_unset=True)
    if "host" in values or "ssh_port" in values:
        asset = session.scalar(select(Asset).where(Asset.id == asset.id)
                               .with_for_update().execution_options(populate_existing=True))
        target_changed = any(key in values and values[key] != getattr(asset, key)
                             for key in ("host", "ssh_port"))
        if target_changed and session.scalar(select(RecoveryVerification.id).where(
            RecoveryVerification.asset_id == asset.id,
            RecoveryVerification.active_alert_id.is_not(None),
        ).limit(1)):
            raise AssetInUseError("存在活动恢复验证，请先取消验证再修改服务器地址或 SSH 端口")
    if values.get("environment") == AssetEnvironment.TEST and asset.environment != AssetEnvironment.TEST:
        raise AssetInUseError("非测试资产不能改为测试环境，请新建独立测试资产")
    has_inline_credential = any(values.get(field) is not None for field in ('ssh_username', 'ssh_secret'))
    credential_id = _resolve_inline_credential(session, values, asset.name, vault) if has_inline_credential else None
    values.pop('ssh_username', None)
    values.pop('ssh_credential_type', None)
    values.pop('ssh_secret', None)
    if credential_id is not None:
        values['credential_id'] = credential_id
    elif values.get('credential_id') is None:
        values.pop('credential_id', None)
    if "credential_id" in values:
        _ensure_credential_exists(session, values["credential_id"])
    for field, value in values.items():
        setattr(asset, field, value)
    _commit_or_conflict(session)
    session.expire(asset, ["credential"])
    return _refresh_asset(session, asset.id)


def delete_asset(session: Session, asset: Asset) -> None:
    raise AssetInUseError(
        "普通业务数据不可物理删除，请归档资产；仅测试环境可在清理入口二次确认后删除"
    )


def update_lifecycle(
    session: Session, asset: Asset, payload: AssetLifecycleUpdate
) -> Asset:
    now = datetime.now(timezone.utc)
    asset.lifecycle_status = payload.status
    asset.lifecycle_reason = payload.reason
    asset.lifecycle_operator = payload.operator
    asset.archived_at = now if payload.status == AssetLifecycleStatus.ARCHIVED else None
    session.commit()
    return _refresh_asset(session, asset.id)


def cleanup_preview(session: Session, asset: Asset) -> dict[str, object]:
    tasks = _asset_tasks(session, asset.id)
    removals = _count(session, ScanTaskRemoval, ScanTaskRemoval.task_id.in_([item.id for item in tasks]))
    owned_ids = [item.id for item in tasks if item.asset_ids == [asset.id]]
    shared = len(tasks) - len(owned_ids)
    running = sum(item.status not in {
        ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED
    } for item in tasks)
    findings = _count(session, VulnerabilityFinding, VulnerabilityFinding.asset_id == asset.id)
    finding_ids = list(session.scalars(
        select(VulnerabilityFinding.id).where(VulnerabilityFinding.asset_id == asset.id)
    ))
    reports = 0 if not owned_ids else _count(
        session, ScanReport, ScanReport.scan_task_id.in_(owned_ids)
    )
    monitoring = sum((
        _count(session, MonitoringSnapshot, MonitoringSnapshot.asset_id == asset.id),
        _count(session, MonitoringAlertState, MonitoringAlertState.asset_id == asset.id),
        _count(session, MonitoringEvent, MonitoringEvent.asset_id == asset.id),
        _count(session, MonitoringPolicy, MonitoringPolicy.asset_id == asset.id),
    ))
    active_terminals = _count(
        session, TerminalSession,
        TerminalSession.asset_id == asset.id,
        TerminalSession.status.in_(("waiting", "connected")),
    )
    incidents = _count(session, Incident, Incident.asset_id == asset.id)
    alert_records = sum((
        _count(session, Alert, Alert.asset_id == asset.id),
        _count(session, RootCauseAnalysis, RootCauseAnalysis.asset_id == asset.id),
    ))
    allowed = (
        _count(session, DiagnosticGrant, DiagnosticGrant.asset_id == asset.id) == 0
        and _count(session, DiagnosticGrantAudit, DiagnosticGrantAudit.asset_id == asset.id) == 0
        and asset.environment == AssetEnvironment.TEST
        and shared == 0
        and running == 0
        and active_terminals == 0
        and incidents == 0
        and alert_records == 0
        and removals == 0
    )
    reason = None
    if asset.environment != AssetEnvironment.TEST:
        reason = "仅 test 环境资产允许物理清理"
    elif shared:
        reason = "资产存在包含其他主机的共享扫描任务，不能清理"
    elif running:
        reason = "资产存在未结束的扫描任务，不能清理"
    elif active_terminals:
        reason = "资产存在活跃终端会话，不能清理"
    elif incidents:
        reason = "资产存在不可变故障处置证据，不能清理"
    elif alert_records:
        reason = "资产存在不可变告警或根因分析证据，不能清理"
    elif removals:
        reason = '资产存在扫描删除审计及保留证据，不能物理清理；请归档资产'
    elif not allowed:
        reason = '资产存在只读诊断授权或不可变审计，不能清理；请归档资产'
    return {
        "asset_id": asset.id, "allowed": allowed, "reason": reason,
        "confirmation_phrase": f"DELETE {asset.id}",
        "scan_tasks": len(tasks), "shared_scan_tasks": shared,
        "vulnerability_findings": findings,
        "vulnerability_cases": _count(
            session, VulnerabilityCase, VulnerabilityCase.asset_id == asset.id
        ),
        "reports": reports, "monitoring_records": monitoring,
        "terminal_sessions": _count(
            session, TerminalSession, TerminalSession.asset_id == asset.id
        ),
        "alert_records": alert_records,
        "agent_recommendations": 0 if not finding_ids else _count(
            session, AgentRecommendation, AgentRecommendation.finding_id.in_(finding_ids)
        ),
    }


def cleanup_asset(
    session: Session, asset: Asset, payload: AssetCleanupRequest
) -> AssetCleanupAudit:
    try:
        for task in sorted(_asset_tasks(session, asset.id), key=lambda item: item.id):
            lock_for_write(session, task)
    except ScanTaskConflictError as exc:
        raise AssetInUseError('资产存在扫描删除审计及保留证据，不能物理清理') from exc
    preview = cleanup_preview(session, asset)
    if not preview["allowed"]:
        raise AssetInUseError(str(preview["reason"]))
    if payload.confirmation.strip() != preview["confirmation_phrase"]:
        raise AssetInUseError("二次确认文本不匹配")
    task_ids = [item.id for item in _asset_tasks(session, asset.id)]
    finding_ids = list(session.scalars(
        select(VulnerabilityFinding.id).where(VulnerabilityFinding.asset_id == asset.id)
    ))
    terminal_ids = list(session.scalars(
        select(TerminalSession.id).where(TerminalSession.asset_id == asset.id)
    ))
    audit = AssetCleanupAudit(
        id=str(uuid4()), asset_id=asset.id, asset_name=asset.name,
        environment=asset.environment.value, reason=payload.reason, operator=payload.operator,
        evidence_snapshot=preview,
    )
    session.add(audit)
    if finding_ids:
        session.execute(delete(AgentRecommendation).where(
            AgentRecommendation.finding_id.in_(finding_ids)
        ))
    session.execute(delete(VulnerabilityFinding).where(VulnerabilityFinding.asset_id == asset.id))
    session.execute(delete(VulnerabilityCase).where(VulnerabilityCase.asset_id == asset.id))
    if task_ids:
        session.execute(delete(ScanReport).where(ScanReport.scan_task_id.in_(task_ids)))
    if terminal_ids:
        session.execute(delete(TerminalCommandAudit).where(
            TerminalCommandAudit.terminal_session_id.in_(terminal_ids)
        ))
        session.execute(delete(TerminalAssistantAnalysis).where(
            TerminalAssistantAnalysis.terminal_session_id.in_(terminal_ids)
        ))
        session.execute(delete(TerminalSession).where(TerminalSession.id.in_(terminal_ids)))
    for model in (MonitoringAlertState, MonitoringEvent, MonitoringSnapshot, MonitoringPolicy):
        session.execute(delete(model).where(model.asset_id == asset.id))
    session.execute(delete(SshHostKey).where(SshHostKey.asset_id == asset.id))
    if task_ids:
        session.execute(delete(ScanTask).where(ScanTask.id.in_(task_ids)))
    session.delete(asset)
    session.commit()
    session.refresh(audit)
    return audit


def list_cleanup_audits(session: Session) -> list[AssetCleanupAudit]:
    return list(session.scalars(
        select(AssetCleanupAudit).order_by(AssetCleanupAudit.deleted_at.desc()).limit(200)
    ))


def check_asset(
    session: Session, asset: Asset, vault: CredentialVault, timeout: float = 3.0
) -> Asset:
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise AssetInUseError("已停用或归档资产不能执行连通性检查")
    started = monotonic()
    message = "SSH 登录失败"
    reachable = False
    if asset.credential is None:
        message = "未配置 SSH 凭据"
    else:
        credential = asset.credential
        try:
            secret = vault.decrypt(
                credential.encrypted_secret, credential.nonce, credential.id,
                credential.credential_type.value, credential.key_version,
            )
            client = open_client(asset, credential, secret, timeout)
            client.close()
            reachable = True
            message = "SSH 登录成功"
            credential.last_used_at = datetime.now(timezone.utc)
        except paramiko.AuthenticationException:
            message = "SSH 登录认证失败：请检查用户名、密码或私钥"
        except NoValidConnectionsError:
            message = "SSH 端口无法连接：请检查服务器地址、防火墙和端口"
        except (TimeoutError, socket.timeout):
            message = "SSH 连接超时：请检查服务器是否在线及端口是否可达"
        except OSError as exc:
            message = f"SSH 网络连接失败：{type(exc).__name__}"
        except paramiko.SSHException as exc:
            message = f"SSH 连接失败：{type(exc).__name__}"
        except Exception:
            message = "SSH 凭据解密失败，请重新保存认证信息"
    asset.status = AssetStatus.ONLINE if reachable else AssetStatus.OFFLINE
    asset.latency_ms = max(1, round((monotonic() - started) * 1000)) if reachable else None
    asset.check_message = message[:255]
    asset.last_checked_at = datetime.now(timezone.utc)
    session.commit()
    return _refresh_asset(session, asset.id)


def check_tcp_connectivity(
    host: str, port: int, timeout: float
) -> tuple[bool, int | None, str]:
    started = monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = max(1, round((monotonic() - started) * 1000))
            return True, elapsed, "SSH 端口可达"
    except (OSError, TimeoutError) as exc:
        return False, None, f"SSH 端口不可达：{type(exc).__name__}"


def _ensure_credential_exists(session: Session, credential_id: str | None) -> None:
    if credential_id is not None and session.get(Credential, credential_id) is None:
        raise CredentialReferenceError("Credential not found")


def _resolve_inline_credential(
    session: Session, values: dict[str, object], asset_name: str, vault: CredentialVault,
) -> str | None:
    secret = values.get("ssh_secret")
    username = values.get("ssh_username")
    if not secret and not username:
        return values.get("credential_id") if isinstance(values.get("credential_id"), str) else None
    if not isinstance(secret, str) or not isinstance(username, str):
        raise CredentialReferenceError("SSH 用户名和密码或私钥必须同时填写")
    credential_id = str(uuid4())
    credential_type = values.get("ssh_credential_type") or CredentialType.PASSWORD
    if isinstance(credential_type, str):
        credential_type = CredentialType(credential_type)
    encrypted, nonce = vault.encrypt(secret, credential_id, credential_type.value)
    credential_name = f"资产 SSH · {asset_name[:80]} · {credential_id[:8]}"
    session.add(Credential(
        id=credential_id, name=credential_name,
        credential_type=credential_type, username=username, encrypted_secret=encrypted,
        nonce=nonce, key_version=vault.key_version,
    ))
    session.flush()
    return credential_id


def _commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AssetNameConflictError("Asset name already exists") from exc


def _refresh_asset(session: Session, asset_id: str) -> Asset:
    asset = get_asset(session, asset_id)
    if asset is None:
        raise RuntimeError("Asset disappeared after database commit")
    return asset


def _asset_tasks(session: Session, asset_id: str) -> list[ScanTask]:
    return [
        task for task in session.scalars(select(ScanTask)) if asset_id in task.asset_ids
    ]


def _count(
    session: Session, model: type[Base], condition: object, *conditions: object
) -> int:
    return int(session.scalar(
        select(func.count()).select_from(model).where(condition, *conditions)
    ) or 0)
