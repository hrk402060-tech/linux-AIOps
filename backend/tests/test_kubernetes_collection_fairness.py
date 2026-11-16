import json
import ssl
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.services import kubernetes_diagnostics as collector, kubernetes_transport as transport
from test_kubernetes_diagnostics import document, run_collection

NAMESPACES = ['production', 'logging', 'monitoring']


def collection(remote: Mock) -> tuple[SimpleNamespace, Mock, Mock]:
    grant = SimpleNamespace(
        id='grant', revision=7, kind='kubernetes', encrypted_secret=b'cipher',
        nonce=b'nonce', key_version='v1', action_ids=list(collector.ACTIONS),
        kubernetes_config={'endpoint': 'https://192.0.2.20:6443', 'namespaces': NAMESPACES},
    )
    return grant, Mock(), remote


def item(resource: str, namespace: str, index: int = 0) -> dict[str, object]:
    return {
        'metadata': {'name': f'demo-{index}', 'namespace': namespace},
        **({'type': 'Warning', 'reason': 'FailedMount'} if resource == 'events' else {}),
    }


def test_event_flood_does_not_starve_other_namespaces_or_workloads() -> None:
    returned = []

    def request(namespace: str, resource: str, cursor: str = '', limit: int = 50) -> dict:
        count = limit if resource == 'events' else 1
        returned.append(count)
        return document(resource, [item(resource, namespace, n) for n in range(count)],
                        'more-events' if resource == 'events' else '')

    remote = Mock(request=Mock(side_effect=request))
    result = run_collection(collection(remote))
    assert result['status'] == 'partial'
    assert {(call.args[0], call.args[1]) for call in remote.request.call_args_list} == {
        (namespace, resource) for namespace in NAMESPACES
        for resource in transport.RESOURCE_KINDS
    }
    assert all(f'{action}:{namespace}' in result['evidence']
               for namespace in NAMESPACES for action in collector.ACTIONS)
    assert sum(returned) <= collector.MAX_ITEMS
    assert sum(map(len, result['evidence'].values())) <= collector.MAX_EVIDENCE_CHARS
    assert all(1 <= call.kwargs['limit'] <= 50 for call in remote.request.call_args_list)


@pytest.mark.parametrize('failure', ['item_limit', 'evidence_limit', 'http_forbidden'])
def test_deployment_failure_does_not_skip_other_workload_types(failure: str) -> None:
    def request(namespace: str, resource: str, cursor: str = '', limit: int = 50) -> dict:
        if resource == 'deployments':
            if failure == 'http_forbidden':
                raise transport.KubernetesTransportError(failure)
            rows = [item(resource, namespace, n) for n in range(limit)]
            if failure == 'evidence_limit':
                for row in rows:
                    row['metadata']['name'] = 'a' * 240
            return document(resource, rows, 'more')
        return document(resource, [item(resource, namespace)])

    remote = Mock(request=Mock(side_effect=request))
    with patch.object(collector, 'MAX_ITEMS', 36 if failure == 'item_limit' else 150):
        result = run_collection(collection(remote))
    assert result['status'] == 'partial'
    for namespace in NAMESPACES:
        rows = json.loads(result['evidence'][f'kubernetes.workloads:{namespace}'])['items']
        assert {'StatefulSet', 'DaemonSet'} <= {row['kind'] for row in rows}
    assert failure in result['evidence']['action_errors']
    assert sum(map(len, result['evidence'].values())) <= collector.MAX_EVIDENCE_CHARS


@pytest.mark.parametrize('empty', [False, True])
def test_small_and_empty_collections_complete_without_false_truncation(empty: bool) -> None:
    def request(namespace: str, resource: str, cursor: str = '', limit: int = 50) -> dict:
        return document(resource, [] if empty else [item(resource, namespace)])

    result = run_collection(collection(Mock(request=Mock(side_effect=request))))
    assert result['status'] == 'completed'
    assert json.loads(result['evidence']['action_errors']) == {}
    assert all(f'{action}:{namespace}' in result['evidence']
               for namespace in NAMESPACES for action in collector.ACTIONS)


def test_all_failed_requests_do_not_create_empty_success_evidence() -> None:
    remote = Mock(request=Mock(side_effect=transport.KubernetesTransportError('http_forbidden')))
    result = run_collection(collection(remote))
    assert result['status'] == 'failed'
    assert not any(key.startswith('kubernetes.') for key in result['evidence'])
    assert remote.request.call_count == 15


def test_response_exceeding_requested_limit_is_rejected() -> None:
    def request(namespace: str, resource: str, cursor: str = '', limit: int = 50) -> dict:
        return document(resource, [item(resource, namespace, n) for n in range(limit + 1)])

    result = run_collection(collection(Mock(request=Mock(side_effect=request))))
    assert result['status'] == 'failed'
    assert 'item_limit' in result['evidence']['action_errors']


@pytest.mark.parametrize('continuation', ['', 'more'])
def test_exact_resource_quota_requires_partial_only_when_more_exist(continuation: str) -> None:
    remote = Mock(request=Mock(return_value=document(
        'pods', [item('pods', 'production', n) for n in range(2)], continuation)))
    values = collection(remote)
    values[0].action_ids = ['kubernetes.pods']
    values[0].kubernetes_config['namespaces'] = ['production']
    with patch.object(collector, 'MAX_ITEMS', 2):
        result = run_collection(values)
    assert result['status'] == ('partial' if continuation else 'completed')
    assert len(json.loads(result['evidence']['kubernetes.pods:production'])['items']) == 2
    assert remote.request.call_count == 1


def test_workload_evidence_survives_next_page_limit_and_empty_following_resource() -> None:
    def request(namespace: str, resource: str, cursor: str = '', limit: int = 50) -> dict:
        if resource == 'deployments' and not cursor:
            return document(resource, [item(resource, namespace, n) for n in range(2)], 'next')
        if resource == 'deployments':
            raise transport.KubernetesTransportError('evidence_limit')
        return document(resource, [] if resource == 'statefulsets' else [item(resource, namespace)])

    remote = Mock(request=Mock(side_effect=request))
    values = collection(remote)
    values[0].action_ids = ['kubernetes.workloads']
    values[0].kubernetes_config['namespaces'] = ['production']
    result = run_collection(values)
    assert result['status'] == 'partial'
    rows = json.loads(result['evidence']['kubernetes.workloads:production'])['items']
    assert [row['kind'] for row in rows] == ['Deployment', 'Deployment', 'DaemonSet']


@pytest.mark.parametrize('action_count', [1, 2, 3])
@pytest.mark.parametrize('namespace_count', [1, 2, 3])
def test_scope_sizes_and_long_names_preserve_global_evidence_bound(
    action_count: int, namespace_count: int,
) -> None:
    def request(namespace: str, resource: str, cursor: str = '', limit: int = 50) -> dict:
        rows = [item(resource, namespace, n) for n in range(limit)]
        for row in rows:
            row['metadata']['name'] = 'long-name-' + 'a' * 240
        return document(resource, rows, 'more')

    values = collection(Mock(request=Mock(side_effect=request)))
    values[0].action_ids = list(collector.ACTIONS)[:action_count]
    values[0].kubernetes_config['namespaces'] = [
        letter * 63 for letter in 'abc'[:namespace_count]]
    result = run_collection(values)
    assert result['status'] == 'partial'
    assert sum(map(len, result['evidence'].values())) <= collector.MAX_EVIDENCE_CHARS


@pytest.mark.parametrize('limit', [0, 51, True, '5'])
def test_transport_rejects_invalid_page_size_before_connecting(limit: object) -> None:
    context = Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True)
    with patch.object(transport.ssl, 'create_default_context', return_value=context):
        client = transport.KubernetesTransport(
            'https://192.0.2.20:6443', 'ca', 'token', transport.time.monotonic() + 40, Mock())
    with patch.object(transport.http.client, 'HTTPSConnection') as connect:
        with pytest.raises(transport.KubernetesTransportError, match='invalid_action'):
            client.request('production', 'pods', limit=limit)
    connect.assert_not_called()
