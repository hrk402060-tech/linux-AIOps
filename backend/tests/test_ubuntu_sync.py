from copy import deepcopy
from unittest.mock import patch

import pytest

from src.services import knowledge, ubuntu_sync


NOTICE = {
    "id": "USN-7000-1", "title": "OpenSSL vulnerabilities",
    "published": "2024-09-12T12:00:00Z",
    "cves": [{"id": "CVE-2024-12345", "priority": "high"}],
    "releases": [
        {"codename": "jammy", "version": "22.04 LTS"},
        {"codename": "noble", "version": "24.04 LTS"},
    ],
    "release_packages": {
        "jammy": [{"name": "libssl3", "version": "3.0.2-0ubuntu1.18"}],
        "noble": [{"name": "libssl3t64", "version": "3.0.13-0ubuntu3.4"}],
    },
}


def test_ubuntu_keeps_vendor_backport_version_and_exact_release() -> None:
    package = ubuntu_sync.build_package([NOTICE])
    advisory = package.advisories[0]
    assert package.source_name == "Ubuntu Security Notices"
    assert advisory.cve_id == "CVE-2024-12345"
    assert str(advisory.vendor_url) == "https://ubuntu.com/security/notices/USN-7000-1"
    assert advisory.severity == "high"
    assert advisory.published_at.isoformat() == "2024-09-12T12:00:00+00:00"
    assert {(item.name, item.fixed_version, tuple(item.os_versions))
            for item in advisory.affected_packages} == {
        ("libssl3", "3.0.2-0ubuntu1.18", ("22.04",)),
        ("libssl3t64", "3.0.13-0ubuntu3.4", ("24.04",)),
    }
    assert all(item.affected_version == f"< {item.fixed_version}"
               for item in advisory.affected_packages)
    assert all(item.requires_manual_confirmation and "CVE" in item.evidence_reason
               for item in advisory.affected_packages)


def test_ubuntu_matches_the_canonical_notice_package_visibility_contract() -> None:
    notice = deepcopy(NOTICE)
    notice["release_packages"]["jammy"].extend([
        {"name": "openssl", "version": "3.0.2-0ubuntu1.18", "is_source": True},
        {"name": "hidden-package", "version": "1.0", "is_source": False, "is_visible": False},
    ])
    package = ubuntu_sync.build_package([notice])
    assert {item.name for item in package.advisories[0].affected_packages} == {
        "libssl3", "libssl3t64",
    }


def test_ubuntu_never_assigns_unknown_release_packages_to_all_releases() -> None:
    notice = deepcopy(NOTICE)
    notice["release_packages"] = {"unknown": [{"name": "openssl", "version": "3.0.1"}]}
    with pytest.raises(knowledge.KnowledgeImportError, match="可验证"):
        ubuntu_sync.build_package([notice])


@pytest.mark.parametrize("key,value", [
    ("published", ""), ("published", "not-a-date"), ("cves", ["not-a-cve"]),
    ("release_packages", {}), ("id", "../../other"),
])
def test_ubuntu_rejects_unverifiable_notices(key: str, value: object) -> None:
    notice = {**NOTICE, key: value}
    with pytest.raises(knowledge.KnowledgeImportError):
        ubuntu_sync.build_package([notice])


def test_ubuntu_fetch_uses_official_ids_and_bounded_batch() -> None:
    with patch("src.services.ubuntu_sync.read_json") as request:
        request.side_effect = [{"notices": [{"id": NOTICE["id"]}]}, NOTICE]
        package = ubuntu_sync.fetch_package(batch_size=1)
    assert package.advisories[0].advisory_id == NOTICE["id"]
    assert request.call_args_list[0].args[0] == "https://ubuntu.com/security/page/notices.json?limit=1"
    assert request.call_args_list[1].args[0] == "https://ubuntu.com/security/notices/USN-7000-1.json"
    with pytest.raises(knowledge.KnowledgeImportError):
        ubuntu_sync.fetch_package(batch_size=101)


def test_ubuntu_rejects_mismatched_notice_details_and_is_deterministic() -> None:
    with patch("src.services.ubuntu_sync.read_json") as request:
        request.side_effect = [{"notices": [{"id": "USN-7000-1"}]}, {**NOTICE, "id": "USN-7000-2"}]
        with pytest.raises(knowledge.KnowledgeImportError, match="标识"):
            ubuntu_sync.fetch_package(1)
    assert ubuntu_sync.build_package([NOTICE]).version == ubuntu_sync.build_package([NOTICE]).version
