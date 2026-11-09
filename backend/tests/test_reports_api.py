from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfReader

from src.models.scan_task import ScanStatus, ScanTask, ScanType


def _completed_scan(client: TestClient) -> str:
    task_id = str(uuid4())
    with client.app.state.session_factory() as session:
        session.add(ScanTask(
            id=task_id, name="生产环境漏洞扫描", scan_type=ScanType.COMPREHENSIVE,
            status=ScanStatus.COMPLETED, progress=100, read_only=True,
            asset_ids=["asset-1"], asset_snapshot=[{
                "id": "asset-1", "name": "web-01", "host": "10.0.0.8",
                "os_family": "openeuler", "environment": "production",
            }], knowledge_version="openeuler-test", findings=[{
                "asset_id": "asset-1", "asset_name": "web-01", "cve_id": "CVE-2026-1001",
                "title": "测试漏洞", "severity": "high", "package_name": "openssl",
                "installed_version": "1.0", "fixed_version": "1.1",
                "source_name": "openEuler Security Advisories",
                "advisory_id": "openEuler-SA-2026-1", "conclusion": "confirmed",
                "confidence": "high", "evidence_chain": [],
            }], result_summary={
                "assets_total": 1, "assets_scanned": 1, "assets_failed": 0,
                "critical": 0, "high": 1, "medium": 0, "low": 0,
                "manual_confirmation": 0,
            }, report_content="scan report", finished_at=datetime.now(timezone.utc),
        ))
        session.commit()
    return task_id


def test_generates_pdf_and_word_reports_with_audit(client: TestClient) -> None:
    scan_id = _completed_scan(client)
    pdf = client.post("/api/v1/reports", json={
        "scan_task_id": scan_id, "format": "pdf", "operator": "admin",
    })
    word = client.post("/api/v1/reports", json={
        "scan_task_id": scan_id, "format": "docx", "operator": "admin",
    })

    assert pdf.status_code == 201
    assert word.status_code == 201
    assert pdf.json()["status"] == "completed"
    assert pdf.json()["sha256"]
    listing = client.get("/api/v1/reports").json()
    assert len(listing) == 2
    assert listing[0]["knowledge_version"] == "openeuler-test"

    pdf_file = client.get(f"/api/v1/reports/{pdf.json()['id']}/download")
    docx_file = client.get(f"/api/v1/reports/{word.json()['id']}/download")
    pdf_reader = PdfReader(BytesIO(pdf_file.content))
    assert len(pdf_reader.pages) >= 1
    assert "证据链" in "".join(page.extract_text() or "" for page in pdf_reader.pages)
    document = Document(BytesIO(docx_file.content))
    assert document.paragraphs[0].text == "Linux 漏洞扫描报告"
    assert "证据链" in [paragraph.text for paragraph in document.paragraphs]
    detail = client.get(f"/api/v1/reports/{pdf.json()['id']}").json()
    assert detail["download_count"] == 1
    assert detail["audit_history"][-1]["action"] == "downloaded"


def test_report_requires_completed_scan_and_supports_retry(client: TestClient) -> None:
    asset = client.post("/api/v1/assets", json={
        "name": "queued-asset", "host": "10.0.0.9", "ssh_port": 22,
        "os_family": "openeuler", "environment": "test", "labels": [],
    }).json()
    queued = client.post("/api/v1/scan-tasks", json={
        "name": "未完成扫描", "scan_type": "host_vulnerability",
        "asset_ids": [asset["id"]],
    }).json()
    rejected = client.post("/api/v1/reports", json={
        "scan_task_id": queued["id"], "format": "pdf", "operator": "admin",
    })
    assert rejected.status_code == 409

    scan_id = _completed_scan(client)
    created = client.post("/api/v1/reports", json={
        "scan_task_id": scan_id, "format": "pdf", "operator": "admin",
    }).json()
    with client.app.state.session_factory() as session:
        from src.models.report import ScanReport
        report = session.get(ScanReport, created["id"])
        assert report is not None
        report.status = "failed"
        report.file_content = None
        report.error_message = "temporary failure"
        session.commit()
    retried = client.post(f"/api/v1/reports/{created['id']}/retry", json={
        "operator": "admin",
    })
    assert retried.status_code == 200
    assert retried.json()["status"] == "completed"
