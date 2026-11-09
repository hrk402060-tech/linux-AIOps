from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.services import knowledge, knowledge_backfill, openeuler_sync
from src.services import knowledge_sync


LIST_RESPONSE = {
    "code": 0, "success": True,
    "result": {
        "totalCount": 1,
        "securityNoticeList": [{
            "securityNoticeNo": "openEuler-SA-2026-3579",
            "affectedComponent": "openssl",
            "affectedProduct": "openEuler-24.03-LTS-SP4",
            "announcementTime": "2026-08-30",
            "updateTime": "2026-08-30 00:00:00",
            "cveId": "CVE-2026-14456;", "summary": "openssl security update",
            "type": "High",
        }],
    },
}
DETAIL_RESPONSE = {
    "code": 0, "success": True,
    "result": {
        **LIST_RESPONSE["result"]["securityNoticeList"][0],
        "packageHelperList": [{
            "productName": "openEuler-24.03-LTS-SP4",
            "child": [{
                "productName": "src",
                "child": [{
                    "packageName": "openssl-3.5.6-5.oe2403sp4.src.rpm", "child": [],
                }],
            }, {
                "productName": "x86_64",
                "child": [{
                    "packageName": "openssl-libs-3.5.6-5.oe2403sp4.x86_64.rpm",
                    "child": [],
                }],
            }],
        }],
    },
}


def test_official_feed_builds_vendor_package_with_product_version() -> None:
    with patch("src.services.openeuler_sync._request_json") as request:
        request.side_effect = [LIST_RESPONSE, DETAIL_RESPONSE]
        package = openeuler_sync.fetch_package(batch_size=20)

    assert package.source_name == "openEuler Security Advisories"
    assert package.advisories[0].cve_id == "CVE-2026-14456"
    affected = package.advisories[0].affected_packages[0]
    assert affected.name == "openssl"
    assert affected.fixed_version == "3.5.6-5.oe2403sp4"
    assert affected.os_versions == ["24.03-LTS-SP4"]
    assert {item.name for item in package.advisories[0].affected_packages} == {
        "openssl", "openssl-libs",
    }
    assert request.call_count == 2


def test_manual_official_sync_publishes_and_exposes_source_health(client: TestClient) -> None:
    sources = client.get("/api/v1/knowledge/sources").json()
    source = next(item for item in sources if item["name"] == "openEuler Security Advisories")

    with patch("src.services.openeuler_sync.fetch_package") as fetch:
        fetch.return_value = openeuler_sync.build_package(LIST_RESPONSE, [DETAIL_RESPONSE])
        response = client.post(f"/api/v1/knowledge/sources/{source['id']}/sync")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    advisories = client.get("/api/v1/knowledge/advisories").json()
    assert advisories[0]["affected_packages"][0]["os_versions"] == ["24.03-LTS-SP4"]
    coverage = client.get("/api/v1/knowledge/coverage").json()
    row = next(item for item in coverage["sources"] if item["source_id"] == source["id"])
    assert row["health"] == "healthy"
    assert row["advisory_count"] == 1


def test_official_sync_failure_retains_active_release(client: TestClient) -> None:
    source = next(
        item for item in client.get("/api/v1/knowledge/sources").json()
        if item["name"] == "openEuler Security Advisories"
    )
    with patch("src.services.openeuler_sync.fetch_package") as fetch:
        fetch.return_value = openeuler_sync.build_package(LIST_RESPONSE, [DETAIL_RESPONSE])
        client.post(f"/api/v1/knowledge/sources/{source['id']}/sync")
    active_before = next(
        item for item in client.get("/api/v1/knowledge/releases").json()
        if item["status"] == "active"
    )

    with patch(
        "src.services.openeuler_sync.fetch_package",
        side_effect=knowledge.KnowledgeImportError("官方接口数据校验失败"),
    ):
        failed = client.post(f"/api/v1/knowledge/sources/{source['id']}/sync")

    assert failed.status_code == 422
    active_after = next(
        item for item in client.get("/api/v1/knowledge/releases").json()
        if item["status"] == "active"
    )
    assert active_after["id"] == active_before["id"]
    assert client.get("/api/v1/knowledge/sync-runs").json()[0]["status"] == "failed"


def test_product_version_prevents_cross_release_match() -> None:
    package = openeuler_sync.build_package(LIST_RESPONSE, [DETAIL_RESPONSE])
    affected = package.advisories[0].affected_packages[0]
    assert "24.03-LTS-SP4" in affected.os_versions
    assert datetime.now(timezone.utc).year >= package.published_at.year


def test_scheduler_aligns_to_daily_two_and_four_hour_windows() -> None:
    current = datetime(2026, 9, 4, 1, 30, tzinfo=timezone.utc)
    assert knowledge_sync._seconds_until_next_sync(14400, current) == 1800
    current = datetime(2026, 9, 4, 2, 30, tzinfo=timezone.utc)
    assert knowledge_sync._seconds_until_next_sync(14400, current) == 12600


def test_backfill_tracks_page_cursor_and_completes(client: TestClient) -> None:
    second_list = {
        **LIST_RESPONSE,
        "result": {
            **LIST_RESPONSE["result"],
            "securityNoticeList": [{
                **LIST_RESPONSE["result"]["securityNoticeList"][0],
                "securityNoticeNo": "openEuler-SA-2026-3000",
                "cveId": "CVE-2026-13000;",
            }],
        },
    }
    second_detail = {
        **DETAIL_RESPONSE,
        "result": {
            **DETAIL_RESPONSE["result"],
            "securityNoticeNo": "openEuler-SA-2026-3000",
            "cveId": "CVE-2026-13000;",
        },
    }
    pages = [
        (openeuler_sync.build_package(LIST_RESPONSE, [DETAIL_RESPONSE]), 2, 1),
        (openeuler_sync.build_package(second_list, [second_detail]), 2, 1),
    ]

    with patch("src.services.knowledge_backfill.fetch_page", side_effect=pages):
        response = client.post(
            "/api/v1/knowledge/backfills",
            json={"batch_size": 1, "delay_seconds": 0},
        )

    assert response.status_code == 202
    jobs = client.get("/api/v1/knowledge/backfills").json()
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["current_page"] == 3
    assert jobs[0]["total_pages"] == 2
    assert jobs[0]["processed_notices"] == 2
    assert jobs[0]["published_advisories"] == 2
    assert jobs[0]["progress"] == 100


def test_failed_backfill_resumes_from_same_page(client: TestClient) -> None:
    package = openeuler_sync.build_package(LIST_RESPONSE, [DETAIL_RESPONSE])
    with patch(
        "src.services.knowledge_backfill.fetch_page",
        side_effect=knowledge.KnowledgeImportError("官方接口暂时不可用"),
    ):
        created = client.post(
            "/api/v1/knowledge/backfills",
            json={"batch_size": 1, "delay_seconds": 0},
        ).json()

    failed = client.get("/api/v1/knowledge/backfills").json()[0]
    assert failed["status"] == "failed"
    assert failed["current_page"] == 1
    assert "暂时不可用" in failed["error_message"]

    with patch(
        "src.services.knowledge_backfill.fetch_page",
        return_value=(package, 1, 1),
    ):
        resumed = client.post(
            f"/api/v1/knowledge/backfills/{created['id']}/resume"
        )

    assert resumed.status_code == 202
    completed = client.get("/api/v1/knowledge/backfills").json()[0]
    assert completed["status"] == "completed"
    assert completed["processed_notices"] == 1


def test_only_one_active_backfill_is_allowed(client: TestClient) -> None:
    with patch("src.services.knowledge_backfill.execute_backfill"):
        first = client.post(
            "/api/v1/knowledge/backfills",
            json={"batch_size": 10, "delay_seconds": 1},
        )
        second = client.post(
            "/api/v1/knowledge/backfills",
            json={"batch_size": 10, "delay_seconds": 1},
        )

    assert first.status_code == 202
    assert second.status_code == 409


def test_interrupted_backfill_can_resume_after_restart(client: TestClient) -> None:
    with patch("src.services.knowledge_backfill.execute_backfill"):
        created = client.post(
            "/api/v1/knowledge/backfills",
            json={"batch_size": 10, "delay_seconds": 1},
        ).json()
    with client.app.state.session_factory() as session:
        job = session.get(knowledge_backfill.KnowledgeBackfillJob, created["id"])
        assert job is not None
        job.status = "running"
        session.commit()
        assert knowledge_backfill.recover_interrupted_jobs(session) == 1

    recovered = client.get("/api/v1/knowledge/backfills").json()[0]
    assert recovered["status"] == "failed"
    assert "可从当前页续跑" in recovered["error_message"]
