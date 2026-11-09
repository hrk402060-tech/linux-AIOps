from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.models.scan_task import ScanTask, ScanType
from src.models.vulnerability import (
    VulnerabilityCase, VulnerabilityDeletionAudit, VulnerabilityFinding,
)
from src.schemas.vulnerability import VulnerabilityDispositionUpdate
from src.services.scan_verification import verified_versions

ACTIVE_STATUSES = {"open", "pending_verification"}


def persist_findings(
    session: Session, task: ScanTask, asset_results: list[dict[str, object]] | None = None
) -> None:
    if session.scalar(select(VulnerabilityFinding.id).where(
        VulnerabilityFinding.scan_task_id == task.id
    ).limit(1)):
        return
    cases = _case_map(session, task.asset_ids)
    for item in task.findings:
        key = (str(item["asset_id"]), str(item["cve_id"]))
        case = cases.get(key) or _create_case(item)
        if key not in cases:
            session.add(case)
            cases[key] = case
        elif case.status in {"fixed", "pending_verification"}:
            reason = (
                "验证扫描仍发现该漏洞"
                if case.status == "pending_verification"
                else "验证扫描再次发现漏洞"
            )
            _set_case(case, "open", reason, "system:scan", task.id)
        session.add(_to_record(task, item, case.id))
    _verify_pending_cases(session, task, cases, asset_results or [])


def list_findings(
    session: Session, query: str | None, asset_id: str | None, severity: str | None,
    conclusion: str | None, disposition: str, page: int, page_size: int,
    visible_asset_ids: set[str] | None = None,
) -> tuple[list[dict[str, object]], int]:
    _expire_risk_acceptances(session)
    statement = _latest_statement(
        query, asset_id, severity, conclusion, disposition, visible_asset_ids
    )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    records = list(session.scalars(
        statement.order_by(VulnerabilityFinding.detected_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ))
    return [serialize_finding(session, item) for item in records], int(total)


def get_finding(session: Session, finding_id: str) -> dict[str, object] | None:
    finding = session.get(VulnerabilityFinding, finding_id)
    return serialize_finding(session, finding) if finding else None


def get_finding_record(session: Session, finding_id: str) -> VulnerabilityFinding | None:
    return session.get(VulnerabilityFinding, finding_id)


def summarize_findings(
    session: Session, visible_asset_ids: set[str] | None = None
) -> dict[str, int]:
    records = list(session.scalars(_latest_statement(
        None, None, None, None, "active", visible_asset_ids
    )))
    summary = {
        "critical": 0, "high": 0, "medium": 0, "low": 0,
        "confirmed": 0, "manual_confirmation": 0,
    }
    for item in records:
        summary[item.severity] += 1
        summary[item.conclusion] += 1
    return summary


def update_disposition(
    session: Session, finding: VulnerabilityFinding, payload: VulnerabilityDispositionUpdate
) -> dict[str, object]:
    case = session.get(VulnerabilityCase, finding.case_id)
    if case is None:
        raise ValueError("漏洞处置记录不存在")
    if case.status == "fixed":
        raise ValueError("已修复状态只能由后续扫描重新判定")
    _set_case(
        case, payload.status, payload.reason.strip(), payload.operator.strip(), None,
        payload.risk_accepted_until,
    )
    session.commit()
    session.refresh(case)
    return serialize_finding(session, finding)


def physically_delete(
    session: Session, finding: VulnerabilityFinding, purpose: str, reason: str, operator: str
) -> None:
    snapshot = serialize_finding(session, finding)
    case_id = finding.case_id
    session.add(VulnerabilityDeletionAudit(
        id=str(uuid4()), finding_id=finding.id, asset_id=finding.asset_id,
        cve_id=finding.cve_id, purpose=purpose, reason=reason, operator=operator,
        evidence_snapshot=_json_snapshot(snapshot),
    ))
    session.delete(finding)
    session.flush()
    if case_id and not session.scalar(select(VulnerabilityFinding.id).where(
        VulnerabilityFinding.case_id == case_id
    ).limit(1)):
        case = session.get(VulnerabilityCase, case_id)
        if case:
            session.delete(case)
    session.commit()


def serialize_finding(
    session: Session, finding: VulnerabilityFinding
) -> dict[str, object]:
    case = session.get(VulnerabilityCase, finding.case_id)
    if case is None:
        raise ValueError("漏洞处置记录不存在")
    history = list(session.scalars(select(VulnerabilityFinding).where(
        VulnerabilityFinding.case_id == case.id
    ).order_by(VulnerabilityFinding.detected_at.desc())))
    return {
        **{column.name: getattr(finding, column.name) for column in finding.__table__.columns},
        "disposition_status": case.status, "disposition_reason": case.reason,
        "disposition_operator": case.operator,
        "disposition_changed_at": case.status_changed_at,
        "risk_accepted_until": case.risk_accepted_until,
        "verification_scan_id": case.verification_scan_id,
        "remediated_version": case.remediated_version, "fixed_at": case.fixed_at,
        "disposition_history": case.audit_history,
        "scan_history": [{
            "finding_id": item.id, "scan_task_id": item.scan_task_id,
            "scan_task_name": item.scan_task_name,
            "detected_at": item.detected_at.isoformat(),
            "knowledge_version": item.knowledge_version,
            "installed_version": item.installed_version,
        } for item in history],
    }


def _latest_statement(
    query: str | None, asset_id: str | None, severity: str | None,
    conclusion: str | None, disposition: str,
    visible_asset_ids: set[str] | None = None,
):
    latest_ids = select(
        VulnerabilityFinding.id.label("id"),
        func.row_number().over(
            partition_by=(VulnerabilityFinding.asset_id, VulnerabilityFinding.cve_id),
            order_by=(VulnerabilityFinding.detected_at.desc(), VulnerabilityFinding.id.desc()),
        ).label("position"),
    ).subquery()
    statement = select(VulnerabilityFinding).join(
        latest_ids, VulnerabilityFinding.id == latest_ids.c.id
    ).join(VulnerabilityCase, VulnerabilityFinding.case_id == VulnerabilityCase.id).where(
        latest_ids.c.position == 1
    )
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(or_(
            VulnerabilityFinding.cve_id.ilike(pattern),
            VulnerabilityFinding.advisory_id.ilike(pattern),
            VulnerabilityFinding.title.ilike(pattern),
            VulnerabilityFinding.asset_name.ilike(pattern),
            VulnerabilityFinding.package_name.ilike(pattern),
        ))
    if asset_id:
        statement = statement.where(VulnerabilityFinding.asset_id == asset_id)
    if visible_asset_ids is not None:
        statement = statement.where(VulnerabilityFinding.asset_id.in_(visible_asset_ids))
    if severity:
        statement = statement.where(VulnerabilityFinding.severity == severity)
    if conclusion:
        statement = statement.where(VulnerabilityFinding.conclusion == conclusion)
    if disposition == "active":
        statement = statement.where(VulnerabilityCase.status.in_(ACTIVE_STATUSES))
    elif disposition != "all":
        statement = statement.where(VulnerabilityCase.status == disposition)
    return statement


def _create_case(item: dict[str, object]) -> VulnerabilityCase:
    now = datetime.now(timezone.utc)
    return VulnerabilityCase(
        id=str(uuid4()), asset_id=str(item["asset_id"]),
        asset_name=str(item["asset_name"]), cve_id=str(item["cve_id"]),
        status="open", status_changed_at=now,
        audit_history=[_audit("open", "扫描首次发现", "system:scan", now, None)],
    )


def _case_map(
    session: Session, asset_ids: list[str]
) -> dict[tuple[str, str], VulnerabilityCase]:
    cases = session.scalars(select(VulnerabilityCase).where(
        VulnerabilityCase.asset_id.in_(asset_ids)
    ))
    return {(item.asset_id, item.cve_id): item for item in cases}


def _verify_pending_cases(
    session: Session, task: ScanTask, cases: dict[tuple[str, str], VulnerabilityCase],
    asset_results: list[dict[str, object]],
) -> None:
    current = {(str(item["asset_id"]), str(item["cve_id"])) for item in task.findings}
    if task.scan_type == ScanType.SERVICE_VULNERABILITY:
        return
    result_map = {
        str(item["asset_id"]): item
        for item in asset_results if item.get("status") == "scanned"
    }
    for key, case in cases.items():
        result = result_map.get(case.asset_id)
        if case.status != "pending_verification" or key in current or result is None:
            continue
        latest = session.scalar(select(VulnerabilityFinding).where(
            VulnerabilityFinding.case_id == case.id
        ).order_by(VulnerabilityFinding.detected_at.desc()).limit(1))
        if latest is None or not isinstance(result.get("package_versions"), dict):
            continue
        previous = list(session.scalars(select(VulnerabilityFinding).where(
            VulnerabilityFinding.case_id == case.id,
            VulnerabilityFinding.scan_task_id == latest.scan_task_id,
        )))
        remediated = verified_versions(
            previous, result.get("remediation_checks", []), result["package_versions"],
        )
        if remediated is None:
            continue
        _set_case(
            case, "fixed", "厂商公告与完整软件包采集验证修复", "system:scan", task.id,
            remediated_version=remediated,
        )


def _set_case(
    case: VulnerabilityCase, status: str, reason: str, operator: str,
    scan_id: str | None, risk_until: datetime | None = None,
    remediated_version: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    case.status = status
    case.reason = reason
    case.operator = operator
    case.status_changed_at = now
    case.risk_accepted_until = risk_until if status == "risk_accepted" else None
    case.verification_scan_id = scan_id if status == "fixed" else None
    case.remediated_version = remediated_version if status == "fixed" else None
    case.fixed_at = now if status == "fixed" else None
    case.audit_history = [
        *case.audit_history, _audit(status, reason, operator, now, scan_id)
    ]


def _expire_risk_acceptances(session: Session) -> None:
    now = datetime.now(timezone.utc)
    expired = session.scalars(select(VulnerabilityCase).where(
        VulnerabilityCase.status == "risk_accepted",
        VulnerabilityCase.risk_accepted_until <= now,
    ))
    changed = False
    for case in expired:
        _set_case(case, "open", "风险接受已到期", "system:expiry", None)
        changed = True
    if changed:
        session.commit()


def _audit(
    status: str, reason: str, operator: str, occurred_at: datetime, scan_id: str | None
) -> dict[str, str | None]:
    return {
        "status": status, "reason": reason, "operator": operator,
        "occurred_at": occurred_at.isoformat(), "verification_scan_id": scan_id,
    }


def _json_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in snapshot.items()
    }


def _to_record(
    task: ScanTask, item: dict[str, object], case_id: str
) -> VulnerabilityFinding:
    return VulnerabilityFinding(
        id=str(uuid4()), case_id=case_id, scan_task_id=task.id,
        scan_task_name=task.name, asset_id=str(item["asset_id"]),
        asset_name=str(item["asset_name"]), knowledge_version=task.knowledge_version,
        cve_id=str(item["cve_id"]), advisory_id=str(item["advisory_id"]),
        title=str(item["title"]), severity=str(item["severity"]),
        package_name=str(item["package_name"]),
        installed_version=str(item["installed_version"]),
        fixed_version=str(item["fixed_version"]), source_name=str(item["source_name"]),
        vendor_url=str(item["vendor_url"]) if item.get("vendor_url") else None,
        conclusion=str(item["conclusion"]), confidence=str(item["confidence"]),
        evidence_chain=[{"type": str(step["type"]), "value": str(step["value"])}
                        for step in item["evidence_chain"]],
    )
