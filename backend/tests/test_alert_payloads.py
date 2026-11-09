import pytest

from src.services.alert_payloads import AlertPayloadError, normalize_payload


def test_prometheus_alertmanager_payload_is_normalized() -> None:
    payload = normalize_payload("prometheus", {
        "receiver": "ops",
        "status": "firing",
        "alerts": [{
            "fingerprint": "fp-1",
            "labels": {"alertname": "NodeHighCPU", "instance": "192.0.2.10:9100", "severity": "critical"},
            "annotations": {"summary": "CPU 持续过高", "description": "超过阈值"},
        }],
    })
    assert payload.external_id == "fp-1"
    assert payload.title == "CPU 持续过高"
    assert payload.severity == "critical"
    assert payload.instance == "192.0.2.10:9100"


@pytest.mark.parametrize(("severity", "expected"), [
    ("Disaster", "critical"), ("Average", "high"), ("Warning", "high"),
    ("Information", "low"), (5, "critical"), (2, "medium"),
])
def test_zabbix_payload_maps_severity_and_host(severity: object, expected: str) -> None:
    payload = normalize_payload("zabbix", {
        "eventid": "1001", "name": "主机不可达", "severity": severity,
        "host": {"host": "192.0.2.20"}, "operational_data": "网络异常",
    })
    assert payload.external_id == "1001"
    assert payload.asset_host == "192.0.2.20"
    assert payload.severity == expected


def test_batch_external_alert_is_rejected_instead_of_dropping_alerts() -> None:
    with pytest.raises(AlertPayloadError):
        normalize_payload("prometheus", {"alerts": [{}, {}]})


def test_generic_payload_remains_backward_compatible() -> None:
    payload = normalize_payload("generic_webhook", {
        "external_id": "generic-1", "title": "告警", "severity": "low",
        "asset_id": "asset-1",
    })
    assert payload.external_id == "generic-1"
