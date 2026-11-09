import hashlib
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.report import ScanReport
from src.models.scan_task import ScanStatus, ScanTask
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.schemas.report import ReportCreate
from src.services import report_documents
from src.services.asset_access import all_assets_visible, evidence_asset_ids

logger = logging.getLogger(__name__)


class ReportConflictError(ValueError):
    pass


def list_reports(
    session: Session, visible_asset_ids: set[str] | None = None
) -> list[ScanReport]:
    reports = list(session.scalars(select(ScanReport).order_by(ScanReport.created_at.desc())))
    if visible_asset_ids is None:
        return reports
    return [
        report for report in reports
        if all_assets_visible(report_asset_ids(session, report), visible_asset_ids)
    ]


def get_report(session: Session, report_id: str) -> ScanReport | None:
    return session.get(ScanReport, report_id)


def report_asset_ids(session: Session, report: ScanReport) -> set[str]:
    snapshot_ids = evidence_asset_ids(
        [], report.asset_snapshot or [], report.finding_snapshot or [],
    )
    task = session.get(ScanTask, report.scan_task_id)
    if task is not None:
        snapshot_ids.update(task_report_asset_ids(session, task))
    return snapshot_ids


def task_report_asset_ids(session: Session, task: ScanTask) -> set[str]:
    targets = evidence_asset_ids(
        task.asset_ids or [], task.asset_snapshot or [], task.findings or [],
    )
    targets.update(session.scalars(select(VulnerabilityFinding.asset_id).where(
        VulnerabilityFinding.scan_task_id == task.id,
    )))
    return targets


def create_report(session: Session, payload: ReportCreate) -> ScanReport:
    task = session.get(ScanTask, payload.scan_task_id)
    if task is None:
        raise ReportConflictError("扫描任务不存在")
    if task.status != ScanStatus.COMPLETED:
        raise ReportConflictError("仅已完成的扫描任务可以生成报告")
    report = ScanReport(
        id=str(uuid4()), scan_task_id=task.id, scan_task_name=task.name,
        format=payload.format, status="queued", asset_snapshot=task.asset_snapshot,
        knowledge_version=task.knowledge_version,
        vulnerability_summary=_summary(task.result_summary),
        finding_snapshot=_finding_snapshot(session, task), generated_by=payload.operator.strip(),
        audit_history=[_audit("created", payload.operator, "报告生成任务已创建")],
    )
    session.add(report)
    session.commit()
    return generate_report(session, report, payload.operator)


def generate_report(session: Session, report: ScanReport, operator: str) -> ScanReport:
    if report.status not in {"queued", "failed"}:
        raise ReportConflictError("仅排队或失败的报告可以生成")
    report.status = "generating"
    report.error_message = None
    session.commit()
    try:
        content = report_documents.build_document(
            report.format, report.scan_task_name, _metadata(report),
            report.vulnerability_summary, report.finding_snapshot,
        )
        report.file_name = f"scan-report-{report.id}.{report.format}"
        report.file_content = content
        report.file_size = len(content)
        report.sha256 = hashlib.sha256(content).hexdigest()
        report.status = "completed"
        report.completed_at = datetime.now(timezone.utc)
        report.audit_history = [
            *report.audit_history, _audit("generated", operator, "报告文件已生成")
        ]
    except Exception as exc:
        logger.exception("Report generation failed for %s", report.id)
        report.status = "failed"
        report.error_message = str(exc)[:1000]
        report.audit_history = [
            *report.audit_history, _audit("failed", operator, report.error_message)
        ]
    session.commit()
    session.refresh(report)
    return report


def record_download(session: Session, report: ScanReport, operator: str) -> bytes:
    if report.status != "completed" or report.file_content is None:
        raise ReportConflictError("报告文件尚未生成")
    report.download_count += 1
    report.audit_history = [
        *report.audit_history, _audit("downloaded", operator, "报告文件已下载")
    ]
    content = report.file_content
    session.commit()
    return content


def _finding_snapshot(session: Session, task: ScanTask) -> list[dict[str, object]]:
    rows = session.execute(
        select(VulnerabilityFinding, VulnerabilityCase.status)
        .join(VulnerabilityCase, VulnerabilityFinding.case_id == VulnerabilityCase.id)
        .where(VulnerabilityFinding.scan_task_id == task.id)
    ).all()
    if rows:
        return [_record_snapshot(finding, disposition) for finding, disposition in rows]
    return [{**item, "disposition_status": "open"} for item in task.findings]


def _record_snapshot(finding: VulnerabilityFinding, disposition: str) -> dict[str, object]:
    keys = (
        "asset_id", "asset_name", "cve_id", "advisory_id", "title", "severity",
        "package_name", "installed_version", "fixed_version", "source_name",
        "vendor_url", "conclusion", "confidence", "evidence_chain",
    )
    return {key: getattr(finding, key) for key in keys} | {"disposition_status": disposition}


def _summary(value: dict[str, object] | None) -> dict[str, int]:
    source = value or {}
    return {
        key: int(source.get(key, 0))
        for key in ("critical", "high", "medium", "low", "manual_confirmation")
    }


def _metadata(report: ScanReport) -> dict[str, str]:
    assets = "、".join(str(item.get("name", "未知资产")) for item in report.asset_snapshot)
    return {
        "扫描任务": report.scan_task_name,
        "目标资产": assets or "无",
        "知识库版本": report.knowledge_version or "未发布",
        "生成时间": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "判定原则": "厂商公告优先，无法确认 backport 时需人工确认",
        "扫描策略": "只读、非破坏",
    }


def _audit(action: str, operator: str, detail: str) -> dict[str, str]:
    return {
        "action": action, "operator": operator.strip(), "detail": detail,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
