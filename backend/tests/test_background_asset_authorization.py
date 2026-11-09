from importlib import import_module
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete

from src.models.asset_access import UserAssetAccess
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.auth import User
from test_user_asset_access import ADMIN, assign, create_asset, create_user, sign_in

RESOURCES = [
    ("scan-tasks", "scan_tasks", "ScanTask", "scan_task", "_collect_asset"),
    ("baselines", "baselines", "BaselineTask", "baseline", "baseline_collector.collect"),
    ("exposures", "exposures", "ExposureTask", "exposure", "exposure_collector.collect"),
]


def change_authorization(client: TestClient, user_id: str, change: str) -> None:
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        if change == "revoke":
            session.execute(delete(UserAssetAccess).where(UserAssetAccess.user_id == user_id))
        elif change == "disable":
            user.enabled = False
        elif change == "role":
            user.role = "auditor"
        elif change == "password":
            user.must_change_password = True
        session.commit()


def prepare_task(client: TestClient, resource: str) -> tuple[dict, list[str], str]:
    user = create_user(client)
    assets = [create_asset(client, f"background-{index}") for index in range(2)]
    assert assign(client, user, [{"asset_id": item} for item in assets]).status_code == 200
    sign_in(client, user)
    payload = {"name": "authorization-aware-task", "asset_ids": assets}
    if resource == "scan-tasks":
        payload["scan_type"] = "comprehensive"
    created = client.post(f"/api/v1/{resource}", json=payload)
    assert created.status_code == 201
    return user, assets, created.json()["id"]


@pytest.mark.parametrize("resource,module,model,model_module,collector", RESOURCES)
@pytest.mark.parametrize("change", ["revoke", "disable", "role", "password"])
def test_queued_background_job_rechecks_requester_before_collecting(
    client: TestClient, resource: str, module: str, model: str, model_module: str,
    collector: str, change: str,
) -> None:
    user, _, task_id = prepare_task(client, resource)
    service = import_module(f"src.services.{module}")
    captured: list[tuple[tuple, dict]] = []
    with patch.object(service, "execute_task", side_effect=lambda *a, **k: captured.append((a, k))):
        assert client.post(f"/api/v1/{resource}/{task_id}/start").status_code == 200
    change_authorization(client, user["id"], change)
    with patch(f"src.services.{module}.{collector}") as collect:
        service.execute_task(*captured[0][0], **captured[0][1])
        collect.assert_not_called()
    task_model = getattr(import_module(f"src.models.{model_module}"), model)
    with client.app.state.session_factory() as session:
        task = session.get(task_model, task_id)
        assert task.status.value == "failed"
        assert "权限" in task.error_message
        assert task.finished_at is not None


@pytest.mark.parametrize("resource,module,model,model_module,collector", RESOURCES)
def test_revocation_during_collection_blocks_next_asset_and_result_publish(
    client: TestClient, resource: str, module: str, model: str, model_module: str,
    collector: str,
) -> None:
    user, _, task_id = prepare_task(client, resource)
    service = import_module(f"src.services.{module}")
    captured: list[tuple[tuple, dict]] = []
    with patch.object(service, "execute_task", side_effect=lambda *a, **k: captured.append((a, k))):
        assert client.post(f"/api/v1/{resource}/{task_id}/start").status_code == 200

    def revoke_during_collect(*args: object, **kwargs: object) -> list | dict:
        del args, kwargs
        change_authorization(client, user["id"], "revoke")
        return {"asset_name": "first", "status": "scanned", "findings": []} if module == "scan_tasks" else []

    with patch(f"src.services.{module}.{collector}", side_effect=revoke_during_collect) as collect:
        service.execute_task(*captured[0][0], **captured[0][1])
        assert collect.call_count == 1
    task_model = getattr(import_module(f"src.models.{model_module}"), model)
    with client.app.state.session_factory() as session:
        task = session.get(task_model, task_id)
        assert task.status.value == "failed"
        assert "权限" in task.error_message
        assert not (task.findings if module == "scan_tasks" else task.results)


@pytest.mark.parametrize("resource,module,model,model_module,collector", RESOURCES)
def test_asset_lifecycle_change_stops_background_collection(
    client: TestClient, resource: str, module: str, model: str,
    model_module: str, collector: str,
) -> None:
    user, assets, task_id = prepare_task(client, resource)
    service = import_module(f"src.services.{module}")
    captured: list[tuple[tuple, dict]] = []
    with patch.object(service, "execute_task", side_effect=lambda *a, **k: captured.append((a, k))):
        assert client.post(f"/api/v1/{resource}/{task_id}/start").status_code == 200
    with client.app.state.session_factory() as session:
        session.get(Asset, assets[0]).lifecycle_status = AssetLifecycleStatus.DISABLED
        session.commit()
    with patch(f"src.services.{module}.{collector}") as collect:
        service.execute_task(*captured[0][0], **captured[0][1])
        collect.assert_not_called()
    task_model = getattr(import_module(f"src.models.{model_module}"), model)
    with client.app.state.session_factory() as session:
        task = session.get(task_model, task_id)
        assert task.status.value == "failed"
        assert "停用" in task.error_message


def test_trusted_scheduler_keeps_existing_system_execution(client: TestClient) -> None:
    from src.services import scan_tasks

    asset_id = create_asset(client, "scheduler-target")
    task_id = client.post("/api/v1/scan-tasks", headers=ADMIN, json={
        "name": "scheduled-system-task", "asset_ids": [asset_id], "scan_type": "comprehensive",
    }).json()["id"]
    from src.models.scan_task import ScanTask
    with client.app.state.session_factory() as session:
        scan_tasks.start_task(session, session.get(ScanTask, task_id))
    with patch("src.services.scan_tasks._collect_asset", return_value={
        "asset_name": "scheduler-target", "status": "failed", "message": "test-only failure",
    }) as collect:
        scan_tasks.execute_task(client.app.state.session_factory, task_id, client.app.state.credential_vault)
        collect.assert_called_once()
