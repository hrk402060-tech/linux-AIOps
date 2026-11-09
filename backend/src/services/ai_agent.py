from datetime import datetime, timezone
import logging
from uuid import uuid4

from sqlalchemy import Select, and_, exists, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.ai_agent import AgentRecommendation
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.services.ai_providers import get_provider, provider_catalog

logger = logging.getLogger(__name__)


def current_findings_statement() -> Select[tuple[VulnerabilityFinding]]:
    ranked = select(
        VulnerabilityFinding.id.label("id"),
        func.row_number().over(
            partition_by=(VulnerabilityFinding.asset_id, VulnerabilityFinding.cve_id),
            order_by=(VulnerabilityFinding.detected_at.desc(), VulnerabilityFinding.id.desc()),
        ).label("position"),
    ).subquery()
    # PostgreSQL otherwise may rerun the ranking for each historical finding.
    latest = select(ranked.c.id).where(ranked.c.position == 1).cte(
        "latest_agent_findings",
    ).prefix_with("MATERIALIZED", dialect="postgresql")
    return select(VulnerabilityFinding).join(
        latest, latest.c.id == VulnerabilityFinding.id,
    ).join(VulnerabilityCase, and_(
        VulnerabilityCase.id == VulnerabilityFinding.case_id,
        VulnerabilityCase.asset_id == VulnerabilityFinding.asset_id,
        VulnerabilityCase.cve_id == VulnerabilityFinding.cve_id,
    )).join(Asset, Asset.id == VulnerabilityFinding.asset_id).where(
        Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED,
        or_(VulnerabilityCase.status.in_({"open", "pending_verification"}), and_(
            VulnerabilityCase.status == "risk_accepted",
            VulnerabilityCase.risk_accepted_until <= datetime.now(timezone.utc),
        )),
    )


def get_workspace(
    session: Session, queue_page: int = 1, queue_page_size: int = 50,
    history_page: int = 1, history_page_size: int = 50,
) -> dict[str, object]:
    current = current_findings_statement()
    pending = ~exists(select(AgentRecommendation.id).where(
        AgentRecommendation.finding_id == VulnerabilityFinding.id,
    ))
    totals = session.execute(current.with_only_columns(
        func.count(), func.count().filter(pending),
        func.count().filter(VulnerabilityFinding.severity.in_({"critical", "high"})),
        maintain_column_froms=True,
    )).one()
    findings = session.execute(current.add_columns(VulnerabilityCase.status).order_by(
        VulnerabilityFinding.detected_at.desc(), VulnerabilityFinding.id.desc(),
    ).offset((queue_page - 1) * queue_page_size).limit(queue_page_size)).all()
    recommendations = list(session.scalars(select(AgentRecommendation).order_by(
        AgentRecommendation.created_at.desc(), AgentRecommendation.id.desc(),
    ).offset((history_page - 1) * history_page_size).limit(history_page_size)))
    statuses = dict(session.execute(select(
        AgentRecommendation.status, func.count(AgentRecommendation.id),
    ).group_by(AgentRecommendation.status)).all())
    return {
        "providers": provider_catalog(), "queue_total": int(totals[0]),
        "queue_page": queue_page, "history_page": history_page,
        "history_total": sum(statuses.values()),
        "summary": {
            "pending_findings": int(totals[1]),
            "pending_review": int(statuses.get("pending_review", 0)),
            "approved": int(statuses.get("approved", 0)),
            "urgent_alerts": int(totals[2]),
        },
        "findings": [_finding_projection(finding, status) for finding, status in findings],
        "recommendations": recommendations,
    }


def _finding_projection(
    finding: VulnerabilityFinding, disposition_status: str,
) -> dict[str, object]:
    return {
        **{column.name: getattr(finding, column.name) for column in finding.__table__.columns},
        "disposition_status": "pending_verification"
        if disposition_status == "pending_verification" else "open",
    }


def _require_current(session: Session, finding_id: str) -> VulnerabilityFinding:
    finding = session.scalar(current_findings_statement().where(
        VulnerabilityFinding.id == finding_id,
    ).execution_options(populate_existing=True))
    if finding is None:
        raise ValueError("该漏洞证据已过期或不在当前待处理队列，请刷新后重试")
    return finding


def create_triage(
    session: Session, finding: VulnerabilityFinding, provider_id: str
) -> AgentRecommendation:
    current = _require_current(session, finding.id)
    provider = get_provider(provider_id)
    result = provider.analyze(current)
    try:
        # Lock disposition and lifecycle while validating the persistence boundary.
        session.scalar(select(Asset).where(Asset.id == current.asset_id).with_for_update())
        session.scalar(select(VulnerabilityCase).where(
            VulnerabilityCase.id == current.case_id,
        ).with_for_update())
        current = _require_current(session, current.id)
        recommendation = AgentRecommendation(
            id=str(uuid4()), finding_id=current.id, cve_id=current.cve_id,
            asset_name=current.asset_name, provider_id=provider.id, provider_name=provider.name,
            status="pending_review", priority=result.priority,
            risk_analysis=result.risk_analysis, remediation_steps=result.remediation_steps,
            validation_steps=result.validation_steps, rollback_plan=result.rollback_plan,
            evidence_snapshot=[dict(step) for step in current.evidence_chain],
        )
        session.add(recommendation)
        session.commit()
        session.refresh(recommendation)
        return recommendation
    except ValueError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Vulnerability triage persistence failed")
        raise


def review_recommendation(
    session: Session, recommendation: AgentRecommendation, decision: str, operator: str,
) -> AgentRecommendation:
    if decision not in {"approved", "rejected"} or not operator.strip():
        raise ValueError("无效审核结果或审核身份")
    try:
        result = session.execute(update(AgentRecommendation).where(
            AgentRecommendation.id == recommendation.id,
            AgentRecommendation.status == "pending_review",
        ).values(status=decision, reviewed_at=datetime.now(timezone.utc),
                 reviewed_by=operator).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise ValueError("该建议已审核，不能覆盖原审核结果，请刷新后查看")
        session.commit()
        session.refresh(recommendation)
        return recommendation
    except ValueError:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Vulnerability recommendation review failed")
        raise
