from unittest.mock import Mock, patch

import paramiko
import pytest

from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.models.credential import Credential, CredentialType
from src.models.knowledge import KnowledgeAdvisory
from src.models.scan_task import ScanType
from src.services import scan_engine


def _asset(port: int = 22) -> Asset:
    return Asset(
        id="asset-1", name="collector-node", host="10.0.0.50", ssh_port=port,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.TEST,
        group_name="test", credential_id="credential-1", labels=[],
    )


def _credential() -> Credential:
    return Credential(
        id="credential-1", name="collector", credential_type=CredentialType.PASSWORD,
        username="ops", encrypted_secret=b"cipher", nonce=b"123456789012",
        key_version="test-v1",
    )


def test_connect_registers_only_confirmed_host_key() -> None:
    expected = paramiko.RSAKey.generate(1024)
    client = Mock()
    client.get_transport.return_value.get_remote_server_key.return_value = expected

    with patch("src.services.scan_engine.paramiko.SSHClient", return_value=client):
        connected = scan_engine._connect(
            _asset(port=2222), _credential(), "secret", expected
        )

    assert connected is client
    client.get_host_keys.return_value.add.assert_called_once_with(
        "[10.0.0.50]:2222", expected.get_name(), expected
    )
    policy = client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.RejectPolicy)
    client.load_system_host_keys.assert_not_called()


def test_connect_rejects_remote_host_key_mismatch() -> None:
    expected = paramiko.RSAKey.generate(1024)
    actual = paramiko.RSAKey.generate(1024)
    client = Mock()
    client.get_transport.return_value.get_remote_server_key.return_value = actual

    with patch("src.services.scan_engine.paramiko.SSHClient", return_value=client):
        with pytest.raises(scan_engine.ScanCollectionError, match="主机密钥不匹配"):
            scan_engine._connect(_asset(), _credential(), "secret", expected)

    client.close.assert_called_once()


def _advisory(source: str, advisory_id: str) -> KnowledgeAdvisory:
    return KnowledgeAdvisory(
        id=advisory_id, release_id="release-1", cve_id="CVE-2026-9001",
        advisory_id=advisory_id, title="内核安全更新", severity="high",
        os_family="openeuler", source_name=source,
        vendor_url="https://security.example.test/advisory",
        affected_packages=[{
            "name": "kernel", "affected_version": "< 2", "fixed_version": "2",
        }],
    )


def test_vendor_advisory_confirms_but_supplemental_source_requires_review() -> None:
    advisories = [
        _advisory("openEuler Security Advisories", "openEuler-SA-2026-9001"),
        _advisory("CNVD", "CNVD-2026-9001"),
    ]

    with patch("src.services.scan_engine._is_older", return_value=True):
        findings = scan_engine._match_advisories(
            object(), "rpm", {"kernel": "1"}, advisories, "openeuler"
        )

    assert [item["conclusion"] for item in findings] == [
        "confirmed", "manual_confirmation",
    ]
    assert findings[0]["confidence"] == "high"
    assert findings[1]["confidence"] == "low"
    assert [step["type"] for step in findings[0]["evidence_chain"]] == [
        "source", "advisory", "installed_version", "fixed_version", "conclusion",
    ]


def test_vendor_advisory_is_first_even_when_sources_are_reversed() -> None:
    advisories = [
        _advisory("CNVD", "CNVD-2026-9001"),
        _advisory("openEuler Security Advisories", "openEuler-SA-2026-9001"),
    ]

    with patch("src.services.scan_engine._is_older", return_value=True):
        findings = scan_engine._match_advisories(
            object(), "rpm", {"kernel": "1"}, advisories, "openeuler"
        )

    assert findings[0]["source_name"] == "openEuler Security Advisories"
    assert findings[0]["conclusion"] == "confirmed"


def test_native_version_rule_suppresses_fixed_package() -> None:
    with patch("src.services.scan_engine._is_older", return_value=False):
        findings = scan_engine._match_advisories(
            object(), "rpm", {"kernel": "2"},
            [_advisory("openEuler Security Advisories", "openEuler-SA-2026-9001")],
            "openeuler",
        )

    assert findings == []


def test_service_scan_matches_only_package_owned_by_listening_process() -> None:
    service_advisory = _advisory(
        "openEuler Security Advisories", "openEuler-SA-2026-9001"
    )
    service_advisory.affected_packages = [{
        "name": "openssh-server", "affected_version": "< 2",
        "fixed_version": "2",
    }]
    services = [{
        "protocol": "tcp", "listen": "0.0.0.0:22", "process": "sshd",
        "process_name": "sshd", "pid": "123", "executable": "/usr/sbin/sshd",
        "package_name": "openssh-server", "installed_version": "1",
    }, {
        "protocol": "tcp6", "listen": "[::]:22", "process": "sshd",
        "process_name": "sshd", "pid": "123", "executable": "/usr/sbin/sshd",
        "package_name": "openssh-server", "installed_version": "1",
    }]

    with patch("src.services.scan_engine._is_older", return_value=True):
        findings = scan_engine._match_advisories(
            object(), "rpm", {"kernel": "1", "openssh-server": "1"},
            [service_advisory], "openeuler", services, ScanType.SERVICE_VULNERABILITY,
        )

    assert len(findings) == 1
    assert findings[0]["detection_scope"] == "service"
    assert findings[0]["service"]["listen"] == "0.0.0.0:22、[::]:22"
    assert [step["type"] for step in findings[0]["evidence_chain"]][-5:] == [
        "service_listener", "service_process", "service_executable",
        "service_package", "conclusion",
    ]


def test_service_scan_does_not_infer_vulnerability_from_unmapped_process() -> None:
    services = [{
        "protocol": "tcp", "listen": "0.0.0.0:22", "process": "sshd",
        "process_name": "sshd", "pid": "123", "executable": "",
        "package_name": "", "installed_version": "",
    }]

    with patch("src.services.scan_engine._is_older") as compare:
        findings = scan_engine._match_advisories(
            object(), "rpm", {"kernel": "1"},
            [_advisory("openEuler Security Advisories", "openEuler-SA-2026-9001")],
            "openeuler", services, ScanType.SERVICE_VULNERABILITY,
        )

    assert findings == []
    compare.assert_not_called()


def test_service_scan_honors_vendor_banner_keywords_without_using_them_as_verdict() -> None:
    advisory = _advisory(
        "openEuler Security Advisories", "openEuler-SA-2026-9001"
    )
    advisory.affected_packages = [{
        "name": "openssh-server", "affected_version": "< 2",
        "fixed_version": "2", "service_banner_keywords": ["openssh_9.3"],
    }]
    matching = [{
        "protocol": "tcp", "listen": "0.0.0.0:22", "process": "sshd",
        "process_name": "sshd", "pid": "123", "executable": "/usr/sbin/sshd",
        "package_name": "openssh-server", "installed_version": "1",
        "banner": "SSH-2.0-OpenSSH_9.3",
    }]
    non_matching = [{**matching[0], "banner": "SSH-2.0-OpenSSH_9.7"}]

    with patch("src.services.scan_engine._is_older", return_value=True):
        findings = scan_engine._match_advisories(
            object(), "rpm", {"openssh-server": "1"}, [advisory], "openeuler",
            matching, ScanType.SERVICE_VULNERABILITY,
        )
        suppressed = scan_engine._match_advisories(
            object(), "rpm", {"openssh-server": "1"}, [advisory], "openeuler",
            non_matching, ScanType.SERVICE_VULNERABILITY,
        )

    assert findings[0]["service"]["banner"] == "SSH-2.0-OpenSSH_9.3"
    assert any(step["type"] == "service_banner" for step in findings[0]["evidence_chain"])
    assert suppressed == []


def test_process_metadata_parser_accepts_ss_output() -> None:
    assert scan_engine._parse_process('users:(("sshd",pid=123,fd=3))') == (
        "sshd", "123",
    )


def test_service_collection_caps_optional_banner_probes_per_asset() -> None:
    line = 'tcp LISTEN 0 128 127.0.0.1:22 0.0.0.0:* users:(("sshd",pid=123,fd=3))'
    with patch("src.services.scan_engine._execute", return_value="\n".join([line] * 20)), \
        patch("src.services.scan_engine._resolve_service_package", return_value=(
            "/usr/sbin/sshd", "openssh-server"
        )), \
        patch("src.services.scan_engine.probe_banner", return_value="SSH-2.0-OpenSSH_9.3") as probe:
        services = scan_engine._collect_services(
            object(), "rpm", {"openssh-server": "1"}
        )

    assert len(services) == 20
    assert probe.call_count == scan_engine.MAX_BANNER_PROBES


def test_openeuler_advisory_matches_only_declared_product_version() -> None:
    advisory = _advisory(
        "openEuler Security Advisories", "openEuler-SA-2026-9001"
    )
    advisory.affected_packages[0]["os_versions"] = ["24.03-LTS-SP4"]

    with patch("src.services.scan_engine._is_older", return_value=True) as compare:
        mismatched = scan_engine._match_advisories(
            object(), "rpm", {"kernel": "1"}, [advisory], "openeuler",
            os_version="24.03 (LTS-SP3)",
        )
        matched = scan_engine._match_advisories(
            object(), "rpm", {"kernel": "1"}, [advisory], "openeuler",
            os_version="24.03 (LTS-SP4)",
        )

    assert mismatched == []
    assert len(matched) == 1
    assert compare.call_count == 1


def test_full_os_release_version_keeps_service_pack_evidence() -> None:
    os_data = scan_engine._parse_os_release(
        'ID=openeuler\nVERSION_ID="24.03"\nVERSION="24.03 (LTS-SP4)"\n'
    )
    assert os_data["version"] == "24.03 (LTS-SP4)"
    assert scan_engine._matches_os_version(os_data["version"], ["24.03-LTS-SP4"])
