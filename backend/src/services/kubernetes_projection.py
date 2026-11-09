import re
from datetime import datetime
from typing import NoReturn

from src.services.kubernetes_transport import KubernetesTransportError, RESOURCE_KINDS

NAME = re.compile(r'^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$')
TIMESTAMP = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$')
REASONS = frozenset({
    'BackOff', 'CrashLoopBackOff', 'ImagePullBackOff', 'ErrImagePull', 'Failed',
    'FailedScheduling', 'FailedMount', 'FailedAttachVolume', 'FailedCreate',
    'FailedGetResourceMetric', 'FailedComputeMetricsReplicas', 'FailedScale',
    'FailedValidation', 'FailedDelete', 'SuccessfulCreate', 'SuccessfulDelete',
    'ScalingReplicaSet', 'NewReplicaSetAvailable', 'ReplicaSetUpdated',
    'MinimumReplicasAvailable', 'MinimumReplicasUnavailable', 'ProgressDeadlineExceeded',
    'ContainersNotReady', 'PodCompleted', 'PodFailed', 'PodInitializing',
    'Unschedulable', 'SchedulingGated', 'NodeNotReady', 'Evicted', 'OOMKilled',
    'Completed', 'Error', 'Running', 'Started', 'Created', 'Pulled', 'Pulling',
    'Scheduled', 'Killing', 'Unhealthy', 'Ready', 'NotReady', 'Unknown',
})
CONDITIONS = frozenset({'Ready', 'Initialized', 'ContainersReady', 'PodScheduled',
                       'Available', 'Progressing', 'ReplicaFailure', 'DisruptionTarget',
                       'PodReadyToStartContainers'})


def _invalid() -> NoReturn:
    raise KubernetesTransportError('invalid_response')


def _bounded_shape(value: object) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > 20000 or depth > 16:
            _invalid()
        if isinstance(current, dict):
            if len(current) > 256:
                _invalid()
            for key, child in current.items():
                if not isinstance(key, str) or len(key) > 256:
                    _invalid()
                pending.append((child, depth + 1))
        elif isinstance(current, list):
            if len(current) > 1000:
                _invalid()
            pending.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            if len(current) > 65536:
                _invalid()
        elif current is not None and type(current) not in {bool, int, float}:
            _invalid()


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _invalid()
    return value


def _name(value: object) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        _invalid()
    return value


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        _invalid()
    return value


def _time(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40 or not TIMESTAMP.fullmatch(value):
        _invalid()
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        _invalid()
    return value


def _reason(value: object) -> str:
    return value if isinstance(value, str) and value in REASONS else 'Other'


def _conditions(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 32:
        _invalid()
    rows = []
    for raw in value:
        item = _mapping(raw)
        condition_type, status = item.get('type'), item.get('status')
        if not isinstance(condition_type, str):
            _invalid()
        if condition_type not in CONDITIONS:
            continue
        if not isinstance(status, str) or status not in {'True', 'False', 'Unknown'}:
            _invalid()
        row = {'type': condition_type, 'status': status}
        if 'reason' in item:
            row['reason'] = _reason(item['reason'])
        rows.append(row)
    return rows


def _pod(status: dict[str, object], output: dict[str, object]) -> None:
    phase = status.get('phase', 'Unknown')
    if not isinstance(phase, str) or phase not in {'Pending', 'Running', 'Succeeded', 'Failed', 'Unknown'}:
        _invalid()
    output['phase'] = phase
    if 'reason' in status:
        output['reason'] = _reason(status['reason'])
    containers = status.get('containerStatuses', [])
    if not isinstance(containers, list) or len(containers) > 100:
        _invalid()
    ready = restarts = 0
    for raw in containers:
        container = _mapping(raw)
        if type(container.get('ready')) is not bool:
            _invalid()
        ready += int(container['ready'])
        restarts += _count(container.get('restartCount', 0))
    output.update(ready_containers=ready, container_count=len(containers), restarts=restarts)
    if 'conditions' in status:
        output['conditions'] = _conditions(status['conditions'])


def _workload(status: dict[str, object], output: dict[str, object]) -> None:
    for key in ('replicas', 'readyReplicas', 'availableReplicas', 'unavailableReplicas',
                'updatedReplicas', 'currentReplicas', 'desiredNumberScheduled',
                'currentNumberScheduled', 'numberReady', 'numberAvailable',
                'numberUnavailable', 'updatedNumberScheduled'):
        if key in status:
            output[key] = _count(status[key])
    if 'conditions' in status:
        output['conditions'] = _conditions(status['conditions'])


def _event(item: dict[str, object], namespace: str, output: dict[str, object]) -> None:
    event_type = item.get('type', 'Normal')
    if not isinstance(event_type, str) or event_type not in {'Normal', 'Warning'}:
        _invalid()
    output.update(type=event_type, reason=_reason(item.get('reason')), count=_count(item.get('count', 1)))
    for key in ('firstTimestamp', 'lastTimestamp', 'eventTime'):
        stamp = _time(item.get(key))
        if stamp is not None:
            output[key] = stamp
    involved = item.get('involvedObject', {})
    if not isinstance(involved, dict):
        return
    kind, name = involved.get('kind'), involved.get('name')
    if (isinstance(kind, str) and kind in set(RESOURCE_KINDS.values()) | {'ReplicaSet', 'Job', 'CronJob'}
        and involved.get('namespace') == namespace and isinstance(name, str) and NAME.fullmatch(name)):
        output['involved_object'] = {'kind': kind, 'name': name, 'namespace': namespace}


def _project(item: object, kind: str, namespace: str) -> dict[str, object]:
    raw = _mapping(item)
    if raw.get('kind', kind) != kind:
        _invalid()
    metadata = _mapping(raw.get('metadata'))
    if metadata.get('namespace') != namespace:
        _invalid()
    output = {'kind': kind, 'name': _name(metadata.get('name')), 'namespace': namespace}
    stamp = _time(metadata.get('creationTimestamp'))
    if stamp is not None:
        output['created_at'] = stamp
    if kind == 'Event':
        _event(raw, namespace, output)
    elif kind == 'Pod':
        _pod(_mapping(raw.get('status', {})), output)
    else:
        _workload(_mapping(raw.get('status', {})), output)
    return output


def project_list(document: dict[str, object], resource: str, namespace: str
                 ) -> tuple[list[dict[str, object]], str]:
    _bounded_shape(document)
    kind = RESOURCE_KINDS.get(resource)
    if kind is None or document.get('kind') != kind + 'List':
        _invalid()
    items = document.get('items')
    metadata = _mapping(document.get('metadata', {}))
    continuation = metadata.get('continue', '')
    if not isinstance(items, list) or len(items) > 50:
        _invalid()
    if not isinstance(continuation, str) or len(continuation) > 2048:
        _invalid()
    return [_project(item, kind, namespace) for item in items], continuation
