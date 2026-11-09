from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.ai_agent import AgentRecommendation
from src.services.auth import create_user
from src.models.asset import Asset, AssetEnvironment, AssetLifecycleStatus, OsFamily
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.services import ai_agent


def add_finding(
    session: Session, *, status: str = "open", asset_id: str | None = None,
    cve: str = "CVE-2099-0001", detected_at: datetime | None = None,
    case_id: str | None = None,
) -> VulnerabilityFinding:
    asset_id = asset_id or str(uuid4())
    if session.get(Asset, asset_id) is None:
        session.add(Asset(id=asset_id, name=asset_id, host="192.0.2.10",
                          os_family=OsFamily.OPENEULER, environment=AssetEnvironment.TEST))
        session.flush()
    if case_id is None:
        case_id = str(uuid4())
        session.add(VulnerabilityCase(id=case_id, asset_id=asset_id, asset_name=asset_id,
                                      cve_id=cve, status=status, audit_history=[]))
        session.flush()
    finding = VulnerabilityFinding(
        id=str(uuid4()), case_id=case_id, asset_id=asset_id, asset_name=asset_id,
        scan_task_id="demo-scan", scan_task_name="demo", cve_id=cve,
        advisory_id="DEMO-ADV", title="demo", severity="high", package_name="kernel",
        installed_version="1", fixed_version="2", source_name="demo",
        conclusion="confirmed", confidence="high", evidence_chain=[],
        detected_at=detected_at or datetime.now(timezone.utc),
    )
    session.add(finding)
    session.commit()
    return finding


def add_advice(
    session: Session, finding: VulnerabilityFinding, status: str = "pending_review",
    created_at: datetime | None = None,
) -> AgentRecommendation:
    item = AgentRecommendation(
        id=str(uuid4()), finding_id=finding.id, cve_id=finding.cve_id,
        asset_name=finding.asset_name, provider_id="local-rules", provider_name="demo",
        status=status, priority="high", risk_analysis="demo", remediation_steps=[],
        validation_steps=[], rollback_plan="demo", evidence_snapshot=[],
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    return item


def test_queue_deduplicates_latest_before_disposition_and_severity(
    client: TestClient, database_session: Session,
) -> None:
    old = add_finding(database_session, detected_at=datetime.now(timezone.utc)-timedelta(days=1))
    new = add_finding(database_session, asset_id=old.asset_id, case_id=old.case_id)
    new.severity = "low"
    database_session.commit()
    body = client.get("/api/v1/agent").json()
    assert [item["id"] for item in body["findings"]] == [new.id]
    assert body["findings"][0]["asset_id"] == old.asset_id
    assert body["summary"]["urgent_alerts"] == 0
    stale = client.post("/api/v1/agent/triage", json={"finding_id": old.id})
    assert stale.status_code == 409
    assert database_session.scalar(select(AgentRecommendation)) is None


@pytest.mark.parametrize("status,eligible", [
    ("open", True), ("pending_verification", True), ("fixed", False),
    ("ignored", False), ("risk_accepted", False),
])
def test_queue_and_triage_follow_current_disposition(
    client: TestClient, database_session: Session, status: str, eligible: bool,
) -> None:
    finding = add_finding(database_session, status=status)
    if status == "risk_accepted":
        case = database_session.get(VulnerabilityCase, finding.case_id)
        case.risk_accepted_until = datetime.now(timezone.utc)+timedelta(days=1)
        database_session.commit()
    response = client.get("/api/v1/agent")
    assert response.json()["queue_total"] == int(eligible)
    triage = client.post("/api/v1/agent/triage", json={"finding_id": finding.id})
    assert triage.status_code == (201 if eligible else 409)


def test_expired_risk_acceptance_is_read_only_open_projection(
    client: TestClient, database_session: Session,
) -> None:
    finding = add_finding(database_session, status="risk_accepted")
    case = database_session.get(VulnerabilityCase, finding.case_id)
    case.risk_accepted_until = datetime.now(timezone.utc)-timedelta(seconds=1)
    database_session.commit()
    body = client.get("/api/v1/agent").json()
    assert body["findings"][0]["disposition_status"] == "open"
    database_session.refresh(case)
    assert case.status == "risk_accepted"
    assert case.audit_history == []


@pytest.mark.parametrize("invalid", ["archived", "missing_asset", "missing_case"])
def test_missing_or_archived_relationships_fail_closed(
    client: TestClient, database_session: Session, invalid: str,
) -> None:
    finding = add_finding(database_session)
    if invalid == "archived":
        database_session.get(Asset, finding.asset_id).lifecycle_status = AssetLifecycleStatus.ARCHIVED
    elif invalid == "missing_asset":
        finding.asset_id = "not-present"
    else:
        finding.case_id = None
    database_session.commit()
    assert client.get("/api/v1/agent").json()["queue_total"] == 0
    assert client.post("/api/v1/agent/triage", json={"finding_id": finding.id}).status_code == 409


def test_full_metrics_and_global_history_are_not_limited_to_fifty(
    client: TestClient, database_session: Session,
) -> None:
    oldest: VulnerabilityFinding | None = None
    for index in range(55):
        finding = add_finding(database_session, cve=f"CVE-2099-{index:04d}")
        if index == 0:
            oldest = finding
    assert oldest is not None
    for index in range(53):
        add_advice(database_session, oldest, "approved" if index % 2 else "pending_review")
    first = client.get("/api/v1/agent").json()
    assert first["queue_total"] == 55
    assert len(first["findings"]) == 50
    assert first["summary"] == {
        "pending_findings": 54, "pending_review": 27, "approved": 26, "urgent_alerts": 55,
    }
    assert first["history_total"] == 53
    assert len(first["recommendations"]) == 50
    second = client.get("/api/v1/agent?queue_page=2&history_page=2").json()
    assert second["queue_page"] == second["history_page"] == 2
    assert len(second["findings"]) == 5
    assert len(second["recommendations"]) == 3
    assert not {i["id"] for i in first["recommendations"]}.intersection(
        i["id"] for i in second["recommendations"]
    )
    assert client.get("/api/v1/agent?history_page_size=51").status_code == 422
    assert client.get("/api/v1/agent?queue_page=0").status_code == 422


def test_old_recommendation_outside_recent_window_still_counts_as_triaged(
    client: TestClient, database_session: Session,
) -> None:
    finding = add_finding(database_session)
    add_advice(database_session, finding, created_at=datetime.now(timezone.utc)-timedelta(days=2))
    hidden = add_finding(database_session, status="fixed", cve="CVE-2099-9999")
    for _ in range(51):
        add_advice(database_session, hidden)
    body = client.get("/api/v1/agent").json()
    assert body["summary"]["pending_findings"] == 0
    assert body["history_total"] == 52
    assert client.get("/api/v1/agent?history_page=2").json()["recommendations"]


def test_review_records_authenticated_actor_once_and_rejects_overwrite(
    client: TestClient, database_session: Session,
) -> None:
    advice = add_advice(database_session, add_finding(database_session))
    route = f"/api/v1/agent/recommendations/{advice.id}/review"
    reviewed = client.patch(route, json={"decision": "approved", "reviewed_by": "spoof"})
    assert reviewed.status_code == 200
    original = reviewed.json()
    assert original["reviewed_by"] == "break-glass-admin"
    assert original["reviewed_at"] is not None
    assert original["can_execute"] is False
    for decision in ("approved", "rejected"):
        assert client.patch(route, json={"decision": decision}).status_code == 409
    database_session.refresh(advice)
    assert advice.status == "approved"
    assert advice.reviewed_by == "break-glass-admin"
    history = client.get("/api/v1/agent").json()["recommendations"][0]
    assert history["reviewed_at"] == original["reviewed_at"]


def test_legacy_review_actor_stays_unknown(
    client: TestClient, database_session: Session,
) -> None:
    advice = add_advice(database_session, add_finding(database_session), "approved")
    advice.reviewed_at = datetime.now(timezone.utc)-timedelta(days=1)
    database_session.commit()
    body = client.get("/api/v1/agent").json()
    assert body["recommendations"][0]["reviewed_by"] is None
    assert body["recommendations"][0]["status"] == "approved"


def test_review_compare_and_set_rejects_stale_session(
    client: TestClient, database_session: Session,
) -> None:
    advice = add_advice(database_session, add_finding(database_session))
    with client.app.state.session_factory() as second:
        stale = second.get(AgentRecommendation, advice.id)
        ai_agent.review_recommendation(database_session, advice, "approved", "first-operator")
        with pytest.raises(ValueError):
            ai_agent.review_recommendation(second, stale, "rejected", "second-operator")
    database_session.refresh(advice)
    assert advice.status == "approved"
    assert advice.reviewed_by == "first-operator"


def test_triage_rechecks_eligibility_after_analysis(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding = add_finding(database_session)
    from src.services.ai_providers import LocalRulesProvider
    original = LocalRulesProvider.analyze

    from src.services.ai_providers import TriageResult

    def analyze(provider: LocalRulesProvider, target: VulnerabilityFinding) -> TriageResult:
        result = original(provider, target)
        database_session.get(VulnerabilityCase, finding.case_id).status = "ignored"
        database_session.commit()
        return result

    monkeypatch.setattr(LocalRulesProvider, "analyze", analyze)
    assert client.post("/api/v1/agent/triage", json={"finding_id": finding.id}).status_code == 409
    assert database_session.scalar(select(AgentRecommendation)) is None


def test_auditor_is_read_only_and_operator_identity_is_server_owned(
    client: TestClient, database_session: Session,
) -> None:
    finding = add_finding(database_session)
    advice = add_advice(database_session, finding)
    for role in ("operator", "auditor"):
        create_user(database_session, f"real-{role}", "Demo-Password-2099", role)
    client.headers.pop("Authorization")
    assert client.get("/api/v1/agent").status_code == 401
    assert client.post("/api/v1/agent/triage", json={"finding_id": finding.id}).status_code == 401
    assert client.post("/api/v1/auth/login", json={
        "username": "real-auditor", "password": "Demo-Password-2099",
    }).status_code == 200
    assert client.get("/api/v1/agent").status_code == 200
    assert client.post("/api/v1/agent/triage", json={"finding_id": finding.id}).status_code == 403
    route = f"/api/v1/agent/recommendations/{advice.id}/review"
    assert client.patch(route, json={"decision": "approved"}).status_code == 403
    database_session.refresh(advice)
    assert advice.status == "pending_review" and advice.reviewed_at is None
    assert advice.reviewed_by is None
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.post("/api/v1/auth/login", json={
        "username": "real-operator", "password": "Demo-Password-2099",
    }).status_code == 200
    assert client.post("/api/v1/agent/triage", json={"finding_id": finding.id}).status_code == 201
    response = client.patch(route, json={"decision": "rejected", "operator": "spoof"})
    assert response.status_code == 200
    assert response.json()["reviewed_by"] == "real-operator"
    assert response.json()["can_execute"] is False


@pytest.mark.parametrize("invalid", ["asset", "cve", "risk_expiry"])
def test_inconsistent_case_or_missing_expiry_cannot_enter_queue(
    client: TestClient, database_session: Session, invalid: str,
) -> None:
    finding = add_finding(database_session)
    case = database_session.get(VulnerabilityCase, finding.case_id)
    if invalid == "asset":
        case.asset_id = "different-asset"
    elif invalid == "cve":
        case.cve_id = "CVE-2099-other"
    else:
        case.status = "risk_accepted"
        case.risk_accepted_until = None
    database_session.commit()
    assert client.get("/api/v1/agent").json()["queue_total"] == 0
    assert client.post("/api/v1/agent/triage", json={"finding_id": finding.id}).status_code == 409


def test_equal_timestamps_use_stable_finding_id_order(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime.now(timezone.utc)
    first = add_finding(database_session, detected_at=now)
    second = add_finding(database_session, asset_id=first.asset_id, case_id=first.case_id,
                         detected_at=now)
    expected = max(first.id, second.id)
    for _ in range(2):
        assert client.get("/api/v1/agent").json()["findings"][0]["id"] == expected


def test_disabled_asset_evidence_remains_available_for_human_analysis(
    client: TestClient, database_session: Session,
) -> None:
    finding = add_finding(database_session)
    database_session.get(Asset, finding.asset_id).lifecycle_status = AssetLifecycleStatus.DISABLED
    database_session.commit()
    assert client.get("/api/v1/agent").json()["queue_total"] == 1
    assert client.post("/api/v1/agent/triage", json={"finding_id": finding.id}).status_code == 201
