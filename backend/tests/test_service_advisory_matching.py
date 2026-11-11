from unittest.mock import patch

import pytest

from src.models.knowledge import KnowledgeAdvisory
from src.models.scan_task import ScanType
from src.services import scan_engine
from src.services.service_advisory_matching import match_service_advisories
from src.services.service_advisory_versions import affected_version


def advisory() -> KnowledgeAdvisory:
    return KnowledgeAdvisory(
        id="upstream-1", release_id="release-1", cve_id="CVE-2024-27316",
        advisory_id="httpd-CVE-2024-27316", title="HTTP/2 memory exhaustion",
        source_name="Upstream Service Advisories", os_family="other", severity="medium",
        vendor_url="https://httpd.apache.org/security/json/CVE-2024-27316.json",
        affected_packages=[{
            "name": "httpd", "affected_version": "2.4.17 through 2.4.58",
            "fixed_version": "2.4.59", "upstream_product": "apache-httpd",
            "upstream_ranges": [{"introduced": "2.4.17", "last_affected": "2.4.58"}],
            "upstream_fixed_versions": ["2.4.59"],
        }],
    )


def service(version: str = "2.4.58-1ubuntu1.9") -> dict[str, str]:
    return {
        "protocol": "tcp", "listen": "0.0.0.0:80", "process": "apache2",
        "process_name": "apache2", "pid": "123", "executable": "/usr/sbin/apache2",
        "package_name": "apache2-bin", "installed_version": version, "banner": "",
    }


def test_distribution_version_is_only_manual_evidence_never_confirmed() -> None:
    findings = match_service_advisories([service()], [advisory()], [], [])
    assert len(findings) == 1
    assert findings[0]["conclusion"] == "manual_confirmation"
    assert findings[0]["confidence"] == "low"
    assert findings[0]["package_name"] == "apache2-bin"
    assert findings[0]["installed_version"] == "2.4.58-1ubuntu1.9"
    evidence = {item["type"]: item["value"] for item in findings[0]["evidence_chain"]}
    assert "backport" in evidence["limitation"]
    assert evidence["service_listener"] == "0.0.0.0:80"


@pytest.mark.parametrize("version", ["2.4.16", "2.4.59", "2.4.60", "unknown"])
def test_unaffected_or_unknown_version_is_not_reported(version: str) -> None:
    assert match_service_advisories([service(version)], [advisory()], [], []) == []


def test_installed_package_without_actual_listener_does_not_create_service_finding() -> None:
    assert match_service_advisories([], [advisory()], [], []) == []
    unrelated = {**service(), "process_name": "python", "executable": "/usr/bin/python"}
    assert match_service_advisories([unrelated], [advisory()], [], []) == []


def test_upstream_banner_can_identify_an_unmanaged_running_service() -> None:
    running = {
        **service(), "package_name": "", "installed_version": "",
        "banner": "HTTP/1.1 200 OK Server: Apache/2.4.58",
    }
    findings = match_service_advisories([running], [advisory()], [], [])
    assert findings[0]["package_name"] == "httpd"
    assert findings[0]["conclusion"] == "manual_confirmation"


def test_banner_requires_product_and_listening_process_evidence() -> None:
    unrelated = {
        **service(), "process_name": "", "process": "", "executable": "",
        "package_name": "", "installed_version": "",
        "banner": "HTTP/1.1 200 OK Server: Apache/2.4.58",
    }
    assert match_service_advisories([unrelated], [advisory()], [], []) == []


def test_vendor_fixed_backport_suppresses_upstream_version_warning() -> None:
    checks = [{
        "cve_id": "CVE-2024-27316", "package_name": "apache2-bin",
        "source_name": "Ubuntu Security Notices", "outcome": "not_vulnerable",
    }]
    assert match_service_advisories([service()], [advisory()], [], checks) == []


def test_running_old_banner_is_not_hidden_by_fixed_installed_package() -> None:
    running = {**service("2.4.59-1ubuntu1"), "banner": "Server: Apache/2.4.58"}
    checks = [{
        "cve_id": "CVE-2024-27316", "package_name": "apache2-bin",
        "source_name": "Ubuntu Security Notices", "outcome": "not_vulnerable",
    }]
    findings = match_service_advisories([running], [advisory()], [], checks)
    assert findings[0]["conclusion"] == "manual_confirmation"
    assert any(
        item["type"] == "upstream_observed_version" and item["value"] == "2.4.58"
        for item in findings[0]["evidence_chain"]
    )


def test_vendor_finding_has_priority_without_suppressing_another_package() -> None:
    vendor_finding = {
        "cve_id": "CVE-2024-27316", "package_name": "apache2-bin",
        "source_name": "Ubuntu Security Notices",
    }
    assert match_service_advisories([service()], [advisory()], [vendor_finding], []) == []
    different = {**vendor_finding, "package_name": "other-httpd"}
    assert len(match_service_advisories([service()], [advisory()], [different], [])) == 1


def test_multiple_listeners_for_same_package_are_combined() -> None:
    findings = match_service_advisories(
        [service(), {**service(), "listen": "[::]:80"}], [advisory()], [], [],
    )
    assert len(findings) == 1
    assert findings[0]["service"]["listen"] == "0.0.0.0:80、[::]:80"


def test_source_ranges_do_not_replace_product_version_with_platform_version() -> None:
    nginx = advisory()
    nginx.affected_packages = [{
        "name": "nginx", "affected_version": "1.25.0-1.25.3", "fixed_version": "1.25.4",
        "upstream_product": "nginx",
        "upstream_ranges": [{"introduced": "1.25.0", "last_affected": "1.25.3"}],
        "upstream_fixed_versions": ["1.25.4"],
    }]
    running = {
        **service("1:1.25.3-1.el9"), "process_name": "nginx",
        "executable": "/usr/sbin/nginx", "package_name": "nginx-core",
    }
    assert len(match_service_advisories([running], [nginx], [], [])) == 1
    running["installed_version"] = "1:1.26.0-1.el9"
    assert match_service_advisories([running], [nginx], [], []) == []


def test_nginx_maintained_stable_fix_overrides_wide_upstream_range() -> None:
    ranges = [{"introduced": "1.5.13", "last_affected": "1.27.0"}]
    fixed = ["1.27.1", "1.26.2"]
    assert affected_version("1.26.1", ranges, fixed)
    assert not affected_version("1.26.2", ranges, fixed)
    assert affected_version("1.27.0", ranges, fixed)
    assert not affected_version("1.27.1", ranges, fixed)


def test_scan_engine_uses_upstream_only_for_live_service_or_comprehensive_scan() -> None:
    with patch("src.services.scan_engine._is_older") as compare:
        host = scan_engine._match_advisories(
            object(), "dpkg", {"apache2-bin": "2.4.58-1ubuntu1.9"},
            [advisory()], "ubuntu",
        )
        running = scan_engine._match_advisories(
            object(), "dpkg", {"apache2-bin": "2.4.58-1ubuntu1.9"},
            [advisory()], "ubuntu", [service()], ScanType.SERVICE_VULNERABILITY,
        )
    assert host == []
    assert len(running) == 1
    compare.assert_not_called()
