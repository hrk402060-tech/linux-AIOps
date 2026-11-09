from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from src.models.vulnerability import VulnerabilityCase
from src.services import ai_agent
from test_ai_agent_governance import add_advice, add_finding


def test_workspace_query_count_does_not_grow_with_page_size(
    client: TestClient, database_session: Session,
) -> None:
    for index in range(50):
        finding = add_finding(database_session, cve=f"CVE-2099-{index:04d}")
        if index == 0:
            add_advice(database_session, finding)
        if index % 2:
            case = database_session.get(VulnerabilityCase, finding.case_id)
            case.status = "pending_verification"
    database_session.commit()
    database_session.expunge_all()
    statements: list[str] = []

    def collect(
        connection: object, cursor: object, statement: str, parameters: object,
        context: object, executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    engine = database_session.get_bind()
    event.listen(engine, "before_cursor_execute", collect)
    try:
        workspace = ai_agent.get_workspace(database_session)
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert len(workspace["findings"]) == workspace["queue_total"] == 50
    assert workspace["summary"]["pending_findings"] == 49
    assert workspace["summary"]["urgent_alerts"] == 50
    assert sum(item["disposition_status"] == "pending_verification"
               for item in workspace["findings"]) == 25
    assert len(statements) <= 4


@pytest.mark.parametrize("invalid", ["missing_case", "wrong_asset", "wrong_cve"])
def test_newest_invalid_relationship_never_revives_old_evidence(
    client: TestClient, database_session: Session, invalid: str,
) -> None:
    old = add_finding(
        database_session, detected_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    new = add_finding(database_session, asset_id=old.asset_id, case_id=old.case_id)
    if invalid == "missing_case":
        new.case_id = None
    else:
        other = add_finding(database_session, cve="CVE-2099-9999")
        case = database_session.get(VulnerabilityCase, other.case_id)
        case.status = "fixed"
        if invalid == "wrong_cve":
            case.asset_id = old.asset_id
        else:
            case.cve_id = old.cve_id
        new.case_id = case.id
    database_session.commit()
    body = client.get("/api/v1/agent").json()
    assert body["findings"] == []
    assert body["queue_total"] == body["summary"]["pending_findings"] == 0
    assert body["summary"]["urgent_alerts"] == 0
    assert client.post("/api/v1/agent/triage", json={"finding_id": old.id}).status_code == 409


def test_empty_page_keeps_global_metrics_and_independent_history(
    client: TestClient, database_session: Session,
) -> None:
    first = add_finding(database_session)
    second = add_finding(database_session)
    second.severity = "low"
    database_session.commit()
    advice = add_advice(database_session, first, status="approved")
    body = client.get("/api/v1/agent?queue_page=2&queue_page_size=2").json()
    assert body["findings"] == []
    assert body["queue_total"] == 2
    assert body["summary"] == {
        "pending_findings": 1, "pending_review": 0, "approved": 1, "urgent_alerts": 1,
    }
    assert body["history_total"] == 1
    assert [item["id"] for item in body["recommendations"]] == [advice.id]
