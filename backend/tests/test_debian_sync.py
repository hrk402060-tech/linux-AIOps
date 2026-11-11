from copy import deepcopy
from unittest.mock import patch

import pytest

from src.services import debian_sync, knowledge


TRACKER = {
    "openssl": {
        "CVE-2024-12345": {
            "description": "OpenSSL vulnerability",
            "releases": {
                "bookworm": {"status": "resolved", "fixed_version": "3.0.14-1~deb12u2", "urgency": "high"},
                "trixie": {"status": "resolved", "fixed_version": "0", "urgency": "unimportant"},
                "sid": {"status": "resolved", "fixed_version": "3.3.2-1", "urgency": "medium"},
            },
        },
    },
}
DSA_LIST = """[12 Sep 2024] DSA-5777-1 openssl - security update
    {CVE-2024-12345}
    [bookworm] - openssl 3.0.14-1~deb12u2
    [trixie] - openssl 0
    [sid] - openssl 3.3.2-1
"""


def test_debian_uses_real_dsa_date_and_exact_supported_release() -> None:
    package = debian_sync.build_package(TRACKER, DSA_LIST, batch_size=20)
    advisory = package.advisories[0]
    assert package.source_name == "Debian Security Advisories"
    assert advisory.advisory_id == "DSA-5777-1"
    assert str(advisory.vendor_url) == "https://www.debian.org/security/2024/dsa-5777"
    assert advisory.published_at.isoformat() == "2024-09-12T00:00:00+00:00"
    assert advisory.severity == "high"
    assert len(advisory.affected_packages) == 1
    affected = advisory.affected_packages[0]
    assert affected.name == "openssl"
    assert affected.package_scope == "source"
    assert affected.fixed_version == "3.0.14-1~deb12u2"
    assert affected.affected_version == "< 3.0.14-1~deb12u2"
    assert affected.os_versions == ["12"]


@pytest.mark.parametrize("status,fixed", [
    ("open", "3.0.14-1~deb12u2"), ("resolved", "0"),
    ("resolved", ""), ("resolved", "3.0.14-1~deb12u3"),
])
def test_debian_does_not_invent_fixes_or_reuse_an_unmatched_dsa(status: str, fixed: str) -> None:
    tracker = deepcopy(TRACKER)
    tracker["openssl"]["CVE-2024-12345"]["releases"]["bookworm"].update(
        status=status, fixed_version=fixed,
    )
    with pytest.raises(knowledge.KnowledgeImportError, match="可验证"):
        debian_sync.build_package(tracker, DSA_LIST)


@pytest.mark.parametrize("dsa_list", [
    "", DSA_LIST.replace("12 Sep 2024", "unknown"),
    DSA_LIST.replace("CVE-2024-12345", "CVE-invalid"),
])
def test_debian_requires_real_advisory_and_date(dsa_list: str) -> None:
    with pytest.raises(knowledge.KnowledgeImportError):
        debian_sync.build_package(TRACKER, dsa_list)


def test_debian_fetch_bounds_the_official_tracker_and_dsa_downloads() -> None:
    with patch("src.services.debian_sync.read_json", return_value=TRACKER) as read_json, patch(
        "src.services.debian_sync.read_bytes", return_value=DSA_LIST.encode(),
    ) as read_bytes:
        package = debian_sync.fetch_package(batch_size=1)
    assert read_json.call_args.args[0] == "https://security-tracker.debian.org/tracker/data/json"
    assert read_json.call_args.kwargs["max_bytes"] == 64 * 1024 * 1024
    assert read_bytes.call_args.args[0].startswith("https://salsa.debian.org/security-tracker-team/")
    assert package.advisories
    with pytest.raises(knowledge.KnowledgeImportError):
        debian_sync.fetch_package(batch_size=101)
