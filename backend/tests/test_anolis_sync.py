from src.services.anolis_parser import parse_notice
from src.services.anolis_sync import build_package


def notice() -> dict[str, object]:
    return {
        "id": 123, "advisory_type": 3, "is_publish": True,
        "advisory_id": "ANSA-2026:1234",
        "publish_date": "2026-11-01T00:00:00Z",
        "gmt_modified": "2026-11-02T00:00:00Z",
        "synpopsis": "openssl security update", "severity": "high",
        "cve": [{"cve_id": "CVE-2026-12345"}],
        "product": [{
            "name_version": "Anolis OS 8",
            "product_package_info": {
                "x86_64": [{
                    "rpm_name": "openssl-libs",
                    "rpm_filename": "openssl-libs-3.0.7-25.0.1.1.oe2309.x86_64.rpm",
                }, {
                    "rpm_name": "openssl-libs",
                    "rpm_filename": "openssl-libs-3.0.7-25.0.1.1.oe2309.src.rpm",
                }],
            },
        }],
    }


def test_anolis_parser_keeps_binary_rpm_and_manual_evidence() -> None:
    advisories = parse_notice(notice())
    assert len(advisories) == 1
    package = advisories[0].affected_packages[0]
    assert package.name == "openssl-libs"
    assert package.fixed_version == "3.0.7-25.0.1.1.oe2309"
    assert package.requires_manual_confirmation is True
    assert package.os_versions == ["8"]


def test_anolis_package_has_stable_identity() -> None:
    package = build_package({"status": {"code": 200}, "data": {"data": [notice()]}}, 1)
    assert package.source_name == "Anolis Security Advisories"
    assert package.advisories[0].advisory_id == "ANSA-2026:1234"
