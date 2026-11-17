from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.services import kubernetes_overview_collect as collector
from src.services.kubernetes_cluster_transport import KubernetesConnectionError
from src.services.kubernetes_overview_metrics import quantity, summarize_metrics
from src.services.kubernetes_overview_transport import collection_path


def resource(name: str, uid: str | None = None) -> dict[str, object]:
    return {"metadata": {"name": name, "uid": uid or name, "creationTimestamp": "2026-01-01T00:00:00Z"}}


def test_fixed_collection_paths_encode_opaque_tokens_and_reject_unknown_resources() -> None:
    assert collection_path("pods", "a+/=&?") == "/api/v1/pods?limit=100&continue=a%2B%2F%3D%26%3F"
    assert collection_path("events") == "/api/v1/events?limit=100&fieldSelector=type%3DWarning"
    with pytest.raises(KubernetesConnectionError):
        collection_path("secrets")
    with pytest.raises(KubernetesConnectionError):
        collection_path("pods", "x\nInjected")


def test_pages_deduplicate_uid_and_require_one_resource_version(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = iter([
        {"metadata": {"resourceVersion": "7", "continue": "a+/="}, "items": [resource("a")]},
        {"metadata": {"resourceVersion": "7"}, "items": [resource("a"), resource("b")]},
    ])
    monkeypatch.setattr(collector, "request_collection_json", lambda *_: next(pages))
    result = collector.read_kind(None, "pods", None)
    assert result.state == "complete" and len(result.items) == 2
    pages = iter([
        {"metadata": {"resourceVersion": "7", "continue": "next"}, "items": [resource("a")]},
        {"metadata": {"resourceVersion": "8"}, "items": [resource("b")]},
    ])
    result = collector.read_kind(None, "pods", None)
    assert result.state == "partial" and result.error_code == "inconsistent_snapshot"
    assert len(result.items) == 1


def test_expired_page_restarts_whole_kind_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []
    def request(_parsed: object, kind: str, _budget: object, token: str | None = None) -> dict[str, object]:
        calls.append(token)
        if len(calls) == 1:
            return {"metadata": {"resourceVersion": "1", "continue": "old"}, "items": [resource("old")]}
        if len(calls) == 2:
            raise KubernetesConnectionError("resource_expired", "upstream-private-message")
        return {"metadata": {"resourceVersion": "2"}, "items": [resource("new")]}
    monkeypatch.setattr(collector, "request_collection_json", request)
    result = collector.read_kind(None, "pods", None)
    assert calls == [None, "old", None]
    assert result.state == "complete" and result.items == [resource("new")]


def test_forbidden_missing_and_partial_are_never_exact_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def request(*_arguments: object) -> dict[str, object]:
        raise KubernetesConnectionError("upstream_forbidden", "token=private")
    monkeypatch.setattr(collector, "request_collection_json", request)
    result = collector.read_kind(None, "pods", None)
    assert result.state == "failed" and result.total is None
    assert result.error_code == "upstream_forbidden"


def test_second_expiry_discards_every_partial_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    def request(*_arguments: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls % 2:
            return {"metadata": {"resourceVersion": str(calls), "continue": "next"},
                    "items": [resource(f"old-{calls}")]}
        raise KubernetesConnectionError("resource_expired", "must not be retained")
    monkeypatch.setattr(collector, "request_collection_json", request)
    result = collector.read_kind(None, "pods", None)
    assert calls == 4 and result.items == [] and result.total is None
    assert result.state == "failed" and result.error_code == "resource_expired"


def test_pagination_loop_and_limits_remain_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector, "request_collection_json", lambda *_: {
        "metadata": {"resourceVersion": "7", "continue": "same"}, "items": [resource("a")],
    })
    result = collector.read_kind(None, "pods", None)
    assert result.state == "partial" and result.total is None and result.error_code == "pagination_loop"
    monkeypatch.setattr(collector, "MAX_PAGES", 1)
    result = collector.read_kind(None, "pods", None)
    assert result.state == "partial" and result.error_code == "page_limit"


def test_duplicate_uid_with_conflicting_content_is_not_silently_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = iter([
        {"metadata": {"resourceVersion": "7", "continue": "next"}, "items": [resource("a")]},
        {"metadata": {"resourceVersion": "7"}, "items": [resource("renamed", "a")]},
    ])
    monkeypatch.setattr(collector, "request_collection_json", lambda *_: next(pages))
    result = collector.read_kind(None, "pods", None)
    assert result.state == "partial" and result.total is None
    assert result.error_code == "inconsistent_snapshot"


@pytest.mark.parametrize("document", [
    {"kind": "SecretList", "items": []},
    {"apiVersion": "apps/v1", "items": []},
    {"items": [{**resource("a"), "kind": "Secret"}]},
    {"items": [{**resource("a"), "apiVersion": "apps/v1"}]},
])
def test_wrong_list_or_object_type_is_rejected(
    monkeypatch: pytest.MonkeyPatch, document: dict[str, object],
) -> None:
    monkeypatch.setattr(collector, "request_collection_json", lambda *_: {
        **document, "metadata": {"resourceVersion": "7"},
    })
    result = collector.read_kind(None, "pods", None)
    assert result.state == "failed" and result.total is None and result.error_code == "invalid_response"


def test_failed_init_container_marks_pod_abnormal() -> None:
    from src.services.kubernetes_overview_projection import project_resources
    results = {kind: collector.KindResult("complete", [], None) for kind in collector.RESOURCE_KINDS}
    results["pods"] = collector.KindResult("complete", [{
        **resource("initializing"), "status": {"phase": "Pending", "initContainerStatuses": [{
            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
        }]},
    }], None)
    assert project_resources(results)["counts"]["pods_abnormal"] == 1


@pytest.mark.parametrize("text,expected", [
    ("123n", Decimal("0.000000123")), ("12u", Decimal("0.000012")),
    ("250m", Decimal(".25")), ("1.5", Decimal("1.5")), ("1e3", Decimal(1000)),
    ("2Ki", Decimal(2048)), ("1.5Gi", Decimal(1610612736)), ("2M", Decimal(2000000)),
])
def test_quantity_supports_standard_units(text: str, expected: Decimal) -> None:
    assert quantity(text) == expected


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "-1", "1e999999", "x" * 129, "3MB"])
def test_quantity_rejects_invalid_or_unbounded_values(value: object) -> None:
    with pytest.raises(ValueError):
        quantity(value)


def test_metrics_use_same_valid_node_coverage_and_real_allocatable() -> None:
    now = datetime.now(timezone.utc)
    nodes = [{**resource(name), "status": {"allocatable": {"cpu": "2", "memory": "2Gi"}}}
             for name in ("a", "b")]
    metric = {
        "metadata": {"name": "a"}, "timestamp": now.isoformat(), "window": "30s",
        "usage": {"cpu": "500m", "memory": "512Mi"},
    }
    result = summarize_metrics(nodes, [metric], now)
    assert result["state"] == "partial" and result["covered_nodes"] == 1
    assert result["total_nodes"] == 2 and result["cpu_percent"] == 25
    assert result["memory_percent"] == 25 and result["cpu_allocatable_cores"] == 2
    stale = {**metric, "timestamp": (now - timedelta(minutes=10)).isoformat()}
    assert summarize_metrics(nodes, [stale], now)["cpu_cores"] is None
    assert summarize_metrics(nodes, [metric, metric], now)["covered_nodes"] == 0


def test_projection_drops_messages_specs_annotations_and_counts_only_warning_objects() -> None:
    from src.services.kubernetes_overview_projection import project_resources
    secret = "private-token-and-env"
    event = {
        **resource("event"), "type": "Warning", "reason": secret, "message": secret,
        "count": 99, "involvedObject": {"kind": "Pod", "name": "web", "namespace": "default"},
    }
    pod = {**resource("pod"), "spec": {"env": secret}, "status": {"phase": "Succeeded"}}
    results = {kind: collector.KindResult("complete", [], None) for kind in collector.RESOURCE_KINDS}
    results["events"] = collector.KindResult("complete", [event], None)
    results["pods"] = collector.KindResult("complete", [pod], None)
    result = project_resources(results)
    assert result["counts"]["warning_events"] == 1
    assert result["counts"]["pods_abnormal"] == 0
    assert result["warnings"][0]["reason"] == "OtherWarning"
    assert secret not in str(result) and "message" not in str(result)
