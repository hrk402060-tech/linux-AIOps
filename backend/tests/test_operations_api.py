def test_operations_overview_and_approval_lifecycle(client):
    overview = client.get("/api/v1/operations/overview")
    assert overview.status_code == 200
    assert overview.json()["read_only_default"] is True
    assert overview.json()["direct_execution"] is False

    created = client.post(
        "/api/v1/operations/approvals",
        json={
            "operation_type": "read_only_diagnostic",
            "target_id": "asset-1",
            "title": "检查生产服务器",
            "reason": "核对告警原因",
        },
    )
    assert created.status_code == 201
    approval_id = created.json()["id"]

    approved = client.post(
        f"/api/v1/operations/approvals/{approval_id}/decision",
        json={"decision": "approve", "reason": "确认只读范围"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_change_record_requires_valid_transition(client):
    created = client.post(
        "/api/v1/operations/changes",
        json={
            "change_type": "patch",
            "target_type": "asset",
            "target_id": "asset-1",
            "title": "补丁变更记录",
            "rollback_plan": "恢复原版本并重新扫描",
        },
    )
    assert created.status_code == 201
    change_id = created.json()["id"]

    invalid = client.post(
        f"/api/v1/operations/changes/{change_id}/decision",
        json={"status": "rolled_back", "note": "不允许跳过应用"},
    )
    assert invalid.status_code == 409

    approved = client.post(
        f"/api/v1/operations/changes/{change_id}/decision",
        json={"status": "approved", "note": "管理员批准"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
