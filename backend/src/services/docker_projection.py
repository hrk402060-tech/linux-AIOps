from datetime import datetime
import re
from typing import NoReturn

from src.services.docker_transport import DockerTransportError, CONTAINER_ID

MAX_PROJECTED = 10
STATE_VALUES = {'created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'}
NAME = re.compile(r'^/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$')


def invalid() -> NoReturn:
    raise DockerTransportError('invalid_response')


def project_containers(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list) or len(raw) > 50:
        invalid()
    output: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            invalid()
        container_id = item.get('Id')
        if not isinstance(container_id, str) or not CONTAINER_ID.fullmatch(container_id) or container_id in seen:
            invalid()
        seen.add(container_id)
        names = item.get('Names', [])
        if not isinstance(names, list) or len(names) > 10 or any(
            not isinstance(name, str) or not NAME.fullmatch(name) for name in names):
            invalid()
        state = item.get('State')
        if not isinstance(state, str) or state not in STATE_VALUES:
            invalid()
        row: dict[str, object] = {'id': container_id, 'names': names[:10], 'state': state}
        created = item.get('Created')
        if created is not None:
            if type(created) is not int or not 0 <= created <= 4102444800:
                invalid()
            row['created_at_epoch'] = created
        output.append(row)
    return sorted(output, key=lambda row: str(row['id']))


def _counter(value: object) -> int | None:
    if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
        invalid()
    return value


def _sample_time(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        invalid()
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        invalid()
    if timestamp.tzinfo is None:
        invalid()
    return timestamp.isoformat()


def _network(raw: object) -> dict[str, int | None]:
    if not isinstance(raw, dict):
        invalid()
    if len(raw) > 32:
        raise DockerTransportError('item_limit')
    counters: dict[str, list[int | None]] = {'rx_bytes': [], 'tx_bytes': []}
    for item in raw.values():
        if not isinstance(item, dict):
            invalid()
        for key in counters:
            counters[key].append(_counter(item.get(key)))
    return {key: _counter(sum(values)) if values and all(value is not None for value in values)
            else None for key, values in counters.items()}


def project_stats(raw: object, container_id: str | None = None) -> dict[str, object]:
    if not isinstance(raw, dict):
        invalid()
    memory = raw.get('memory_stats', {})
    cpu = raw.get('cpu_stats', {})
    if not isinstance(memory, dict) or not isinstance(cpu, dict):
        invalid()
    if (container_id is not None and raw.get('id') != container_id
            or 'id' in raw and (not isinstance(raw['id'], str) or not CONTAINER_ID.fullmatch(raw['id']))):
        invalid()
    row: dict[str, object] = {'memory_usage': _counter(memory.get('usage')),
                              'memory_limit': _counter(memory.get('limit')),
                              'sample_time': _sample_time(raw.get('read')), 'cpu_percent': None}
    cpu_usage = cpu.get('cpu_usage', {})
    if not isinstance(cpu_usage, dict):
        invalid()
    for key in ('cpu_usage', 'system_cpu_usage', 'online_cpus'):
        value = cpu.get(key) if key != 'cpu_usage' else cpu_usage.get('total_usage')
        row[key] = _counter(value)
    pids = raw.get('pids_stats', {})
    if not isinstance(pids, dict):
        invalid()
    row['pids'] = _counter(pids.get('current'))
    row['network_bytes'] = _network(raw.get('networks', {}))
    row['metrics_available'] = any(row[key] is not None for key in ('memory_usage', 'cpu_usage', 'pids'))
    return row
