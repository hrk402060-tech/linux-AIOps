import pytest

from src.services import alert_simulation


def test_simulation_requires_preview_sqlite_database() -> None:
    with pytest.raises(alert_simulation.AlertSimulationSafetyError):
        alert_simulation.assert_isolated_database("postgresql+psycopg://ops:secret@db/prod")
    with pytest.raises(alert_simulation.AlertSimulationSafetyError):
        alert_simulation.assert_isolated_database("sqlite:////var/lib/production.db")


def test_simulation_payload_is_marked_preview_only() -> None:
    payload = alert_simulation.demo_payload("asset-1")
    assert payload["asset_id"] == "asset-1"
    assert payload["labels"] == {"preview_only": "true", "scenario": "alert-closed-loop"}
    assert "真实服务器" in payload["description"]


def test_signed_request_is_deterministic_for_fixed_nonce_and_timestamp() -> None:
    payload = alert_simulation.demo_payload("asset-1")
    body, headers = alert_simulation.signed_request(
        "demo-secret", payload, timestamp=1_700_000_000, nonce="fixed-nonce"
    )
    expected = alert_simulation.signed_request(
        "demo-secret", payload, timestamp=1_700_000_000, nonce="fixed-nonce"
    )
    assert body == expected[0]
    assert headers == expected[1]
    assert headers["X-Alert-Signature"].startswith("sha256=")
    assert len(alert_simulation.fingerprint_payload(payload)) == 64
