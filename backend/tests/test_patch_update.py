from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.models.credential import Credential, CredentialType
from src.models.ssh_host_key import SshHostKey
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding
from src.services import patch_execution, rpm_patch


def test_rpm_update_is_limited_to_the_confirmed_name_epoch_version_and_arch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []
    outputs = iter([
        (0, "openssl\t0:3.0.1-1.oe2403\tx86_64\n"),
        (0, "dnf\n"), (0, "openssl\t0:3.0.1-2.oe2403\tx86_64\n"),
        (0, "-1\n"), (0, ""),
        (0, "openssl\t0:3.0.1-2.oe2403\tx86_64\n"),
    ])

    def run(_client: object, command: str, _deadline: float) -> tuple[int, str]:
        commands.append(command)
        return next(outputs)

    monkeypatch.setattr(rpm_patch, "_run", run)
    rpm_patch.update_package(Mock(), "openssl", "0:3.0.1-1.oe2403", "3.0.1-2.oe2403")
    assert "openssl-0:3.0.1-2.oe2403.x86_64" in commands[4]
    assert "--setopt=gpgcheck=1" in commands[4]
    assert "obsoletes=0" in commands[4]
    assert "upgrade" in commands[4]
    assert "downgrade" not in commands[4]


@pytest.mark.parametrize("installed, comparison", [
    ("0:3.0.1-3.oe2403", "-1"),
    ("0:3.0.1-1.oe2403", "1"),
])
def test_stale_scan_or_lower_target_never_dispatches_update(
    monkeypatch: pytest.MonkeyPatch, installed: str, comparison: str,
) -> None:
    run = Mock(side_effect=[
        (0, f"openssl\t{installed}\tx86_64\n"), (0, "dnf\n"),
        (0, "openssl\t0:3.0.1-2.oe2403\tx86_64\n"), (0, comparison),
    ])
    monkeypatch.setattr(rpm_patch, "_run", run)
    with pytest.raises(rpm_patch.PatchUpdateError) as error:
        rpm_patch.update_package(Mock(), "openssl", "0:3.0.1-1.oe2403", "3.0.1-2.oe2403")
    assert not error.value.verify_required
    assert all(" upgrade " not in call.args[1] for call in run.call_args_list)


def test_rejects_rpm_specs_that_can_select_multiple_or_different_packages() -> None:
    assert rpm_patch.is_rpm_advice(SimpleNamespace(
        conclusion="confirmed", confidence="high", knowledge_version="kb",
        package_name="openssl", installed_version="0:3.0-1.oe2403", fixed_version="3.0-2.oe2403",
    ))
    for name in ("*", "--allowerasing", "pkg; reboot", "/tmp/something.rpm"):
        assert not rpm_patch.valid_package_name(name)


def test_connection_loss_after_dispatch_requires_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rpm_patch, "_run", Mock(side_effect=[
        (0, "openssl\t0:3.0.1-1.oe2403\tx86_64\n"),
        (0, "dnf\n"), (0, "openssl\t0:3.0.1-2.oe2403\tx86_64\n"),
        (0, "-1\n"), TimeoutError(),
    ]))
    with pytest.raises(rpm_patch.PatchUpdateError) as error:
        rpm_patch.update_package(Mock(), "openssl", "0:3.0.1-1.oe2403", "3.0.1-2.oe2403")
    assert error.value.verify_required


@pytest.mark.parametrize(("outcome", "expected_code", "case_status"), [
    ("applied", 200, "pending_verification"),
    ("blocked", 409, "open"),
    ("uncertain", 409, "pending_verification"),
])
def test_execute_checks_role_confirmation_and_audits_result(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
    outcome: str, expected_code: int, case_status: str,
) -> None:
    role = "operator"

    def principal() -> AuthPrincipal:
        return AuthPrincipal("actual-admin", role, None, "session")

    _seed_patch_target(database_session)
    apply = Mock(return_value=(outcome, "执行测试结果"))
    monkeypatch.setattr(patch_execution, "_apply", apply)
    endpoint = "/api/v1/patches/patch-finding/execute"
    payload = {"reason": "批准本次更新", "confirmation": "确认更新软件包", "operator": "forged"}
    assert client.post(endpoint, json=payload).status_code == 403  # emergency token is read-only here
    client.app.dependency_overrides[require_admin] = principal
    assert client.post(endpoint, json=payload).status_code == 403
    role = "admin"
    assert client.post(endpoint, json={**payload, "confirmation": "错误确认文本"}).status_code == 400
    assert not apply.called
    assert client.get("/api/v1/patches").json()[0]["execution_enabled"] is True
    assert client.post(endpoint, json=payload).status_code == expected_code
    database_session.expire_all()
    case = database_session.get(VulnerabilityCase, "patch-case")
    assert case is not None and case.status == case_status
    assert case.audit_history[0]["status"] == f"patch_{outcome}"
    assert case.audit_history[0]["operator"] == "actual-admin"
    if case_status != "open":
        assert client.post(endpoint, json=payload).status_code == 409
        assert apply.call_count == 1


def _seed_patch_target(session: Session) -> None:
    session.add(Credential(
        id="patch-credential", name="test", username="root",
        credential_type=CredentialType.PASSWORD, encrypted_secret=b"test", nonce=b"test", key_version="test-v1",
    ))
    session.add(Asset(
        id="patch-asset", name="patch-target", host="192.0.2.4", ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.TEST, credential_id="patch-credential",
    ))
    session.add(SshHostKey(
        id="patch-key", asset_id="patch-asset", host="192.0.2.4", port=22,
        key_type="ssh-ed25519", fingerprint="SHA256:test", public_key="test", confirmed_by="test",
    ))
    session.add(VulnerabilityCase(
        id="patch-case", asset_id="patch-asset", asset_name="patch-target",
        cve_id="CVE-2026-0001", status="open", audit_history=[],
    ))
    session.add(VulnerabilityFinding(
        id="patch-finding", case_id="patch-case", scan_task_id="patch-scan", scan_task_name="test",
        asset_id="patch-asset", asset_name="patch-target", knowledge_version="kb",
        cve_id="CVE-2026-0001", advisory_id="ADV-1", title="Test", severity="high",
        package_name="openssl", installed_version="0:3.0.1-1.oe2403", fixed_version="3.0.1-2.oe2403",
        source_name="Vendor", conclusion="confirmed", confidence="high", evidence_chain=[],
    ))
    session.commit()
