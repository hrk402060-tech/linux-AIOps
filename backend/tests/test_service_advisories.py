from datetime import timezone
from unittest.mock import patch

import pytest

from src.services import knowledge, service_advisories
from src.services.service_advisory_sources import apache_advisory, nginx_entries
from src.services.service_advisory_versions import parse_nginx_ranges


NGINX_XML = b"""<!DOCTYPE article SYSTEM "../../dtd/article.dtd">
<article><section><security>
<item name="HTTP/2 memory consumption" severity="medium" cve="2024-24989"
      good="1.25.4+, 1.24.1+" vulnerable="1.25.0-1.25.3" />
<item name="Windows only" cve="2011-4963" good="1.3.1+"
      vulnerable="nginx/Windows 0.7.52-1.3.0" />
</security></section></article>"""


def cve_record(cve_id: str = "CVE-2024-27316") -> dict[str, object]:
    return {
        "cveMetadata": {
            "cveId": cve_id, "state": "PUBLISHED",
            "datePublished": "2024-04-04T19:21:41.984Z",
            "dateUpdated": "2025-11-04T22:06:02.830Z",
        },
    }


def apache_record() -> dict[str, object]:
    return {
        "cveMetadata": {"cveId": "CVE-2024-27316", "state": "PUBLISHED"},
        "containers": {"cna": {
            "title": "Apache HTTP Server: HTTP/2 memory exhaustion",
            "affected": [{
                "product": "Apache HTTP Server", "defaultStatus": "unaffected",
                "versions": [{
                    "versionType": "semver", "version": "2.4.17",
                    "lessThanOrEqual": "2.4.58", "status": "affected",
                }],
            }],
            "metrics": [{"other": {"content": {"text": "moderate"}}}],
            "timeline": [{"time": "2024-04-04", "value": "2.4.59 released"}],
        }},
    }


def test_nginx_uses_official_structured_ranges_and_skips_windows_only() -> None:
    entries = nginx_entries(NGINX_XML)
    assert len(entries) == 1
    assert entries[0]["cve_id"] == "CVE-2024-24989"
    assert entries[0]["ranges"] == [{
        "introduced": "1.25.0", "last_affected": "1.25.3",
    }]
    assert entries[0]["fixed_versions"] == ["1.25.4", "1.24.1"]


def test_nginx_rejects_entities_instead_of_resolving_external_xml() -> None:
    with pytest.raises(knowledge.KnowledgeImportError):
        nginx_entries(b'<!DOCTYPE x [<!ENTITY x "EXPANDED">]><article>&x;</article>')


def test_nginx_exact_version_and_multiple_ranges_are_preserved() -> None:
    assert parse_nginx_ranges("1.25.0-1.25.5, 1.26.0") == [
        {"introduced": "1.25.0", "last_affected": "1.25.5"},
        {"introduced": "1.26.0", "last_affected": "1.26.0"},
    ]


def test_apache_inclusive_range_and_released_fixed_version_remain_distinct() -> None:
    advisory = apache_advisory(apache_record(), cve_record())
    package = advisory.affected_packages[0]
    assert package.upstream_product == "apache-httpd"
    assert package.upstream_ranges[0].introduced == "2.4.17"
    assert package.upstream_ranges[0].last_affected == "2.4.58"
    assert package.upstream_ranges[0].fixed is None
    assert package.upstream_fixed_versions == ["2.4.59"]
    assert advisory.os_family == "other"
    assert advisory.severity == "medium"
    assert advisory.published_at.tzinfo == timezone.utc
    assert advisory.published_at.year == 2024


def test_apache_real_zero_lower_bound_means_all_earlier_numeric_versions() -> None:
    record = apache_record()
    record["containers"]["cna"]["affected"][0]["versions"][0]["version"] = "0"
    advisory = apache_advisory(record, cve_record())
    assert advisory.affected_packages[0].upstream_ranges[0].introduced == "0"


@pytest.mark.parametrize("change", ["identity", "unpublished", "date"])
def test_cve_metadata_must_be_published_and_bound_to_expected_cve(change: str) -> None:
    record = cve_record()
    if change == "identity":
        record["cveMetadata"]["cveId"] = "CVE-2024-9999"
    elif change == "unpublished":
        record["cveMetadata"]["state"] = "REJECTED"
    else:
        record["cveMetadata"].pop("datePublished")
    with pytest.raises(knowledge.KnowledgeImportError):
        apache_advisory(apache_record(), record)


def test_apache_does_not_treat_unaffected_versions_as_affected() -> None:
    record = apache_record()
    versions = record["containers"]["cna"]["affected"][0]["versions"]
    versions.append({
        "version": "2.4.59", "status": "unaffected", "versionType": "semver",
    })
    advisory = apache_advisory(record, cve_record())
    assert len(advisory.affected_packages[0].upstream_ranges) == 1


def test_apache_unsupported_version_rules_fail_closed() -> None:
    record = apache_record()
    record["containers"]["cna"]["affected"][0]["versions"][0]["changes"] = [
        {"at": "2.4.50", "status": "unaffected"},
    ]
    with pytest.raises(knowledge.KnowledgeImportError):
        apache_advisory(record, cve_record())


def test_cve_record_url_uses_official_bucket_without_user_url_input() -> None:
    assert service_advisories.cve_record_url("CVE-2024-27316").endswith(
        "/cves/2024/27xxx/CVE-2024-27316.json"
    )
    with pytest.raises(knowledge.KnowledgeImportError):
        service_advisories.cve_record_url("https://outside.invalid/payload")


def test_source_fetch_has_a_bounded_batch_and_keeps_cve_dates() -> None:
    def read_json(url: str) -> dict[str, object]:
        if "httpd.apache.org" in url:
            return apache_record()
        return cve_record("CVE-2024-24989" if "24989" in url else "CVE-2024-27316")

    listing = b'<a href="CVE-2024-27316.json">CVE</a>'
    with patch.object(service_advisories, "read_bytes", side_effect=[NGINX_XML, listing]), \
            patch.object(service_advisories, "read_json", side_effect=read_json):
        package = service_advisories.fetch_package(2)
    assert package.source_name == service_advisories.SOURCE_NAME
    assert len(package.advisories) == 2
    assert {entry.affected_packages[0].upstream_product for entry in package.advisories} == {
        "nginx", "apache-httpd",
    }
    assert all(entry.published_at.year == 2024 for entry in package.advisories)


def test_source_failure_records_failed_run_without_publishing() -> None:
    from unittest.mock import Mock

    session, source, run = Mock(), Mock(), Mock()
    with patch.object(service_advisories.knowledge_publish, "start_source_run", return_value=run), \
            patch.object(service_advisories, "fetch_package",
                         side_effect=knowledge.KnowledgeImportError("invalid official data")), \
            patch.object(service_advisories.knowledge_publish, "fail_source_run") as fail, \
            patch.object(service_advisories.knowledge_publish, "publish_source_package") as publish:
        with pytest.raises(knowledge.KnowledgeImportError):
            service_advisories.sync_source(session, source)
    fail.assert_called_once()
    publish.assert_not_called()
