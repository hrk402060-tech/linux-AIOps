from datetime import datetime, timezone

import pytest

from src.services.knowledge import KnowledgeImportError
from src.services.redhat_sync import build_package


def detail() -> dict[str, object]:
    return {
        "name": "CVE-2026-12345", "threat_severity": "Important",
        "public_date": "2026-11-01T00:00:00Z",
        "bugzilla": {"description": "kernel security update"},
        "affected_release": [{
            "product_name": "Red Hat Enterprise Linux 9",
            "release_date": "2026-11-03T00:00:00Z",
            "advisory": "RHSA-2026:12345",
            "cpe": "cpe:/o:redhat:enterprise_linux:9",
            "package": "kernel-0:5.14.0-570.50.1.el9_6",
        }],
    }


def test_redhat_uses_official_fixed_evr_and_release_branch() -> None:
    package = build_package([detail()])
    advisory = package.advisories[0]
    assert advisory.os_family == "rhel"
    assert advisory.advisory_id == "RHSA-2026:12345"
    assert advisory.severity == "high"
    assert advisory.published_at == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert advisory.updated_at == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert advisory.affected_packages[0].name == "kernel"
    assert advisory.affected_packages[0].fixed_version == "0:5.14.0-570.50.1.el9_6"
    assert advisory.affected_packages[0].os_versions == ["9.6"]


def test_redhat_skips_eus_and_non_rhel_products_and_deduplicates() -> None:
    value = detail()
    records = value["affected_release"]
    assert isinstance(records, list)
    records.extend([
        dict(records[0]),
        {**records[0], "product_name": "Red Hat Enterprise Linux 9.2 Extended Update Support",
         "cpe": "cpe:/o:redhat:rhel_eus:9.2"},
        {**records[0], "product_name": "Red Hat OpenShift Container Platform"},
    ])
    package = build_package([value])
    assert len(package.advisories) == 1
    assert len(package.advisories[0].affected_packages) == 1


@pytest.mark.parametrize("field,value", [
    ("affected_release", []), ("public_date", "not-a-date"), ("name", "invalid"),
])
def test_redhat_rejects_unverifiable_data(field: str, value: object) -> None:
    record = detail()
    record[field] = value
    with pytest.raises(KnowledgeImportError):
        build_package([record])
