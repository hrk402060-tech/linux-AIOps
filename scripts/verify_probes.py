"""Verify probes against an approved disposable HTTP fixture, not real assets."""
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from uuid import uuid4


BASE = os.environ.get("PROBE_VERIFY_API", "http://127.0.0.1:8000/api/v1")
TOKEN = os.environ["API_ADMIN_TOKEN"]
FIXTURE = os.environ["PROBE_FIXTURE_URL"]


def request(path: str, payload: dict[str, object] | None = None,
            method: str | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(BASE + path, data=data, method=method,
                  headers={"Authorization": f"Bearer {TOKEN}",
                           "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def main() -> None:
    suffix = uuid4().hex[:10]
    status, business = request("/cmdb/businesses", {"code": f"PROBE-{suffix}", "name": "Probe fixture"})
    assert status == 201, ("business", status)
    status, app = request("/cmdb/applications", {"business_id": business["id"], "code": f"APP-{suffix}", "name": "Fixture app"})
    assert status == 201, ("application", status)
    status, service = request("/cmdb/services", {"application_id": app["id"], "code": f"SVC-{suffix}", "name": "Fixture HTTP", "service_type": "api"})
    assert status == 201, ("service", status)
    status, _ = request("/probes/policy")
    assert status == 200
    host = urlsplit(FIXTURE).hostname
    status, asset = request('/assets', {'name': 'Probe fixture', 'host': host, 'ssh_port': 22, 'os_family': 'ubuntu', 'environment': 'test', 'group_name': 'fixture', 'labels': []})
    assert status == 201, ('asset', status)
    status, _ = request(f'/cmdb/services/{service["id"]}/assets', {'asset_id': asset['id'], 'relation_role': 'primary'})
    assert status == 201, ('relation', status)
    status, target = request("/probes", {"name": "Fixture HEAD", "service_id": service["id"], "asset_id": asset['id'], "api_id": None, "url": FIXTURE, "method": "HEAD", "expected_status": 200})
    assert status == 201, ("target", status)
    for _ in range(2):
        status, result = request(f'/probes/{target["id"]}/run', {})
        assert status in (200, 201), ("run", status)
        assert result["status"] == "healthy" and result["http_status"] == 200, "fixture check failed"
        assert result["service_id"] == service["id"] and result["business_id"] == business["id"]
        assert result["operator_username"] == "break-glass-admin" and result["trace_id"]
    status, history = request(f'/probes/{target["id"]}/runs')
    assert status == 200 and len(history["items"]) == 2
    assert history["items"][0]["id"] != history["items"][1]["id"]
    status, _ = request(f'/probes/{target["id"]}', method="DELETE")
    assert status in (404, 405), "ordinary deletion must be unavailable"
    denied = {"name": "Denied metadata", "service_id": service["id"], "asset_id": asset['id'], "url": "http://169.254.169.254/", "method": "HEAD", "expected_status": 200}
    status, _ = request("/probes", denied)
    assert status in (400, 422), "metadata must be rejected"
    print("Probe Docker integration: CMDB, real HEAD, append-only history, operator/IDs, deletion and metadata boundaries passed")


if __name__ == "__main__":
    main()
