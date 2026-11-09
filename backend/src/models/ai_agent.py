from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class AgentRecommendation(Base):
    __tablename__ = "agent_recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("vulnerability_findings.id", ondelete="CASCADE"), index=True
    )
    cve_id: Mapped[str] = mapped_column(String(40), index=True)
    asset_name: Mapped[str] = mapped_column(String(120), index=True)
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), index=True)
    priority: Mapped[str] = mapped_column(String(20), index=True)
    risk_analysis: Mapped[str] = mapped_column(Text)
    remediation_steps: Mapped[list[str]] = mapped_column(JSON)
    validation_steps: Mapped[list[str]] = mapped_column(JSON)
    rollback_plan: Mapped[str] = mapped_column(Text)
    evidence_snapshot: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
