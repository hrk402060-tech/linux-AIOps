from unittest.mock import patch

from src.models.knowledge import KnowledgeAdvisory
from src.services import scan_engine


def test_dpkg_collection_keeps_source_package_mapping() -> None:
    output = "openssl:amd64\t3.0.2-1\topenssl (3.0.2-1)\n"
    with patch("src.services.scan_engine._execute", return_value=output):
        packages = scan_engine._collect_packages(object(), "dpkg")
    assert packages["openssl"] == "3.0.2-1"
    assert packages["__source__:openssl"] == "3.0.2-1"


def test_debian_source_advisory_matches_major_release_without_binary_guess() -> None:
    advisory = KnowledgeAdvisory(
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
    with patch("src.services.scan_engine._is_older", return_value=True):
        findings = scan_engine._match_advisories(
            object(), "dpkg", {"__source__:openssl": "3.0.2-1"},
            [advisory], "debian", os_version="12.12",
        )
    assert len(findings) == 1
    assert findings[0]["conclusion"] == "confirmed"
