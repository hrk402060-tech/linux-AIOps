from src.services import baseline_collector


def test_read_only_command_and_evidence_parser() -> None:
    command = baseline_collector._command()
    assert "systemctl is-active" in command
    for forbidden in ("systemctl enable", "systemctl start", "sed -i", "chmod", "rm -"):
        assert forbidden not in command

    values = baseline_collector._parse(
        "permitrootlogin=no\npasswordauthentication=no\n"
        "firewall=active\ntimesync=active\naudit=inactive\n"
    )

    assert values["permitrootlogin"] == "no"
    assert values["audit"] == "inactive"
