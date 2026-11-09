from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.vulnerability import EvidenceStep

ReviewDecision = Literal["approved", "rejected"]
RecommendationStatus = Literal["pending_review", "approved", "rejected"]
Priority = Literal["urgent", "high", "normal", "review"]


class AgentProvider(BaseModel):
    id: str
    name: str
    kind: Literal["local", "external"]
    status: Literal["ready", "disabled"]
    requires_api_key: bool


class AgentFinding(BaseModel):
    id: str
    cve_id: str
    title: str
    asset_name: str
    severity: str
    conclusion: str
    confidence: str
    package_name: str
    installed_version: str
    fixed_version: str
    source_name: str
    asset_id: str
    disposition_status: Literal["open", "pending_verification"]

    model_config = ConfigDict(from_attributes=True)


class AgentSummary(BaseModel):
    pending_findings: int
    pending_review: int
    approved: int
    urgent_alerts: int


class AgentRecommendationResponse(BaseModel):
    id: str
    finding_id: str
    cve_id: str
    asset_name: str
    provider_id: str
    provider_name: str
    status: RecommendationStatus
    priority: Priority
    risk_analysis: str
    remediation_steps: list[str]
    validation_steps: list[str]
    rollback_plan: str
    evidence_snapshot: list[EvidenceStep]
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    can_execute: Literal[False] = False

    model_config = ConfigDict(from_attributes=True)


class AgentWorkspaceResponse(BaseModel):
    providers: list[AgentProvider]
    summary: AgentSummary
    findings: list[AgentFinding]
    recommendations: list[AgentRecommendationResponse]
    queue_total: int
    queue_page: int
    history_total: int
    history_page: int


class AgentTriageRequest(BaseModel):
    finding_id: str
    provider_id: str = Field(default="local-rules", min_length=1, max_length=80)


class AgentReviewRequest(BaseModel):
    decision: ReviewDecision
