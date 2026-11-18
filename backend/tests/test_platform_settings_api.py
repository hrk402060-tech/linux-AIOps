def test_platform_settings_read_save_and_runtime_apply(client) -> None:
    response = client.get("/api/v1/system/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["connection"]["collection_interval_seconds"] == 300
    assert body["security"]["mfa_enforcement"] == "off"
    assert body["ai"]["api_key_masked"] is None

    payload = {
        **body["settings"]["connection"],
        "collection_interval_seconds": 600,
        "retry_count": 4,
    }
    saved = client.put("/api/v1/system/settings/connection", json=payload)
    assert saved.status_code == 200
    assert saved.json()["settings"]["connection"]["collection_interval_seconds"] == 600
    assert client.get("/api/v1/system/status").json()["monitoring_interval_seconds"] == 600
    assert client.app.state.platform_runtime["connection"]["retry_count"] == 4


def test_platform_settings_reject_invalid_values_without_overwriting(client) -> None:
    original = client.get("/api/v1/system/settings").json()["settings"]["tasks"]
    response = client.put("/api/v1/system/settings/tasks", json={
        **original, "terminal_idle_timeout_seconds": 10,
    })
    assert response.status_code == 422
    assert client.get("/api/v1/system/settings").json()["settings"]["tasks"] == original


def test_platform_settings_requires_admin(client) -> None:
    client.headers.pop("Authorization", None)
    assert client.get("/api/v1/system/settings").status_code == 401


def test_settings_reject_stale_revision_and_keep_saved_values(client) -> None:
    body = client.get("/api/v1/system/settings").json()
    revision = body["revisions"]["connection"]
    payload = {**body["settings"]["connection"], "collection_interval_seconds": 601}
    saved = client.put("/api/v1/system/settings/connection", json=payload,
                       headers={"If-Match": revision})
    assert saved.status_code == 200
    payload["collection_interval_seconds"] = 602
    conflict = client.put("/api/v1/system/settings/connection", json=payload,
                          headers={"If-Match": revision})
    assert conflict.status_code == 409
    assert client.get("/api/v1/system/settings").json()["settings"]["connection"]["collection_interval_seconds"] == 601


def test_corrupt_settings_are_not_silently_replaced(client) -> None:
    from src.models.platform_setting import PlatformSetting

    with client.app.state.session_factory() as session:
        session.add(PlatformSetting(key="settings.tasks", value='{"bad":true}'))
        session.commit()
    response = client.get("/api/v1/system/settings")
    assert response.status_code == 503
    assert "bad" not in response.text


def test_settings_cannot_disable_human_confirmation_or_all_login(client) -> None:
    settings = client.get("/api/v1/system/settings").json()["settings"]
    assert client.put("/api/v1/system/settings/tasks", json={
        **settings["tasks"], "require_confirmation": False,
    }).status_code == 422
    assert client.put("/api/v1/system/settings/authentication", json={
        **settings["authentication"], "local_login_enabled": False,
    }).status_code in {409, 422}


def test_settings_validation_never_echoes_secret(client) -> None:
    response = client.put("/api/v1/system/settings/ai", json={
        "enabled": True, "base_url": "invalid", "model": "model",
        "api_key": "must-not-appear-in-errors", "timeout_seconds": -1,
    })
    assert response.status_code == 422
    assert "must-not-appear-in-errors" not in response.text


def test_defaults_preserve_notification_delivery_without_quiet_hours(client) -> None:
    from src.services.platform_settings import in_quiet_hours

    with client.app.state.session_factory() as session:
        assert in_quiet_hours(session, client.app.state.runtime_settings) is False
