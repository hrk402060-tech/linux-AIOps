from datetime import datetime, timezone
import math
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.cmdb import CmdbApplication, CmdbBusiness, CmdbService, CmdbServiceApi, CmdbServiceAsset
from src.models.monitoring import MonitoringSnapshot
from src.models.probes import HttpProbe, HttpProbeRun
from src.models.recovery import RecoveryVerification
from src.services import probes

METRICS = {'availability': '在线状态', 'cpu_percent': 'CPU 使用率', 'memory_percent': '内存使用率',
           'swap_percent': 'Swap 使用率', 'disk_percent': '磁盘使用率', 'inode_percent': 'inode 使用率',
           'load_1m': '1 分钟负载'}
RULE_VERSION = 'recovery-evidence-2026.10.10'
SAFE_PROBE_ERRORS = {'timeout', 'tls_error', 'connection_error', 'http_status_mismatch'}
SAFE_ERROR_MESSAGE = '监控采集异常，原始错误请在受控日志中核查。'


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def project_evidence(records: object) -> list[dict[str, object]]:
    if not isinstance(records, list):
        return []
    projected: list[dict[str, object]] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        entry: dict[str, object] = {}
        for key, limit in (('source', 64), ('id', 64), ('at', 64), ('status', 32), ('metric', 32)):
            value = raw.get(key)
            if isinstance(value, str):
                entry[key] = value[:limit]
        for key in ('value', 'threshold'):
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                entry[key] = value
        http_status = raw.get('http_status')
        if isinstance(http_status, int) and not isinstance(http_status, bool) and 100 <= http_status <= 599:
            entry['http_status'] = http_status
        elif 'http_status' in raw and http_status is None:
            entry['http_status'] = None
        latency = raw.get('latency_ms')
        if isinstance(latency, int) and not isinstance(latency, bool) and 0 <= latency <= 3_600_000:
            entry['latency_ms'] = latency
        code = raw.get('error_code')
        if 'error_code' in raw:
            entry['error_code'] = (None if code is None else code
                if isinstance(code, str) and code in SAFE_PROBE_ERRORS else 'unknown_error')
        if isinstance(raw.get('error_message'), str):
            entry['error_message'] = SAFE_ERROR_MESSAGE
        try:
            entry['trace_id'] = str(UUID(str(raw.get('trace_id'))))
        except ValueError:
            entry.pop('trace_id', None)
        projected.append(entry)
    return projected


def configuration(session: Session, asset: Asset, probe_id: str | None) -> dict[str, object]:
    asset = session.scalar(select(Asset).where(Asset.id == asset.id).with_for_update().execution_options(populate_existing=True))
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise ValueError('资产必须处于启用状态')
    result: dict[str, object] = {'host': asset.host, 'ssh_port': asset.ssh_port, 'rule_version': RULE_VERSION}
    if probe_id:
        probe = session.scalar(select(HttpProbe).where(HttpProbe.id == probe_id).with_for_update().execution_options(populate_existing=True))
        if probe is None or probe.status != 'active' or probe.asset_id != asset.id or probe.method not in {'GET', 'HEAD'}:
            raise ValueError('拨测必须已启用且属于告警资产，仅允许 GET/HEAD')
        business, application, service, _ = probes._context(session, probe.service_id, asset.id, probe.api_id)
        for model, identifier in ((CmdbBusiness, business.id), (CmdbApplication, application.id), (CmdbService, service.id), (CmdbServiceApi, probe.api_id)):
            if identifier:
                session.scalar(select(model).where(model.id == identifier).with_for_update().execution_options(populate_existing=True))
        session.scalars(select(CmdbServiceAsset).where(CmdbServiceAsset.service_id == service.id,
            CmdbServiceAsset.asset_id == asset.id).with_for_update().execution_options(populate_existing=True)).all()
        business, application, service, _ = probes._context(session, probe.service_id, asset.id, probe.api_id)
        result.update(probe={key: getattr(probe, key) for key in (
            'name', 'url', 'method', 'expected_status', 'timeout_seconds')},
            service_id=service.id, application_id=application.id, business_id=business.id, api_id=probe.api_id)
    return result


def options(session: Session, asset: Asset | None) -> dict[str, object]:
    rows = [] if asset is None else session.scalars(select(HttpProbe).where(
        HttpProbe.asset_id == asset.id, HttpProbe.status == 'active', HttpProbe.method.in_(('GET', 'HEAD'))
    ).order_by(HttpProbe.name, HttpProbe.id).limit(100)).all()
    return {'asset_enabled': bool(asset and asset.lifecycle_status == AssetLifecycleStatus.ENABLED),
            'metrics': [{'value': key, 'label': label} for key, label in METRICS.items()],
            'probes': [{'id': row.id, 'name': row.name} for row in rows],
            'collection_mode': 'existing_evidence_only'}


def _source(item: RecoveryVerification) -> tuple[object, object, object]:
    if item.kind == 'monitor':
        return MonitoringSnapshot, MonitoringSnapshot.asset_id == item.asset_id, MonitoringSnapshot.collected_at
    return HttpProbeRun, HttpProbeRun.probe_id == item.probe_id, HttpProbeRun.checked_at


def _monitor(row: MonitoringSnapshot, item: RecoveryVerification) -> tuple[dict[str, object], str]:
    entry: dict[str, object] = {'source': 'monitoring_snapshot', 'id': row.id,
        'at': aware(row.collected_at).isoformat(), 'status': row.status, 'metric': item.metric}
    if row.error_message:
        entry['error_message'] = SAFE_ERROR_MESSAGE
    if not row.read_only or row.status not in {'online', 'offline'}:
        return entry, 'invalid_evidence'
    if row.status == 'offline':
        return entry, 'check_failed'
    if item.metric == 'availability':
        return entry, 'healthy'
    value = row.metrics.get(item.metric) if isinstance(row.metrics, dict) else None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return entry, 'invalid_evidence'
    if value < 0 or (item.metric != 'load_1m' and value > 100):
        return entry, 'invalid_evidence'
    if item.metric in {'disk_percent', 'inode_percent'} and not _valid_disks(row.metrics, item.metric, value):
        return entry, 'invalid_evidence'
    entry.update(value=value, threshold=item.threshold)
    return entry, 'healthy' if value <= item.threshold else 'check_failed'


def _valid_disks(metrics: dict[str, object], metric: str, aggregate: float) -> bool:
    disks = metrics.get('disks')
    if not isinstance(disks, list) or not disks or len(disks) > 200:
        return False
    field = 'used_percent' if metric == 'disk_percent' else 'inode_percent'
    values = []
    for disk in disks:
        if not isinstance(disk, dict) or not isinstance(disk.get('mount'), str) or not disk['mount']:
            return False
        if metric == 'inode_percent' and disk.get('inode_observed') is not True:
            return False
        value = disk.get(field)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100:
            return False
        values.append(value)
    return math.isclose(max(values), aggregate, rel_tol=0, abs_tol=0.01)


def _probe_details(row: HttpProbeRun) -> dict[str, object]:
    result: dict[str, object] = {
        'error_code': row.error_code if row.error_code in SAFE_PROBE_ERRORS else 'unknown_error' if row.error_code else None}
    if isinstance(row.latency_ms, int) and not isinstance(row.latency_ms, bool) and row.latency_ms >= 0:
        result['latency_ms'] = row.latency_ms
    try:
        result['trace_id'] = str(UUID(str(row.trace_id)))
    except ValueError:
        return result
    return result


def _probe(row: HttpProbeRun, item: RecoveryVerification) -> tuple[dict[str, object], str]:
    entry: dict[str, object] = {'source': 'http_probe_run', 'id': row.id,
        'at': aware(row.checked_at).isoformat(), 'status': row.status, 'http_status': row.http_status,
        **_probe_details(row)}
    snapshot = item.config_snapshot
    if row.asset_id != item.asset_id or any(getattr(row, key) != snapshot[key] for key in (
        'service_id', 'application_id', 'business_id', 'api_id')) or row.config_snapshot != snapshot['probe']:
        return entry, 'configuration_changed'
    if row.status not in {'healthy', 'unhealthy', 'error'}:
        return entry, 'invalid_evidence'
    return entry, 'healthy' if row.status == 'healthy' and not row.error_code and row.http_status == snapshot['probe']['expected_status'] else 'check_failed'


def inspect_evidence(session: Session, item: RecoveryVerification, now: datetime) -> tuple[str, str, list[dict[str, object]]]:
    asset = session.get(Asset, item.asset_id)
    try:
        current = configuration(session, asset, item.probe_id) if asset else None
    except ValueError:
        return 'blocked', 'target_unavailable', []
    snapshot = {key: value for key, value in item.config_snapshot.items()
                if key != 'alert_cycle_id'}
    if current != snapshot:
        return 'blocked', 'configuration_changed', []
    model, match, timestamp = _source(item)
    if session.scalar(select(model.id).where(match, timestamp > now).limit(1)):
        return 'blocked', 'future_evidence', []
    rows = session.scalars(select(model).where(match, timestamp > item.started_at, timestamp <= now)
                           .order_by(timestamp, model.id).limit(501)).all()
    if len(rows) > 500:
        return 'blocked', 'sample_limit_exceeded', []
    evidence: list[dict[str, object]] = []
    results: list[str] = []
    for row in rows:
        entry, result = _monitor(row, item) if item.kind == 'monitor' else _probe(row, item)
        evidence.append(entry)
        results.append(result)
    if 'check_failed' in results:
        return 'failed', 'check_failed', evidence
    if any(result != 'healthy' for result in results):
        return 'blocked', next(result for result in results if result != 'healthy'), evidence
    times = [aware(row.collected_at if item.kind == 'monitor' else row.checked_at) for row in rows]
    if len(set(times)) != len(times):
        return 'blocked', 'duplicate_sample_time', evidence
    if now < aware(item.deadline_at):
        return 'observing', 'observation_pending', evidence
    if len(times) < 3:
        return 'blocked', 'insufficient_samples', evidence
    boundaries = [aware(item.started_at), *times, now]
    if any((right - left).total_seconds() > item.max_gap_seconds for left, right in zip(boundaries, boundaries[1:])):
        return 'blocked', 'sample_gap_or_stale', evidence
    return 'verified', 'check_passed', evidence


SUMMARIES = {'alert_reopened': '告警复发，上一轮恢复验证已封存；本轮需要重新验证。',
    'check_passed': '所选检查在本次观察窗口通过，不代表整个服务恢复。',
    'check_failed': '观察到异常样本，所选检查未恢复；需人工继续排查。',
    'observation_pending': '观察窗口尚未结束，等待连续的新鲜样本。',
    'target_unavailable': '资产或拨测已停用、关联失效，无法验证。',
    'configuration_changed': '资产或拨测配置发生变化，请重新登记验证。',
    'future_evidence': '存在未来时间样本，不能作为恢复证据。',
    'sample_limit_exceeded': '证据超过500条上限，未截断判定；请缩小窗口重新验证。',
    'invalid_evidence': '证据缺少有效字段或包含非法数值，无法验证。',
    'duplicate_sample_time': '证据存在重复采样时间，无法证明连续恢复。',
    'insufficient_samples': '观察窗口内不足三个有效时间点，无法验证。',
    'sample_gap_or_stale': '样本未覆盖观察窗口、间隔过大或已陈旧，无法验证。',
    'cancelled': '人工取消验证；历史评估和证据保留。'}
