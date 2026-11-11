from datetime import datetime, timezone
import logging

from cryptography.exceptions import InvalidTag
import paramiko
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus, OsFamily
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.schemas.vulnerability import VulnerabilityDispositionUpdate
from src.services import rpm_patch, vulnerabilities
from src.services.crypto import CredentialVault
from src.services.ssh_connection import open_client

CONFIRMATION = "确认更新软件包"
_RPM_SYSTEMS = {
    OsFamily.AUTO, OsFamily.OPENEULER, OsFamily.RHEL, OsFamily.CENTOS,
    OsFamily.ROCKY, OsFamily.ALMALINUX, OsFamily.ANOLIS, OsFamily.KYLIN, OsFamily.UOS,
}
_log = logging.getLogger(__name__)


def availability_issue(asset: Asset | None) -> str | None:
    if asset is None or asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        return "仅可更新已启用的服务器"
    if asset.os_family not in _RPM_SYSTEMS:
        return "仅支持使用 YUM/DNF 的 RPM 系统"
    if asset.credential_id is None:
        return "请先在服务器资产中配置 SSH 登录"
    return None


def execute(
    session: Session, vault: CredentialVault, finding: VulnerabilityFinding, operator: str, reason: str,
) -> dict[str, object]:
    asset, case = _lock_target(session, finding)
    issue = availability_issue(asset)
    if issue or asset is None:
        raise HTTPException(409, issue or "服务器不存在")
    outcome, detail = _apply(session, vault, asset, finding)
    case.audit_history = [*case.audit_history, {
        "status": f"patch_{outcome}", "operator": operator, "reason": f"{reason}；{detail}",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "verification_scan_id": None,
        "finding_id": finding.id, "package": finding.package_name, "target_version": finding.fixed_version,
    }]
    if outcome == "blocked":
        session.commit()
        raise HTTPException(409, detail)
    result = vulnerabilities.update_disposition(session, finding, VulnerabilityDispositionUpdate(
        status="pending_verification", operator=operator, reason=f"{detail}；请重新扫描验证",
    ))
    if outcome == "uncertain":
        raise HTTPException(409, detail)
    return {**result, "status": "pending_verification", "execution_enabled": False}


def _lock_target(session: Session, finding: VulnerabilityFinding) -> tuple[Asset | None, VulnerabilityCase]:
    try:
        asset = session.get(Asset, finding.asset_id, with_for_update={"nowait": True}, populate_existing=True)
        case = session.get(VulnerabilityCase, finding.case_id, with_for_update={"nowait": True}, populate_existing=True) if finding.case_id else None
    except OperationalError as error:
        _log.warning("Could not lock patch target %s (%s)", finding.id, type(error).__name__)
        raise HTTPException(409, "服务器或漏洞记录正在处理，请刷新后再试") from error
    latest = session.scalar(select(VulnerabilityFinding.id).where(
        VulnerabilityFinding.asset_id == finding.asset_id, VulnerabilityFinding.cve_id == finding.cve_id,
    ).order_by(VulnerabilityFinding.detected_at.desc(), VulnerabilityFinding.id.desc()).limit(1))
    if case is None or case.status != "open" or latest != finding.id:
        raise HTTPException(409, "补丁建议已变化，请刷新后选择最新待处理建议")
    if not rpm_patch.is_rpm_advice(finding):
        raise HTTPException(409, "缺少已确认的 RPM 修复版本证据，请人工处理")
    return asset, case


def _apply(
    session: Session, vault: CredentialVault, asset: Asset, finding: VulnerabilityFinding,
) -> tuple[str, str]:
    credential = asset.credential
    if credential is None:
        return "blocked", "请先在服务器资产中配置 SSH 登录"
    try:
        secret = vault.decrypt(
            credential.encrypted_secret, credential.nonce, credential.id,
            credential.credential_type.value, credential.key_version,
        )
        with open_client(asset, credential, secret, timeout=6) as client:
            rpm_patch.update_package(client, finding.package_name, finding.installed_version, finding.fixed_version)
    except rpm_patch.PatchUpdateError as error:
        return ("uncertain" if error.verify_required else "blocked"), str(error)
    except paramiko.BadHostKeyException:
        return "blocked", "SSH 主机身份已变化，已阻止更新"
    except (OSError, paramiko.SSHException, ValueError, InvalidTag) as error:
        _log.warning("Patch preflight failed for %s (%s)", finding.id, type(error).__name__)
        return "blocked", "无法验证 SSH 主机身份、登录或读取 RPM 信息，未开始更新"
    return "applied", "已更新至公告目标版本"
