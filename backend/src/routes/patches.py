from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database import get_session
from src.auth import AuthPrincipal, authenticated_operator, require_admin, require_system_admin
from src.models.asset import Asset
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.services import vulnerabilities as vulnerability_service
from src.services.version_safety import compare_obvious_versions
from src.services import asset_access, patch_execution, rpm_patch
from src.schemas.vulnerability import VulnerabilityDispositionUpdate

router = APIRouter(prefix="/patches", tags=["patches"])


class PatchApprovalRequest(BaseModel):
    operator: str = Field(default="authenticated", min_length=2, max_length=120)
    confirmation: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=2, max_length=1000)


@router.get("")
def read_patch_advice(
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    visible_asset_ids = asset_access.visible_asset_ids(session, principal)
    ranked = select(
        VulnerabilityFinding.id.label("finding_id"),
        func.row_number().over(
            partition_by=(VulnerabilityFinding.asset_id, VulnerabilityFinding.cve_id),
            order_by=(VulnerabilityFinding.detected_at.desc(), VulnerabilityFinding.id.desc()),
        ).label("position"),
    ).subquery()
    statement = select(VulnerabilityFinding).join(
            ranked, VulnerabilityFinding.id == ranked.c.finding_id
        ).where(ranked.c.position == 1)
    if visible_asset_ids is not None:
        statement = statement.where(VulnerabilityFinding.asset_id.in_(visible_asset_ids))
    findings = list(session.scalars(statement.order_by(
        VulnerabilityFinding.detected_at.desc()
    ).limit(200)))
    eligible_ids = {item.asset_id for item in findings if rpm_patch.is_rpm_advice(item)}
    assets = {asset.id: asset for asset in session.scalars(select(Asset).where(Asset.id.in_(eligible_ids)))} if eligible_ids else {}
    return [{
        "id": item.id, "finding_id": item.id, "asset_id": item.asset_id,
        "asset_name": item.asset_name, "cve_id": item.cve_id,
        "package_name": item.package_name, "current_version": item.installed_version,
        "target_version": item.fixed_version, "severity": item.severity,
        "source_name": item.source_name, "vendor_url": item.vendor_url,
        "evidence_chain": item.evidence_chain,
        "status": _patch_status(session, item),
        "target_version_relation": compare_obvious_versions(
            item.installed_version, item.fixed_version
        ),
        "target_version_issue": _target_version_issue(item),
        "rollback_plan": _rollback_plan(item),
        "approval_phrase": f"APPROVE {item.id}",
        **_execution_fields(session, principal, item, assets.get(item.asset_id)),
        "history_count": session.scalar(select(func.count()).where(
            VulnerabilityFinding.asset_id == item.asset_id,
            VulnerabilityFinding.cve_id == item.cve_id,
        )) or 0,
        "latest_scan_id": item.scan_task_id,
        "latest_scan_name": item.scan_task_name,
        "latest_detected_at": item.detected_at,
    } for item in findings]


def _execution_fields(
    session: Session, principal: AuthPrincipal, item: VulnerabilityFinding,
    asset: Asset | None,
) -> dict[str, object]:
    case = session.get(VulnerabilityCase, item.case_id) if item.case_id else None
    if principal.role != "admin" or principal.auth_type != "session" or case is None or case.status != "open":
        return {"execution_enabled": False}
    if not rpm_patch.is_rpm_advice(item):
        return {"execution_enabled": False, "execution_issue": "缺少可自动更新的 RPM 修复版本证据"}
    issue = patch_execution.availability_issue(asset)
    return {"execution_enabled": issue is None, "execution_issue": issue}


def _patch_status(session: Session, finding: VulnerabilityFinding) -> str:
    disposition = (vulnerability_service.get_finding(session, finding.id) or {}).get(
        "disposition_status", "open"
    )
    if disposition != "open":
        return str(disposition)
    relation = compare_obvious_versions(finding.installed_version, finding.fixed_version)
    if relation in {"lower_than_current", "equal_to_current"}:
        return "invalid_target"
    if relation == "unknown" or finding.conclusion != "confirmed":
        return "manual_confirmation"
    return "pending_approval"


def _target_version_issue(finding: VulnerabilityFinding) -> str | None:
    relation = compare_obvious_versions(finding.installed_version, finding.fixed_version)
    if relation == "lower_than_current":
        return "目标版本低于当前版本，禁止生成补丁建议"
    if relation == "equal_to_current":
        return "目标版本与当前版本相同，不能作为修复升级"
    if relation == "unknown":
        return "发行版版本无法安全比较，需人工核对厂商公告及 backport 修复"
    if finding.conclusion != "confirmed":
        return "漏洞判定需人工确认，请先核对厂商公告与版本证据"
    return None


def _rollback_plan(finding: VulnerabilityFinding) -> str:
    return (
        f"变更前记录 {finding.package_name} 当前版本 {finding.installed_version}；"
        "复扫未通过时由人工按厂商公告恢复原版本，确认服务状态后再次复扫。"
        "平台不会自动卸载或回滚。"
    )


@router.post("/{finding_id}/approve")
def approve_patch_advice(
    finding_id: str, payload: PatchApprovalRequest, request: Request,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session)
) -> dict[str, object]:
    finding = session.get(VulnerabilityFinding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="补丁建议不存在")
    asset_access.require_asset(session, principal, finding.asset_id)
    expected = f"APPROVE {finding.id}"
    if payload.confirmation.strip() != expected:
        raise HTTPException(status_code=400, detail=f"确认文本必须为：{expected}")
    current = vulnerability_service.get_finding(session, finding.id)
    if current is None:
        raise HTTPException(status_code=404, detail="漏洞处置记录不存在")
    if current["disposition_status"] != "open":
        raise HTTPException(status_code=409, detail="当前漏洞状态不允许重复审批")
    issue = _target_version_issue(finding)
    if issue:
        detail = "目标版本低于当前版本，禁止审批补丁建议" if (
            compare_obvious_versions(finding.installed_version, finding.fixed_version) == "lower_than_current"
        ) else issue
        raise HTTPException(status_code=409, detail=detail)
    try:
        updated = vulnerability_service.update_disposition(
            session, finding, VulnerabilityDispositionUpdate(
                status="pending_verification", reason=payload.reason,
                operator=authenticated_operator(request),
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {**updated, "status": "pending_verification", "execution_enabled": False}


@router.post("/{finding_id}/execute")
def execute_patch_advice(
    finding_id: str, payload: PatchApprovalRequest, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if principal.auth_type != "session":
        raise HTTPException(403, "请使用管理员账号登录后执行更新")
    if payload.confirmation.strip() != patch_execution.CONFIRMATION or len(payload.reason.strip()) < 2:
        raise HTTPException(400, f"请输入“{patch_execution.CONFIRMATION}”并填写更新原因")
    finding = session.get(VulnerabilityFinding, finding_id)
    if finding is None:
        raise HTTPException(404, "补丁建议不存在")
    asset_access.require_asset(session, principal, finding.asset_id, ssh=True)
    return patch_execution.execute(
        session, request.app.state.credential_vault, finding, principal.username, payload.reason.strip(),
    )
