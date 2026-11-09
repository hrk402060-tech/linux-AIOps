import json
import ssl
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from src.services import alert_diagnostics, kubernetes_diagnostics as collector
from src.services import kubernetes_projection as projection, kubernetes_transport as transport

def document(resource: str = 'pods', items: list[dict[str, object]] | None = None,
             continuation: str = '') -> dict[str, object]:
    kind = transport.RESOURCE_KINDS[resource]
    return {'kind': kind + 'List', 'metadata': {'continue': continuation}, 'items': items or []}

def pod(name: str = 'demo') -> dict[str, object]:
    return {'kind': 'Pod', 'metadata': {'name': name, 'namespace': 'production'},
            'spec': {'env': [{'value': 'raw-secret'}]},
            'status': {'phase': 'Running', 'message': 'raw-secret',
                       'containerStatuses': [{'ready': True, 'restartCount': 2}]}}

def response(body: object, status: int = 200) -> Mock:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return Mock(status=status, read=Mock(side_effect=[raw, b'']))

@pytest.fixture
def context() -> Mock:
    return Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True)

@pytest.mark.parametrize('endpoint,port', [('https://192.0.2.20:6443', 6443), ('https://192.0.2.20', 443)])
def test_fixed_get_scope_encoded_cursor_tls_and_close(context: Mock, endpoint: str, port: int) -> None:
    connection = Mock()
    connection.getresponse.return_value = response(document())
    authorize = Mock()
    with patch.object(transport.ssl, 'create_default_context', return_value=context) as tls:
        with patch.object(transport.http.client, 'HTTPSConnection', return_value=connection) as connect:
            client = transport.KubernetesTransport(endpoint, 'ca', 'token',
                                                   transport.time.monotonic() + 40, authorize)
            client.request('production', 'pods', 'https://evil.invalid/?token=bad')
    tls.assert_called_once_with(cadata='ca')
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert connect.call_args.args == ('192.0.2.20', port)
    assert 0 < connect.call_args.kwargs['timeout'] <= 5
    args, kwargs = connection.request.call_args
    assert args[0] == 'GET'
    parsed = urlsplit(args[1])
    assert parsed.path == '/api/v1/namespaces/production/pods'
    assert parse_qs(parsed.query) == {'limit': ['50'], 'continue': ['https://evil.invalid/?token=bad']}
    assert kwargs['headers']['Authorization'] == 'Bearer token'
    assert authorize.call_count >= 6
    connection.close.assert_called_once()

@pytest.mark.parametrize('endpoint', [
    'http://192.0.2.20:6443', 'https://localhost:6443', 'https://127.0.0.1:6443',
    'https://169.254.169.254:6443', 'https://192.0.2.20:6443/x',
    'https://name:password@192.0.2.20:6443', 'https://[::ffff:192.0.2.20]:6443',
])
def test_endpoint_rejected_before_connection(endpoint: str, context: Mock) -> None:
    with patch.object(transport.http.client, 'HTTPSConnection') as connect:
        with pytest.raises(transport.KubernetesTransportError, match='invalid_configuration'):
            transport.KubernetesTransport(endpoint, 'ca', 'token', 100, Mock())
    connect.assert_not_called()

@pytest.mark.parametrize('resource,namespace', [
    ('secrets', 'production'), ('nodes', 'production'), ('pods/log', 'production'),
    ('pods', '../other'), ('pods', '*'), ('pods', ''),
])
def test_disallowed_paths_never_connect(resource: str, namespace: str, context: Mock) -> None:
    with patch.object(transport.ssl, 'create_default_context', return_value=context):
        client = transport.KubernetesTransport('https://192.0.2.20:6443', 'ca', 'token',
                                               transport.time.monotonic() + 40, Mock())
    with patch.object(transport.http.client, 'HTTPSConnection') as connect:
        with pytest.raises(transport.KubernetesTransportError):
            client.request(namespace, resource)
    connect.assert_not_called()

@pytest.mark.parametrize('status,code', [(302, 'http_redirect'), (401, 'http_unauthorized'),
                                         (403, 'http_forbidden'), (410, 'resource_expired'),
                                         (500, 'http_server_error')])
def test_http_error_does_not_read_or_echo_body(status: int, code: str, context: Mock) -> None:
    reply = response(b'Authorization Bearer sensitive-token', status)
    connection = Mock()
    connection.getresponse.return_value = reply
    with patch.object(transport.ssl, 'create_default_context', return_value=context):
        with patch.object(transport.http.client, 'HTTPSConnection', return_value=connection):
            client = transport.KubernetesTransport('https://192.0.2.20:6443', 'ca', 'token',
                                                   transport.time.monotonic() + 40, Mock())
            with pytest.raises(transport.KubernetesTransportError, match=code) as error:
                client.request('production', 'pods')
    assert 'sensitive' not in str(error.value)
    reply.read.assert_not_called()
    connection.close.assert_called_once()

@pytest.mark.parametrize('raw,code', [(b'not-json', 'invalid_response'),
                                      (b'x' * (256 * 1024 + 1), 'response_limit')])
def test_response_limits_and_invalid_json(raw: bytes, code: str, context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response(raw)
    with patch.object(transport.ssl, 'create_default_context', return_value=context):
        with patch.object(transport.http.client, 'HTTPSConnection', return_value=connection):
            client = transport.KubernetesTransport('https://192.0.2.20:6443', 'ca', 'token',
                                                   transport.time.monotonic() + 40, Mock())
            with pytest.raises(transport.KubernetesTransportError, match=code):
                client.request('production', 'pods')
    connection.close.assert_called_once()

def test_guard_cancellation_propagates_and_closes(context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response(document())
    guard = Mock(side_effect=[None, None, None, RuntimeError('lease-lost')])
    with patch.object(transport.ssl, 'create_default_context', return_value=context):
        with patch.object(transport.http.client, 'HTTPSConnection', return_value=connection):
            client = transport.KubernetesTransport('https://192.0.2.20:6443', 'ca', 'token',
                                                   transport.time.monotonic() + 40, guard)
            with pytest.raises(RuntimeError, match='lease-lost'):
                client.request('production', 'pods')
    connection.close.assert_called_once()

def test_projection_drops_raw_and_limits_reason() -> None:
    item = pod()
    item['metadata']['annotations'] = {'secret': 'raw-secret'}
    item['status']['conditions'] = [{'type': 'Ready', 'status': 'False',
                                    'reason': 'token=raw-secret', 'message': 'raw-secret'}]
    rows, cursor = projection.project_list(document(items=[item]), 'pods', 'production')
    assert cursor == ''
    assert rows[0]['phase'] == 'Running' and rows[0]['restarts'] == 2
    assert 'raw-secret' not in json.dumps(rows)
    assert 'spec' not in json.dumps(rows) and 'message' not in json.dumps(rows)

@pytest.mark.parametrize('body', [
    {'kind': 'SecretList', 'items': []}, {'kind': 'Pod', 'items': []},
    {'kind': 'PodList', 'items': {}}, {'kind': 'PodList', 'items': [{'kind': 'Secret'}]},
    document(items=[{**pod(), 'metadata': {'name': 'demo', 'namespace': 'other'}}]),
])
def test_projection_rejects_kind_and_cross_namespace(body: dict[str, object]) -> None:
    with pytest.raises(transport.KubernetesTransportError, match='invalid_response'):
        projection.project_list(body, 'pods', 'production')

@pytest.fixture
def collection() -> tuple[SimpleNamespace, Mock, Mock]:
    grant = SimpleNamespace(id='grant', revision=7, kind='kubernetes',
        action_ids=['kubernetes.pods'], encrypted_secret=b'cipher', nonce=b'nonce', key_version='v1',
        kubernetes_config={'endpoint': 'https://192.0.2.20:6443', 'namespaces': ['production'],
                           'service_account_namespace': 'ops', 'service_account_name': 'diagnostic',
                           'ca_fingerprint': 'test'})
    vault = Mock()
    remote = Mock()
    remote.request.return_value = document(items=[pod()])
    return grant, vault, remote

def run_collection(collection: tuple[SimpleNamespace, Mock, Mock], authorize: Mock | None = None
                   ) -> dict[str, object]:
    grant, vault, remote = collection
    with patch.object(collector.diagnostic_grants, 'require_grant', return_value=grant):
        with patch.object(collector.alert_diagnostics, '_authorization_guard',
                          return_value=authorize or Mock()):
            with patch.object(collector, '_credentials', return_value=('private-token', 'ca')):
                with patch.object(collector, 'KubernetesTransport', return_value=remote):
                    return collector.collect(SimpleNamespace(host='192.0.2.20'), vault, Mock(),
                                             expected_revision=7)

def test_collection_metadata_and_pagination(collection: tuple[SimpleNamespace, Mock, Mock]) -> None:
    collection[2].request.side_effect = [document(items=[pod()], continuation='cursor'),
                                        document(items=[pod('second')])]
    result = run_collection(collection)
    assert result['status'] == 'completed'
    evidence = result['evidence']
    assert evidence['collection_mode'] == 'restricted_read_only_kubernetes'
    assert evidence['read_only'] == 'true' and evidence['remediation'] == 'disabled'
    assert evidence['grant_revision'] == '7' and evidence['policy_version'] == collector.POLICY_VERSION
    assert len(json.loads(evidence['kubernetes.pods:production'])['items']) == 2
    assert collection[2].request.call_args_list[1].args == ('production', 'pods', 'cursor')
    assert 'raw-secret' not in json.dumps(evidence) and 'private-token' not in json.dumps(evidence)

@pytest.mark.parametrize('code', ['resource_expired', 'response_limit', 'total_byte_limit'])
def test_partial_preserves_previous_page(collection: tuple[SimpleNamespace, Mock, Mock], code: str) -> None:
    collection[2].request.side_effect = [document(items=[pod()], continuation='cursor'),
                                        transport.KubernetesTransportError(code)]
    result = run_collection(collection)
    assert result['status'] == 'partial'
    assert len(json.loads(result['evidence']['kubernetes.pods:production'])['items']) == 1
    assert code in result['evidence']['action_errors']

def test_page_and_item_limits_are_partial(collection: tuple[SimpleNamespace, Mock, Mock]) -> None:
    collection[2].request.return_value = document(items=[pod()] * 50, continuation='cursor')
    result = run_collection(collection)
    assert result['status'] == 'partial'
    assert collection[2].request.call_count <= 3
    assert sum(len(value) for value in result['evidence'].values()) <= collector.MAX_EVIDENCE_CHARS

@pytest.mark.parametrize('failure', ['expired', 'target_changed', 'cancelled'])
def test_collector_live_guard_retains_safe_page(
    collection: tuple[SimpleNamespace, Mock, Mock], failure: str,
) -> None:
    from src.services.diagnostic_job_guard import DiagnosticCancelled
    grant, vault, remote = collection
    remote.request.return_value = document(items=[pod()], continuation='cursor')
    interrupted = collector.diagnostic_grants.DiagnosticAuthorizationError(failure)
    checks = Mock(side_effect=[grant] * 6 + [interrupted])
    def guard() -> None:
        if failure == 'cancelled' and checks.call_count >= 6:
            raise DiagnosticCancelled('Diagnostic cancellation requested')
    expected = DiagnosticCancelled if failure == 'cancelled' else alert_diagnostics.DiagnosticAuthorizationRequired
    with patch.object(collector.diagnostic_grants, 'require_grant', checks):
        with patch.object(collector, '_credentials', return_value=('private-token', 'ca')):
            with patch.object(collector, 'KubernetesTransport', return_value=remote):
                with pytest.raises(expected) as error:
                    collector.collect(SimpleNamespace(host='192.0.2.20'), vault, Mock(), guard, 7)
    assert error.value.revision == 7
    saved = json.loads(error.value.evidence['kubernetes.pods:production'])
    assert len(saved['items']) == 1 and remote.request.call_count == 1
    assert 'raw-secret' not in json.dumps(error.value.evidence) and 'private-token' not in str(error.value.evidence)

def test_empty_invalid_or_transport_failure_is_failed(
    collection: tuple[SimpleNamespace, Mock, Mock],
) -> None:
    collection[2].request.side_effect = transport.KubernetesTransportError('tls_failed')
    result = run_collection(collection)
    assert result['status'] == 'failed'
    assert 'tls_failed' in result['evidence']['action_errors']
    assert result['error_code'] == 'tls_failed'

def test_native_typed_items_and_event_unknown_involved_object() -> None:
    native = pod()
    del native['kind']
    rows, _ = projection.project_list(document(items=[native]), 'pods', 'production')
    assert rows[0]['kind'] == 'Pod'
    event = {'metadata': {'name': 'event', 'namespace': 'production'}, 'type': 'Warning',
             'reason': 'FailedMount', 'message': 'raw-secret', 'count': 2,
             'involvedObject': {'kind': 'Secret', 'name': 'raw-secret', 'namespace': 'other'}}
    rows, _ = projection.project_list(document('events', [event]), 'events', 'production')
    assert rows[0]['reason'] == 'FailedMount'
    assert 'involved_object' not in rows[0] and 'raw-secret' not in json.dumps(rows)

def test_total_bytes_stop_and_close(context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response(document())
    with patch.object(transport.ssl, 'create_default_context', return_value=context):
        with patch.object(transport.http.client, 'HTTPSConnection', return_value=connection):
            client = transport.KubernetesTransport('https://192.0.2.20:6443', 'ca', 'token',
                                                   transport.time.monotonic() + 40, Mock())
            client.bytes_read = transport.MAX_TOTAL_BYTES
            with pytest.raises(transport.KubernetesTransportError, match='total_byte_limit'):
                client.request('production', 'pods')
    connection.close.assert_called_once()

def test_expired_deadline_never_opens_connection(context: Mock) -> None:
    with patch.object(transport.ssl, 'create_default_context', return_value=context):
        client = transport.KubernetesTransport('https://192.0.2.20:6443', 'ca', 'token',
                                               transport.time.monotonic() - 1, Mock())
    with patch.object(transport.http.client, 'HTTPSConnection') as connect:
        with pytest.raises(transport.KubernetesTransportError, match='timeout'):
            client.request('production', 'pods')
    connect.assert_not_called()

@pytest.mark.parametrize('field,value', [
    ('phase', {}), ('conditions', [{'type': {}, 'status': []}]),
    ('containerStatuses', [{'ready': True, 'restartCount': -1}]),
])
def test_malformed_status_types_are_fixed_error(field: str, value: object) -> None:
    item = pod()
    item['status'][field] = value
    with pytest.raises(transport.KubernetesTransportError, match='invalid_response'):
        projection.project_list(document(items=[item]), 'pods', 'production')

@pytest.mark.parametrize('known_status', ['False', {}, 'invalid'])
def test_projection_custom_readiness_gate_does_not_drop_pod(known_status: object) -> None:
    item = pod()
    item['status']['conditions'] = [
        {'type': 'Ready', 'status': known_status},
        {'type': 'example.invalid/custom-gate', 'status': {}, 'message': 'raw-secret', 'reason': 'raw-secret'}]
    if known_status != 'False':
        with pytest.raises(transport.KubernetesTransportError, match='invalid_response'):
            projection.project_list(document(items=[item]), 'pods', 'production')
        return
    rows, _ = projection.project_list(document(items=[item]), 'pods', 'production')
    assert rows[0]['conditions'] == [{'type': 'Ready', 'status': 'False'}]
    assert rows[0]['name'] == 'demo' and 'raw-secret' not in json.dumps(rows)

def test_projection_depth_limit() -> None:
    nested: dict[str, object] = {}
    for _ in range(18):
        nested = {'ignored': nested}
    item = pod()
    item['spec'] = nested
    with pytest.raises(transport.KubernetesTransportError, match='invalid_response'):
        projection.project_list(document(items=[item]), 'pods', 'production')

@pytest.mark.parametrize('detached', [False, True])
def test_watchdog_shuts_down_retained_socket_without_leaking_errors(detached: bool,
                                                                   caplog: pytest.LogCaptureFixture) -> None:
    active = Mock()
    active.shutdown.side_effect = OSError('sensitive-token')
    connection = Mock(sock=None if detached else active)
    transport._abort(connection, [active])
    active.shutdown.assert_called_once_with(transport.socket.SHUT_RDWR)
    connection.close.assert_called_once()
    assert 'Kubernetes deadline socket shutdown failed' in caplog.text
    assert 'sensitive-token' not in caplog.text
