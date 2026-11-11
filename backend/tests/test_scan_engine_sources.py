from unittest.mock import patch

import pytest

from src.models.knowledge import KnowledgeAdvisory
from src.models.scan_task import ScanType
from src.services import scan_engine
from src.services.scan_package_inventory import parse_inventory


def test_dpkg_collection_keeps_source_package_mapping() -> None:
    output = "openssl:amd64\t3.0.2-1+b1\topenssl\t3.0.2-1\tinstalled\n"
    with patch("src.services.scan_engine._execute", return_value=output):
        packages = scan_engine._collect_packages(object(), "dpkg")
    assert packages == {"openssl": "3.0.2-1+b1"}
    assert packages.sources["openssl"] == ("openssl", "3.0.2-1")


def debian_advisory() -> KnowledgeAdvisory:
    return KnowledgeAdvisory(
        id="dsa", release_id="release", cve_id="CVE-2026-12345",
        advisory_id="DSA-2026-1234", title="openssl update", severity="high",
        os_family="debian", source_name="Debian Security Advisories",
        vendor_url="https://www.debian.org/security/2026/dsa-1234",
        affected_packages=[{
            "name": "openssl", "package_scope": "source",
            "affected_version": "< 3.0.2-2", "fixed_version": "3.0.2-2",
            "os_versions": ["12"],
        }],
    )


def test_debian_source_advisory_matches_real_binary_and_source_version() -> None:
    inventory = parse_inventory(
        "libssl3\t3.0.2-1+b1\topenssl\t3.0.2-1\tinstalled\n", "dpkg",
    )
    with patch("src.services.scan_engine._is_older", return_value=True) as compare:
        findings = scan_engine._match_advisories(
            object(), "dpkg", inventory, [debian_advisory()], "debian", os_version="12.12",
        )
    assert len(findings) == 1
    assert findings[0]["package_name"] == "libssl3"
    assert findings[0]["installed_version"] == "3.0.2-1+b1"
    assert findings[0]["conclusion"] == "confirmed"
    assert compare.call_args.args[-2:] == ("3.0.2-1", "3.0.2-2")
    assert {"type": "source_package", "value": "openssl"} in findings[0]["evidence_chain"]


def test_source_inventory_preserves_each_binary_version_and_ignores_removed_packages() -> None:
    inventory = parse_inventory(
        "libssl3\t3.0.2-1\topenssl\t3.0.2-1\tinstalled\n"
        "openssl\t3.0.2-3\topenssl\t3.0.2-3\tinstalled\n"
        "oldssl\t2.0-1\toldssl\t2.0-1\tconfig-files\n", "dpkg",
    )
    with patch("src.services.scan_engine._is_older", side_effect=[True, False]):
        findings = scan_engine._match_advisories(
            object(), "dpkg", inventory, [debian_advisory()], "debian", os_version="12",
        )
    assert "oldssl" not in inventory
    assert [finding["package_name"] for finding in findings] == ["libssl3"]


def test_source_package_metadata_is_required_before_claiming_absence_or_fixed() -> None:
    checks: list[dict[str, object]] = []
    with patch("src.services.scan_engine._is_older") as compare:
        assert scan_engine._match_advisories(
            object(), "dpkg", {"openssl": "3.0.2-1"}, [debian_advisory()], "debian",
            os_version="12", remediation_checks=checks,
        ) == []
    compare.assert_not_called()
    assert checks == []


def test_source_advisory_matches_running_binary_in_service_scan() -> None:
    inventory = parse_inventory(
        "libssl3\t3.0.2-1\topenssl\t3.0.2-1\tinstalled\n", "dpkg",
    )
    running = {
        "package_name": "libssl3", "listen": "0.0.0.0:443",
        "process_name": "ssl", "process": "ssl", "executable": "/usr/bin/ssl",
    }
    with patch("src.services.scan_engine._is_older", return_value=True):
        findings = scan_engine._match_advisories(
            object(), "dpkg", inventory, [debian_advisory()], "debian",
            [running], ScanType.SERVICE_VULNERABILITY, os_version="12",
        )
    assert findings[0]["service"]["package_name"] == "libssl3"


@pytest.mark.parametrize("family", ["centos", "rocky", "almalinux"])
def test_rhel_vendor_versions_are_never_applied_to_compatible_distributions(family: str) -> None:
    advisory = debian_advisory()
    advisory.os_family = "rhel"
    advisory.source_name = "Red Hat Security Advisories"
    advisory.affected_packages = [{
        "name": "kernel", "fixed_version": "2-1", "os_versions": ["9"],
    }]
    with patch("src.services.scan_engine._is_older") as compare:
        assert scan_engine._match_advisories(
            object(), "rpm", {"kernel": "1-1"}, [advisory], family, os_version="9.6",
        ) == []
    compare.assert_not_called()


@pytest.mark.parametrize("older", [True, False])
def test_uncertain_vendor_mapping_never_confirms_or_proves_remediation(older: bool) -> None:
    advisory = debian_advisory()
    advisory.source_name = "Ubuntu Security Notices"
    advisory.os_family = "ubuntu"
    advisory.affected_packages = [{
        "name": "openssl", "fixed_version": "3.0.2-2", "requires_manual_confirmation": True,
    }]
    checks: list[dict[str, object]] = []
    with patch("src.services.scan_engine._is_older", return_value=older):
        findings = scan_engine._match_advisories(
            object(), "dpkg", {"openssl": "3.0.2-1"}, [advisory], "ubuntu",
            os_version="22.04", remediation_checks=checks,
        )
    if older:
        assert findings[0]["conclusion"] == "manual_confirmation"
    else:
        assert findings == []
    assert checks == []
