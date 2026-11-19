import pytest
from fastapi.testclient import TestClient

from src.services.kubernetes_helm import HelmPreviewError, summarize
from test_kubernetes_change_preflight import seed_snapshot
from test_kubernetes_clusters_api import BASE, create

pytest_plugins = ("test_kubernetes_clusters_api",)
DIGEST = "sha256:" + "a" * 64


def test_manifest_projection_does_not_return_values() -> None:
    result = summarize(b"""apiVersion: v1
kind: ConfigMap
metadata:
  name: demo
data:
  key: do-not-disclose
""", "payments")
    assert [row.model_dump() for row in result] == [
        {"api_version": "v1", "kind": "ConfigMap", "namespace": "payments", "name": "demo"},
    ]


@pytest.mark.parametrize("manifest", [
    b"apiVersion: v1\nkind: Secret\nmetadata: {name: demo}",
    b"apiVersion: v1\nkind: ConfigMap\nmetadata: {name: demo, namespace: elsewhere}",
    b"apiVersion: v1\nkind: ConfigMap\nmetadata: {name: demo, annotations: {helm.sh/hook: pre-install}}",
    b"apiVersion: other/v1\nkind: Deployment\nmetadata: {name: demo}",
    b"apiVersion: v1\nkind: Service\nmetadata: {name: demo}\nspec: {type: LoadBalancer}",
    b"apiVersion: v1\nkind: ConfigMap\nkind: Secret\nmetadata: {name: demo}",
    b"apiVersion: v1\nkind: ConfigMap\nmetadata: &meta {name: demo}\ndata: *meta",
])
def test_manifest_fails_closed(manifest: bytes) -> None:
    with pytest.raises(HelmPreviewError):
        summarize(manifest, "payments")


def test_helm_preview_requires_admin_current_revision_and_opt_in(kclient: TestClient) -> None:
    cluster = create(kclient)
    url = f"{BASE}/clusters/{cluster['id']}"
    seed_snapshot(kclient, url)
    body = {"expected_revision": cluster["revision"], "namespace": "payments", "release_name": "demo",
            "chart_id": "demo-1", "chart_digest": DIGEST}
    assert kclient.get(f"{BASE}/helm-charts").json() == {"enabled": False, "charts": []}
    assert kclient.post(f"{url}/helm-preview", json={**body, "expected_revision": 999}).status_code == 409
    assert kclient.post(f"{url}/helm-preview", json=body).status_code == 503
    response = kclient.post(f"{url}/helm-preview", json={**body, "values": "do-not-echo"})
    assert response.status_code == 422 and "do-not-echo" not in response.text
    kclient.cookies.clear()
    assert kclient.get(f"{BASE}/helm-charts").status_code == 401
    token = kclient.app.state.api_admin_token
    assert kclient.post(f"{url}/helm-preview", json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 403
