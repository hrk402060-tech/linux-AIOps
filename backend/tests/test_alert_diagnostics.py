from typing import cast
from unittest.mock import Mock
from unittest.mock import patch

import paramiko
import pytest

from src.services import alert_diagnostics


@pytest.mark.parametrize("output", [
    "", "  UNIT LOAD ACTIVE SUB DESCRIPTION\n0 loaded units listed.",
    "0 loaded units listed. Pass --all to see loaded but inactive units, too.",
    "backup.service loaded active running backup failed previously",
    "incomplete.service failed", "Some error: systemd failed unexpectedly",
])
def test_empty_or_non_failed_units_do_not_report_service_failure(output: str) -> None:
    summary = alert_diagnostics._summarize({"failed_units": output})
    assert summary == alert_diagnostics._summarize({})


@pytest.mark.parametrize("output", [
    "sshd.service loaded failed failed OpenSSH server",
    "\u25cf app.service loaded failed failed Application server",
    "missing.service not-found failed failed Missing service",
])
def test_failed_unit_rows_are_reported(output: str) -> None:
    summary = alert_diagnostics._summarize({"failed_units": output})
    assert summary != alert_diagnostics._summarize({})
    assert "systemd" in summary


def test_zero_failed_units_do_not_hide_kernel_evidence() -> None:
    evidence = {"failed_units": "0 loaded units listed.", "kernel_errors": "I/O error"}
    assert alert_diagnostics._summarize(evidence) == alert_diagnostics._summarize({
        "kernel_errors": "I/O error",
    })


def test_failed_unit_collection_omits_legend_and_stays_read_only() -> None:
    command = alert_diagnostics.COMMANDS["failed_units"]
    assert "--no-legend" in command
    assert "LC_ALL=C" in command
    assert "--failed" in command
    assert all(word not in command for word in ("sudo", "restart", "stop", "start"))


def test_evidence_sanitization_is_bounded_and_redacted() -> None:
    evidence = alert_diagnostics.sanitize_evidence({
        key: "token=sensitive " + "a" * 20_000 for key in alert_diagnostics.COMMANDS
    })
    assert sum(len(value) for value in evidence.values()) <= alert_diagnostics.MAX_EVIDENCE_CHARS
    assert set(evidence) == set(alert_diagnostics.COMMANDS)
    assert "sensitive" not in str(evidence)


def test_default_diagnostic_never_decrypts_or_connects_ssh() -> None:
    from src.models.asset import Asset
    from src.services.crypto import CredentialVault

    asset = cast(Asset, Mock())
    vault = cast(CredentialVault, Mock())
    with patch("src.services.scan_engine._connect") as connect:
        with pytest.raises(alert_diagnostics.DiagnosticAuthorizationRequired):
            alert_diagnostics.collect(asset, vault, cast(paramiko.PKey, Mock()))
    vault.decrypt.assert_not_called()
    connect.assert_not_called()
